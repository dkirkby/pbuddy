"""Pass 6 — Video Export: concatenate rally segments into a highlight reel with chapter markers.

Video frames are decoded and re-encoded via PyAV so that:
  - Cuts are frame-accurate (not keyframe-bounded)
  - Per-frame overlay graphics can be composited in _render_overlay()

Cross-fades between rallies and the median background image are synthesised
during the PyAV encode loop.  Each rally is wrapped with:
  - fade_in  (fade_time s): median → static first rally frame
  - rally frames
  - fade_out (fade_time s): static last rally frame → median

Back-to-back rallies produce consecutive fade_out / fade_in transitions with
no hold on the median image.  A single fade_in precedes the first rally and
a single fade_out follows the last rally.  The closest Pass 1 median image
(by window midpoint time) is used for each rally's transitions.

Audio (if present) is handled in three steps:
  1. The full source audio track is decoded to an uncompressed PCM WAV file.
  2. Segments are sliced at exactly the same sample positions that correspond
     to the video frame boundaries, giving sample-accurate sync with no gaps.
     Each rally segment has audio_fade_time s of fade-in/out applied, and is
     bracketed by silence that covers the cross-fade video frames.
  3. The spliced WAV is AAC-encoded and muxed into the final MP4 alongside
     the encoded video and chapter markers.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import wave
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

from pbva_core.types import Pass6AcceptedOutput, Pass6RawResult
from pbva_pipeline.base import PassContext


# ---------------------------------------------------------------------------
# Overlay hook
# ---------------------------------------------------------------------------

def _render_overlay(
    frame_bgr: np.ndarray,
    rally_idx: int,
    source_frame_number: int,
    output_frame_number: int,
    rally: dict,
) -> np.ndarray | None:
    """Return a BGRA overlay (same HxW, uint8) to alpha-blend onto frame_bgr, or None for no-op.

    Extension point for additional per-frame overlay graphics (ball tracking,
    etc.).  The score/name overlay is handled separately by
    _build_score_overlay and pre-computed once per rally.  Returning None
    avoids any blending cost.

    Args:
        frame_bgr: source video frame as HxW×3 uint8 BGR numpy array.
        rally_idx: 0-based index of the current rally.
        source_frame_number: OpenCV frame number in the original video.
        output_frame_number: 0-based frame index in the output highlight reel.
        rally: the rally dict from rally.json (keys: score, start_frame,
            stop_frame, serverName, receiverName, servingTeamWinsRally).
    """
    return None


# ---------------------------------------------------------------------------
# Score / name overlay
# ---------------------------------------------------------------------------

# Candidate font paths tried in order; first hit wins.
_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(size: int):
    """Return a PIL FreeTypeFont at *size* pt, falling back to the bitmap default."""
    from PIL import ImageFont
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _build_score_overlay(
    frame_shape: tuple[int, int],
    rally: dict,
    player_names: dict,
    corner: str = "upper_right",
) -> tuple[np.ndarray, np.ndarray] | None:
    """Pre-compute the score/name overlay for one rally.

    Returns (overlay_bgr_f32, alpha_f32) both shaped (H, W, 3) and (H, W, 1),
    ready for the blending expression:
        out = frame_bgr * (1 - alpha) + overlay_bgr * alpha

    The overlay is constant for every frame in the rally (score and server
    do not change within a rally), so calling this once per rally and reusing
    the result avoids redundant PIL rendering.

    corner: "upper_right" | "upper_left" | "lower_right" | "lower_left"

    Returns None when player_names is empty (no data to display).

    Layout (2 × 2 grid, no visible grid lines):
        ┌──────────────────┬────────┐
        │ serving team     │  a     │  ← team that served first in the game
        ├──────────────────┼────────┤
        │ receiving team   │  b     │
        └──────────────────┴────────┘
    where score "a-b-s" with serving_score=a / receiving_score=b when the
    initial serving team is currently serving, swapped otherwise.

    Name format:
        serving team  → "first_server/second_server"
                         (serving_team_left = server 1 at n-m-1,
                          serving_team_right = server 2 at n-m-2)
        receiving team → "right_player/left_player"

    The current server (rally["serverName"]) is underlined.

    Background colours: white (semi-transparent) for the name column,
    dark green for the score column.  All text is white; a thin dark stroke
    is applied on the name column for legibility against the light background.
    """
    if not player_names:
        return None

    from PIL import Image, ImageDraw

    h, w = frame_shape

    # ---- Players ----
    sv_first = player_names.get("serving_team_left", "?")    # server 1 (n-m-1)
    sv_second = player_names.get("serving_team_right", "?")  # server 2 (n-m-2)
    rv_right = player_names.get("receiving_team_right", "?")
    rv_left = player_names.get("receiving_team_left", "?")
    current_server = rally.get("serverName", "")

    # Which players are on the initial serving team?
    initial_serving = {sv_first, sv_second}

    # ---- Score ----
    score_parts = rally.get("score", "0-0-0").split("-")
    raw_a, raw_b = score_parts[0], score_parts[1]
    if current_server in initial_serving:
        top_score, bot_score = raw_a, raw_b   # initial-serving team is still serving
    else:
        top_score, bot_score = raw_b, raw_a   # initial-serving team is now receiving

    # ---- Layout (scales with frame height) ----
    scale = h / 720.0
    row_h = max(26, round(30 * scale))
    font_sz = max(11, round(13 * scale))
    pad_x = max(6, round(8 * scale))
    margin = max(8, round(10 * scale))
    stroke_w = max(1, round(scale))

    font = _load_font(font_sz)

    # Measure text widths on a throw-away image.
    _dummy = Image.new("RGBA", (1, 1))
    _dd = ImageDraw.Draw(_dummy)

    def _tw(text: str) -> int:
        bb = _dd.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]

    def _th(text: str) -> int:
        bb = _dd.textbbox((0, 0), text, font=font)
        return bb[3] - bb[1]

    sep_w = _tw("/")
    name_col_w = (
        max(
            _tw(sv_first) + sep_w + _tw(sv_second),
            _tw(rv_right) + sep_w + _tw(rv_left),
        )
        + 2 * pad_x
    )
    score_col_w = max(_tw("00") + 2 * pad_x, round(36 * scale))
    total_w = name_col_w + score_col_w
    total_h = 2 * row_h

    # ---- PIL image ----
    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    NAME_BG = (255, 255, 255, 160)   # semi-transparent white
    SCORE_BG = (0, 100, 45, 225)     # dark green
    WHITE = (255, 255, 255, 255)
    STROKE = (0, 0, 0, 110)          # thin dark halo for white-on-white legibility

    for row in range(2):
        y0, y1 = row * row_h, (row + 1) * row_h
        draw.rectangle([0, y0, name_col_w - 1, y1 - 1], fill=NAME_BG)
        draw.rectangle([name_col_w, y0, total_w - 1, y1 - 1], fill=SCORE_BG)

    # Subtle row separator inside score column only (avoids visible grid lines
    # on the name side, preserving the "no grid lines" requirement).
    draw.line([name_col_w, row_h, total_w - 1, row_h], fill=(0, 60, 25, 180), width=1)

    def _draw_stroked(x: int, ty: int, text: str, underline: bool = False) -> int:
        """Draw *text* at (x, ty) with a stroke halo; return rendered width."""
        bb = draw.textbbox((x, ty), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        # Stroke (drawn first, slightly offset in 4 directions)
        for dx, dy in ((-stroke_w, 0), (stroke_w, 0), (0, -stroke_w), (0, stroke_w)):
            draw.text((x + dx, ty + dy), text, font=font, fill=STROKE)
        draw.text((x, ty), text, font=font, fill=WHITE)
        if underline:
            uy = ty + th + max(1, round(scale))
            draw.line([x, uy, x + tw, uy], fill=WHITE, width=stroke_w)
        return tw

    def _draw_score(text: str, cell_x: int, cell_w: int, y_mid: int) -> None:
        bb = _dd.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = cell_x + (cell_w - tw) // 2
        ty = y_mid - th // 2
        draw.text((x, ty), text, font=font, fill=WHITE)

    def _text_top(row: int) -> int:
        """Vertically centred top-of-text y for the given row."""
        row_mid = row * row_h + row_h // 2
        th = _th("Ay")   # representative ascender height
        return row_mid - th // 2

    # Row 0 — serving team
    ty0 = _text_top(0)
    x = pad_x
    x += _draw_stroked(x, ty0, sv_first,  underline=(current_server == sv_first))
    x += _draw_stroked(x, ty0, "/")
    x += _draw_stroked(x, ty0, sv_second, underline=(current_server == sv_second))
    _draw_score(top_score, name_col_w, score_col_w, row_h // 2)

    # Row 1 — receiving team
    ty1 = _text_top(1)
    x = pad_x
    x += _draw_stroked(x, ty1, rv_right, underline=(current_server == rv_right))
    x += _draw_stroked(x, ty1, "/")
    x += _draw_stroked(x, ty1, rv_left,  underline=(current_server == rv_left))
    _draw_score(bot_score, name_col_w, score_col_w, row_h + row_h // 2)

    # ---- Position in full frame ----
    ox, oy = total_w, total_h
    if corner == "upper_right":
        r0, c0 = margin, w - ox - margin
    elif corner == "upper_left":
        r0, c0 = margin, margin
    elif corner == "lower_right":
        r0, c0 = h - oy - margin, w - ox - margin
    else:  # lower_left
        r0, c0 = h - oy - margin, margin

    # ---- Convert PIL RGBA → full-frame BGRA float32 arrays ----
    rgba = np.array(img, dtype=np.float32)           # (total_h, total_w, 4)
    bgr_patch = rgba[:, :, [2, 1, 0]]               # BGR
    alpha_patch = rgba[:, :, 3:4] / 255.0

    ov_bgr = np.zeros((h, w, 3), dtype=np.float32)
    ov_alpha = np.zeros((h, w, 1), dtype=np.float32)
    ov_bgr[r0:r0 + oy, c0:c0 + ox] = bgr_patch
    ov_alpha[r0:r0 + oy, c0:c0 + ox] = alpha_patch

    return ov_bgr, ov_alpha


# ---------------------------------------------------------------------------
# Median image helpers
# ---------------------------------------------------------------------------

def _load_median_images(
    project_root: Path,
    pass1_raw: dict,
) -> tuple[list[np.ndarray], list[tuple[float, float]]]:
    """Load all Pass 1 median background images and their time windows.

    Returns (images, window_times) where window_times[i] = (start_s, end_s).
    """
    images = []
    for rel_path in pass1_raw["median_background_paths"]:
        img = cv2.imread(str(project_root / rel_path))
        if img is None:
            raise FileNotFoundError(f"Median background not found: {rel_path}")
        images.append(img)
    window_times: list[tuple[float, float]] = [
        (ws, we) for ws, we in pass1_raw["median_window_times"]
    ]
    return images, window_times


def _closest_median(
    t_s: float,
    median_images: list[np.ndarray],
    window_times: list[tuple[float, float]],
    width: int,
    height: int,
) -> np.ndarray:
    """Return the median image whose window midpoint is closest to t_s.

    Resizes to (width, height) if the stored image differs in size.
    """
    best_idx = 0
    best_dist = float("inf")
    for i, (ws, we) in enumerate(window_times):
        dist = abs(t_s - (ws + we) / 2)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    img = median_images[best_idx]
    if img.shape[0] != height or img.shape[1] != width:
        img = cv2.resize(img, (width, height))
    return img


# ---------------------------------------------------------------------------
# Encode helper
# ---------------------------------------------------------------------------

def _encode_bgr(
    bgr: np.ndarray,
    pts: int,
    stream: av.VideoStream,
    container: av.container.OutputContainer,
) -> None:
    """Reformat a BGR frame to yuv420p and mux it."""
    out_frame = av.VideoFrame.from_ndarray(bgr, format="bgr24")
    out_frame = out_frame.reformat(format="yuv420p")
    out_frame.pts = pts
    for pkt in stream.encode(out_frame):
        container.mux(pkt)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _extract_full_audio(ffmpeg_bin: str, video_path: Path, out_wav: Path) -> None:
    """Decode the entire source audio track to a 16-bit PCM WAV file.

    Forces pcm_s16le so that numpy int16 slicing is straightforward.
    The original sample rate is preserved.
    """
    out_wav.unlink(missing_ok=True)
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            str(out_wav),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {result.stderr[-300:]}")


def _splice_audio(
    src_wav: Path,
    out_wav: Path,
    chapter_info: list[dict],
    fps: float,
    fade_frames: int,
    audio_fade_time: float,
) -> None:
    """Slice rally segments from src_wav and write a gapless spliced WAV.

    Each rally segment is:
      - preceded by silence covering the fade_in video frames
      - audio-faded in over audio_fade_time seconds at the start
      - audio-faded out over audio_fade_time seconds at the end
      - followed by silence covering the fade_out video frames

    Splice points are computed from the same start_frame/fps times used by
    the PyAV encode loop, so audio and video cuts are sample-aligned.
    """
    with wave.open(str(src_wav), "r") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # pcm_s16le guaranteed by _extract_full_audio.
    audio = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_channels)

    n_silence = round(fade_frames / fps * sample_rate)
    n_audio_fade = round(audio_fade_time * sample_rate)
    silence = np.zeros((n_silence, n_channels), dtype=np.int16)

    pieces: list[np.ndarray] = []
    for c in chapter_info:
        s0 = round(c["start_frame"] / fps * sample_rate)
        s1 = min(round((c["stop_frame"] + 1) / fps * sample_rate), n_frames)
        seg = audio[s0:s1].copy().astype(np.float32)

        # Apply audio fade-in; cap at half the segment to avoid overlap.
        if n_audio_fade > 0 and len(seg) > 0:
            fade_in_len = min(n_audio_fade, len(seg) // 2)
            seg[:fade_in_len] *= np.linspace(0.0, 1.0, fade_in_len)[:, np.newaxis]

        # Apply audio fade-out; same cap.
        if n_audio_fade > 0 and len(seg) > 0:
            fade_out_len = min(n_audio_fade, len(seg) // 2)
            seg[-fade_out_len:] *= np.linspace(1.0, 0.0, fade_out_len)[:, np.newaxis]

        seg_int16 = seg.clip(-32768, 32767).astype(np.int16)
        pieces.extend([silence, seg_int16, silence])

    spliced = np.concatenate(pieces, axis=0)

    with wave.open(str(out_wav), "w") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(2)          # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(spliced.tobytes())


# ---------------------------------------------------------------------------
# Pass 6 implementation
# ---------------------------------------------------------------------------

class Pass6:
    name = "pass6"

    def validate_inputs(self, ctx: PassContext) -> None:
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        if not rally_path.exists():
            raise FileNotFoundError("Pass 2 accepted rally.json not found — accept Pass 2 first")

    def run(self, ctx: PassContext, progress=None) -> Pass6RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        # Pass 6 parameters
        fade_time: float = 0.5        # seconds, cross-fade between median and rally content
        audio_fade_time: float = 0.25  # seconds, audio fade-in/out within each rally
        overlay_corner: str = "upper_right"  # score overlay position

        # ------------------------------------------------------------------
        # 1. Load rally data + probe source video
        # ------------------------------------------------------------------
        progress.update(0.01, "prepare", "Loading rally data and probing video…")
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        rally_data = json.loads(rally_path.read_text())
        rallies: list[dict] = rally_data.get("rally", [])
        player_names: dict = rally_data.get("player_names", {})
        if not rallies:
            raise ValueError("No rallies found in pass2/accepted/rally.json")

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        video_path = ctx.video_path.resolve()

        src = av.open(str(video_path))
        v_stream = src.streams.video[0]
        fps_frac: Fraction = v_stream.average_rate
        fps = float(fps_frac)
        width = v_stream.width
        height = v_stream.height
        has_audio = len(src.streams.audio) > 0
        src.close()

        fade_frames = max(1, round(fade_time * fps))
        fade_s = fade_frames / fps

        # ------------------------------------------------------------------
        # 2. Load Pass 1 median background images
        # ------------------------------------------------------------------
        pass1_raw_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "result.json"
        pass1_raw = json.loads(pass1_raw_path.read_text())
        median_images, median_window_times = _load_median_images(ctx.paths.project_root, pass1_raw)

        # ------------------------------------------------------------------
        # 3. Compute per-rally timing and chapter metadata
        #    Each output section = fade_in (fade_s) + rally + fade_out (fade_s)
        # ------------------------------------------------------------------
        chapter_info = []
        cumulative_s = 0.0
        total_source_frames = 0
        for r in rallies:
            n_frames = r["stop_frame"] - r["start_frame"] + 1
            rally_duration_s = n_frames / fps
            section_duration_s = fade_s + rally_duration_s + fade_s
            chapter_info.append({
                "title": r["score"],
                "start_frame": r["start_frame"],
                "stop_frame": r["stop_frame"],
                "chapter_start_s": cumulative_s,
                "duration_s": section_duration_s,
            })
            cumulative_s += section_duration_s
            total_source_frames += n_frames

        total_duration_s = cumulative_s

        # ------------------------------------------------------------------
        # 4. Write chapter metadata for the ffmpeg mux step
        # ------------------------------------------------------------------
        meta_path = raw_dir / "chapters.ffmeta"
        meta_lines = [";FFMETADATA1", ""]
        for c in chapter_info:
            start_ms = int(c["chapter_start_s"] * 1000)
            end_ms = int((c["chapter_start_s"] + c["duration_s"]) * 1000)
            meta_lines += [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={c['title']}",
                "",
            ]
        meta_path.write_text("\n".join(meta_lines))

        # ------------------------------------------------------------------
        # 5. Extract and splice audio (sample-accurate rally cuts + fades)
        # ------------------------------------------------------------------
        spliced_wav_path: Path | None = None
        if has_audio:
            progress.update(0.015, "audio", "Extracting and splicing audio…")
            full_wav_path = raw_dir / "full_audio.wav"
            spliced_wav_path = raw_dir / "spliced_audio.wav"
            _extract_full_audio(ctx.settings.ffmpeg_bin, video_path, full_wav_path)
            _splice_audio(
                full_wav_path,
                spliced_wav_path,
                chapter_info,
                fps,
                fade_frames,
                audio_fade_time,
            )
            full_wav_path.unlink()

        # ------------------------------------------------------------------
        # 6. PyAV encode loop: decode rally frames, composite overlay, encode
        #    Each rally is wrapped with fade_in / fade_out cross-fade frames.
        #
        # Progress spans 0.02 → 0.95 so the slow encode dominates the bar.
        # Updates are time-gated to ~1 s intervals, matching the WebSocket
        # poll period in main.py so every DB write reaches the browser.
        # ------------------------------------------------------------------
        video_only_path = raw_dir / "video_only.mp4"
        video_only_path.unlink(missing_ok=True)

        progress.update(0.02, "encode", "Encoding video frames…")

        out_container = av.open(str(video_only_path), mode="w")
        out_v = out_container.add_stream("libx264", rate=fps_frac)
        out_v.width = width
        out_v.height = height
        out_v.pix_fmt = "yuv420p"
        out_v.options = {"crf": "18", "preset": "fast"}

        src = av.open(str(video_path))
        src_v = src.streams.video[0]

        out_frame_idx = 0
        source_frames_done = 0
        encode_start = time.monotonic()
        last_progress_t = encode_start - 1.0  # ensure first update fires immediately

        for rally_idx, (rally, c) in enumerate(zip(rallies, chapter_info)):
            start_frame = c["start_frame"]
            stop_frame = c["stop_frame"]

            # Select the median image whose window midpoint is closest to
            # the midpoint of this rally.
            rally_mid_s = (start_frame + stop_frame) / 2 / fps
            median_bgr = _closest_median(
                rally_mid_s, median_images, median_window_times, width, height
            )

            # Pre-compute the score overlay for this rally.  Score and server
            # are constant across all frames in a rally, so rendering once
            # and reusing avoids per-frame PIL overhead.
            score_ov = _build_score_overlay(
                (height, width), rally, player_names, overlay_corner
            )

            # Seek to just before the start of this rally.  PyAV seeks to the
            # nearest keyframe at or before the target PTS.
            seek_pts = int(max(0, (start_frame - 1) / fps) / float(src_v.time_base))
            src.seek(seek_pts, stream=src_v)

            first_bgr: np.ndarray | None = None
            last_bgr: np.ndarray | None = None

            for frame in src.decode(video=0):
                # Map PyAV PTS → OpenCV frame number (per CLAUDE.md: PTS = (N+1)/fps)
                pts_s = float(frame.pts * src_v.time_base)
                src_frame_num = round(pts_s * fps) - 1

                if src_frame_num < start_frame:
                    continue
                if src_frame_num > stop_frame:
                    break

                # Convert to BGR numpy array for overlay compositing.
                bgr = frame.to_ndarray(format="bgr24")

                # Extension-point overlay (ball tracking, etc.).
                overlay = _render_overlay(
                    bgr, rally_idx, src_frame_num, out_frame_idx, rally
                )
                if overlay is not None:
                    # Alpha-blend BGRA overlay onto BGR frame.
                    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
                    bgr = (
                        bgr.astype(np.float32) * (1.0 - alpha)
                        + overlay[:, :, :3].astype(np.float32) * alpha
                    ).clip(0, 255).astype(np.uint8)

                # Score/name overlay (pre-computed once per rally).
                if score_ov is not None:
                    ov_bgr, ov_alpha = score_ov
                    bgr = (
                        bgr.astype(np.float32) * (1.0 - ov_alpha) + ov_bgr * ov_alpha
                    ).clip(0, 255).astype(np.uint8)

                if first_bgr is None:
                    # Emit fade_in frames: median → first rally frame.
                    # alpha goes from 1/(N+1) to N/(N+1) so neither endpoint
                    # is duplicated in the output.
                    first_bgr = bgr
                    for fi in range(fade_frames):
                        blend = (fi + 1) / (fade_frames + 1)
                        fade_bgr = cv2.addWeighted(
                            median_bgr, 1.0 - blend, first_bgr, blend, 0
                        )
                        _encode_bgr(fade_bgr, out_frame_idx, out_v, out_container)
                        out_frame_idx += 1

                _encode_bgr(bgr, out_frame_idx, out_v, out_container)
                out_frame_idx += 1
                last_bgr = bgr
                source_frames_done += 1

                now = time.monotonic()
                if now - last_progress_t >= 1.0:
                    frac = 0.02 + 0.93 * source_frames_done / total_source_frames
                    elapsed_s = now - encode_start
                    progress.update(
                        frac,
                        "encode",
                        f"Encoding… {source_frames_done}/{total_source_frames} frames ({elapsed_s:.0f}s)",
                    )
                    last_progress_t = now
                    progress.check_cancelled()

            # Emit fade_out frames: last rally frame → median.
            if last_bgr is not None:
                for fi in range(fade_frames):
                    blend = (fi + 1) / (fade_frames + 1)
                    fade_bgr = cv2.addWeighted(
                        last_bgr, 1.0 - blend, median_bgr, blend, 0
                    )
                    _encode_bgr(fade_bgr, out_frame_idx, out_v, out_container)
                    out_frame_idx += 1

        # Flush encoder.
        for pkt in out_v.encode():
            out_container.mux(pkt)
        out_container.close()
        src.close()

        # ------------------------------------------------------------------
        # 7. ffmpeg mux: encoded video + spliced audio + chapter markers
        # ------------------------------------------------------------------
        progress.update(0.96, "mux", "Muxing audio and chapter markers…")

        export_path = raw_dir / "export.mp4"
        export_path.unlink(missing_ok=True)

        if spliced_wav_path is not None:
            cmd = [
                ctx.settings.ffmpeg_bin, "-y",
                "-i", str(video_only_path),
                "-i", str(spliced_wav_path),
                "-f", "ffmetadata", "-i", str(meta_path),
                "-map", "0:v",
                "-map", "1:a",
                "-map_metadata", "2",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-loglevel", "error",
                str(export_path),
            ]
        else:
            cmd = [
                ctx.settings.ffmpeg_bin, "-y",
                "-i", str(video_only_path),
                "-f", "ffmetadata", "-i", str(meta_path),
                "-map", "0:v",
                "-map_metadata", "1",
                "-c:v", "copy",
                "-movflags", "+faststart",
                "-loglevel", "error",
                str(export_path),
            ]

        stderr_lines: list[str] = []
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        def _drain() -> None:
            if proc.stderr:
                stderr_lines.extend(proc.stderr.read().splitlines())

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        proc.wait()
        t.join(timeout=10)

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg mux failed (exit {proc.returncode}): {' '.join(stderr_lines)[:500]}")

        # Clean up intermediates.
        video_only_path.unlink(missing_ok=True)
        if spliced_wav_path is not None:
            spliced_wav_path.unlink(missing_ok=True)

        # ------------------------------------------------------------------
        # 8. Write result
        # ------------------------------------------------------------------
        progress.update(0.98, "finalize", "Writing result…")
        result = Pass6RawResult(
            rally_count=len(rallies),
            output_duration_s=round(total_duration_s, 3),
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "done", f"Exported {len(rallies)} rallies ({total_duration_s:.1f}s)")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass6RawResult) -> list[dict]:
        path = ctx.paths.pass_raw_dir / "export.mp4"
        return [{"role": "raw", "type": "mp4", "path": str(path)}] if path.exists() else []

    def validate_corrections(self, payload: dict) -> dict:
        return payload  # Pass 6 has no user corrections

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass6RawResult | dict,
        corrections: dict | None,
    ) -> Pass6AcceptedOutput:
        import shutil

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)

        raw_mp4 = ctx.paths.pass_raw_dir / "export.mp4"
        if raw_mp4.exists():
            shutil.copy2(raw_mp4, accepted_dir / "export.mp4")

        if isinstance(raw_result, dict):
            rally_count = raw_result.get("rally_count", 0)
            output_duration_s = raw_result.get("output_duration_s", 0.0)
        else:
            rally_count = raw_result.rally_count
            output_duration_s = raw_result.output_duration_s

        accepted = Pass6AcceptedOutput(
            rally_count=rally_count,
            output_duration_s=output_duration_s,
        )
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
