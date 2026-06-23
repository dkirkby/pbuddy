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
# Helpers
# ---------------------------------------------------------------------------

def _fmt_yt_timestamp(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS for YouTube chapter descriptions."""
    total_s = int(seconds)
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


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
# Track trail overlay
# ---------------------------------------------------------------------------

def _render_trail_overlay(
    frame_bgr: np.ndarray,
    source_frame_number: int,
    fps: float,
    tracks: list[dict],
    trail_s: float = 1.0,
) -> np.ndarray | None:
    """Render a yellow trailing trail for all tracks overlapping the trailing window.

    The window covers the *trail_s* seconds (default 1.0 s, i.e. N = round(fps)
    frames) leading up to *source_frame_number* inclusive: [F-N+1 … F].
    N-1 straight lines connect consecutive smooth positions looked up directly
    from the track's smooth array (one entry per frame from smooth_first_frame).
    Opacity runs linearly from 5 % (oldest line) to 80 % (newest line);
    stroke width runs linearly from 1 px to 6 px over the same range.
    Lines are rendered with anti-aliasing (cv2.LINE_AA).

    Returns a BGRA uint8 array the same size as *frame_bgr*, or None if there
    is nothing to draw.
    """
    if not tracks:
        return None

    h, w = frame_bgr.shape[:2]
    N = max(2, round(trail_s * fps))
    win_start = source_frame_number - N + 1

    overlay = np.zeros((h, w, 4), dtype=np.uint8)
    has_any = False

    for track in tracks:
        first_frame = track["first_frame"]
        last_frame  = track["last_frame"]
        if last_frame < win_start or first_frame > source_frame_number:
            continue

        smooth      = track["smooth"]           # list of [cx, cy], one per frame
        base_frame  = track["smooth_first_frame"]  # OpenCV frame of smooth[0]
        n_smooth    = len(smooth)

        for i in range(N - 1):
            f0 = win_start + i
            f1 = f0 + 1
            idx0 = f0 - base_frame
            idx1 = f1 - base_frame
            if not (0 <= idx0 < n_smooth and 0 <= idx1 < n_smooth):
                continue
            p0 = smooth[idx0]
            p1 = smooth[idx1]
            t_frac = i / (N - 2) if N > 2 else 1.0
            opacity = 0.05 + 0.75 * t_frac
            line_w = max(1, round(1 + 5 * t_frac))
            alpha = int(opacity * 255)
            pt0 = (int(round(p0[0])), int(round(p0[1])))
            pt1 = (int(round(p1[0])), int(round(p1[1])))
            # Yellow in BGR = (0, 255, 255); BGRA channel order for cv2.line.
            cv2.line(overlay, pt0, pt1, (0, 255, 255, alpha), line_w, cv2.LINE_AA)
            has_any = True

    return overlay if has_any else None


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
    receiver_first: str | None = None,
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
    # Initial team membership from player_names.
    # Support both old keys (serving_team_*) and new keys (far_team_* / near_team_*).
    far_first = player_names.get("far_team_serves_first", True)
    if far_first:
        sv_a = player_names.get("far_team_left",  player_names.get("serving_team_left",  "?"))
        sv_b = player_names.get("far_team_right", player_names.get("serving_team_right", "?"))
        rv_a = player_names.get("near_team_right", player_names.get("receiving_team_right", "?"))
        rv_b = player_names.get("near_team_left",  player_names.get("receiving_team_left",  "?"))
    else:
        sv_a = player_names.get("near_team_left",  player_names.get("receiving_team_left",  "?"))
        sv_b = player_names.get("near_team_right", player_names.get("receiving_team_right", "?"))
        rv_a = player_names.get("far_team_right", player_names.get("serving_team_right", "?"))
        rv_b = player_names.get("far_team_left",  player_names.get("serving_team_left",  "?"))
    initial_sv_set = frozenset({sv_a, sv_b})

    current_server = rally.get("serverName", "")
    # receiver_first pins the receiving team's first-listed player across a
    # service run so the order only changes at side-out boundaries.
    current_receiver = receiver_first if receiver_first is not None else rally.get("receiverName", "")

    score_parts = rally.get("score", "0-0-0").split("-")
    raw_a, raw_b = score_parts[0], score_parts[1]
    server_num = int(score_parts[2]) if score_parts[2].isdigit() else 2

    # Identify each player's team and teammate.
    if current_server in initial_sv_set:
        server_team = initial_sv_set
        receiver_team = frozenset({rv_a, rv_b})
        top_score, bot_score = raw_a, raw_b   # initial serving team still serving
    else:
        server_team = frozenset({rv_a, rv_b})
        receiver_team = initial_sv_set
        top_score, bot_score = raw_b, raw_a   # initial serving team now receiving

    server_mate = next(iter(server_team - {current_server})) if current_server in server_team else "?"
    receiver_mate = next(iter(receiver_team - {current_receiver})) if current_receiver in receiver_team else "?"

    # Serving team: server 1 listed first (determined by server_num in score).
    if server_num == 1:
        sv_s1, sv_s2 = current_server, server_mate
    else:
        sv_s1, sv_s2 = server_mate, current_server

    # Receiving team: current right-side player (receiverName) is listed first.
    rv_s1, rv_s2 = current_receiver, receiver_mate

    # Assign to fixed rows: top = initial serving team, bottom = initial receiving team.
    if current_server in initial_sv_set:
        top_s1, top_s2 = sv_s1, sv_s2
        bot_s1, bot_s2 = rv_s1, rv_s2
    else:
        top_s1, top_s2 = rv_s1, rv_s2
        bot_s1, bot_s2 = sv_s1, sv_s2

    # ---- Score ----
    # (top_score, bot_score already set above)

    # ---- Layout (scales with frame height; 1.5× base size) ----
    scale = h / 720.0
    row_h = max(39, round(45 * scale))
    font_sz = max(17, round(20 * scale))
    pad_x = max(9, round(12 * scale))
    margin = 0   # flush with video bounds

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
            _tw(sv_a) + sep_w + _tw(sv_b),
            _tw(rv_a) + sep_w + _tw(rv_b),
        )
        + 2 * pad_x
    )
    score_col_w = max(_tw("00") + 2 * pad_x, round(36 * scale))
    total_w = name_col_w + score_col_w
    total_h = 2 * row_h

    # ---- PIL image ----
    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    NAME_BG = (0, 0, 0, 220)         # black
    SCORE_BG = (0, 100, 45, 225)     # dark green
    WHITE = (255, 255, 255, 255)

    for row in range(2):
        y0, y1 = row * row_h, (row + 1) * row_h
        draw.rectangle([0, y0, name_col_w - 1, y1 - 1], fill=NAME_BG)
        draw.rectangle([name_col_w, y0, total_w - 1, y1 - 1], fill=SCORE_BG)

    # Subtle row separator inside score column only (avoids visible grid lines
    # on the name side, preserving the "no grid lines" requirement).
    draw.line([name_col_w, row_h, total_w - 1, row_h], fill=(0, 60, 25, 180), width=1)

    def _draw_stroked(x: int, ty: int, text: str, underline: bool = False) -> int:
        """Draw *text* at (x, ty); return rendered width."""
        bb = draw.textbbox((x, ty), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        draw.text((x, ty), text, font=font, fill=WHITE)
        if underline:
            uy = ty + th + max(1, round(scale))
            draw.line([x, uy, x + tw, uy], fill=WHITE, width=max(1, round(scale)))
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

    # Row 0 — initial serving team
    ty0 = _text_top(0)
    x = pad_x
    x += _draw_stroked(x, ty0, top_s1, underline=(current_server == top_s1))
    x += _draw_stroked(x, ty0, "/")
    x += _draw_stroked(x, ty0, top_s2, underline=(current_server == top_s2))
    _draw_score(top_score, name_col_w, score_col_w, row_h // 2)

    # Row 1 — initial receiving team
    ty1 = _text_top(1)
    x = pad_x
    x += _draw_stroked(x, ty1, bot_s1, underline=(current_server == bot_s1))
    x += _draw_stroked(x, ty1, "/")
    x += _draw_stroked(x, ty1, bot_s2, underline=(current_server == bot_s2))
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
# Score overlay compositor
# ---------------------------------------------------------------------------

def _apply_score_ov(bgr: np.ndarray, score_ov: tuple | None) -> np.ndarray:
    """Alpha-blend the pre-computed score overlay onto a BGR frame."""
    if score_ov is None:
        return bgr
    ov_bgr, ov_alpha = score_ov
    return (
        bgr.astype(np.float32) * (1.0 - ov_alpha) + ov_bgr * ov_alpha
    ).clip(0, 255).astype(np.uint8)


def _lerp_score_ovs(
    ov_a: tuple | None,
    ov_b: tuple | None,
    t: float,
) -> tuple | None:
    """Linearly interpolate between two score overlays (t=0 → ov_a, t=1 → ov_b)."""
    if ov_a is None:
        return ov_b
    if ov_b is None:
        return ov_a
    bgr_a, alpha_a = ov_a
    bgr_b, alpha_b = ov_b
    return bgr_a * (1.0 - t) + bgr_b * t, alpha_a * (1.0 - t) + alpha_b * t


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

        # Load pass5 accepted tracks for trail overlay (optional).
        tracks_path = ctx.paths.project_root / "passes" / "pass5" / "accepted" / "tracks.json"
        accepted_tracks: list[dict] = []
        if tracks_path.exists():
            accepted_tracks = json.loads(tracks_path.read_text()).get("tracks", [])

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
        # 2. Load median background images
        #    New Pass 1 format: median_background_paths + median_window_times
        #    Old Pass 1 format (pre-rework): fall back to Pass 0 medians
        # ------------------------------------------------------------------
        pass1_raw_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "result.json"
        pass1_raw = json.loads(pass1_raw_path.read_text())
        if "median_background_paths" in pass1_raw:
            median_images, median_window_times = _load_median_images(ctx.paths.project_root, pass1_raw)
        else:
            pass0_medians_dir = ctx.paths.project_root / "passes" / "pass0" / "raw" / "medians"
            median_paths = sorted(pass0_medians_dir.glob("median_*.png"))
            if not median_paths:
                raise FileNotFoundError("No median images found in pass0/raw/medians/")
            median_images = [cv2.imread(str(p)) for p in median_paths]
            n = len(median_images)
            D = ctx.video_duration_s
            median_window_times = [(i * D / n, (i + 1) * D / n) for i in range(n)]

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
                "serverName": r.get("serverName", ""),
                "receiverName": r.get("receiverName", ""),
            })
            cumulative_s += section_duration_s
            total_source_frames += n_frames

        # Extra hold frame appended after the last rally's fade_out (see step 6).
        total_duration_s = cumulative_s + 1.0 / fps

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
        # 6. Pre-compute score overlays for all rallies + final game state
        #
        # score_ovs[i]      — overlay shown during rally i and its fade_in
        # next_score_ovs[i] — overlay shown during rally i's fade_out:
        #                     = score_ovs[i+1] for non-final rallies
        #                     = final_score_ov for the last rally
        #
        # During fade_out the score lerps from score_ovs[i] → next_score_ovs[i]
        # so the score change is visible as the video fades to the median.
        # During fade_in of the next rally, next_score_ovs[i] == score_ovs[i+1]
        # so the updated score is already at full opacity.  After the last
        # rally's fade_out, a single hold frame locks in the final game score.
        # ------------------------------------------------------------------
        # Determine a stable receiver_first for each rally: fixed at each
        # side-out boundary (when the serving team changes) and held constant
        # across all subsequent rallies until the next side out.  This stops
        # the receiving team's display order flipping rally-to-rally due to
        # diagonal-receiver changes while the same team keeps serving.
        _far_first = player_names.get("far_team_serves_first", True)
        if _far_first:
            _initial_sv_set = frozenset({
                player_names.get("far_team_left",  player_names.get("serving_team_left",  "")),
                player_names.get("far_team_right", player_names.get("serving_team_right", "")),
            })
        else:
            _initial_sv_set = frozenset({
                player_names.get("near_team_left",  player_names.get("receiving_team_left",  "")),
                player_names.get("near_team_right", player_names.get("receiving_team_right", "")),
            })
        _stable_rv_first: list[str] = []
        _cur_rv_first = ""
        _prev_serving_initial = None
        for _r in rallies:
            _serving_initial = _r["serverName"] in _initial_sv_set
            if _serving_initial != _prev_serving_initial:
                _cur_rv_first = _r["receiverName"]
                _prev_serving_initial = _serving_initial
            _stable_rv_first.append(_cur_rv_first)

        score_ovs = [
            _build_score_overlay((height, width), r, player_names, overlay_corner,
                                 receiver_first=_stable_rv_first[i])
            for i, r in enumerate(rallies)
        ]

        # Build a synthetic rally dict whose score reflects the outcome of the
        # last rally (serving team +1 if they won, unchanged on side-out).
        _last = rallies[-1]
        _lparts = _last["score"].split("-")
        _la, _lb = int(_lparts[0]), int(_lparts[1])
        if _last.get("servingTeamWinsRally"):
            _la += 1
        _final_rally = {**_last, "score": f"{_la}-{_lb}-{_lparts[2]}"}
        final_score_ov = _build_score_overlay(
            (height, width), _final_rally, player_names, overlay_corner,
            receiver_first=_stable_rv_first[-1],
        )

        next_score_ovs = score_ovs[1:] + [final_score_ov]

        # ------------------------------------------------------------------
        # 7. PyAV encode loop: decode rally frames, composite overlay, encode
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
        out_v.options = {"crf": "18", "preset": "slow"}

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

            score_ov = score_ovs[rally_idx]
            next_score_ov = next_score_ovs[rally_idx]
            is_last_rally = rally_idx == len(rallies) - 1

            # Seek to just before the start of this rally.  PyAV seeks to the
            # nearest keyframe at or before the target PTS.
            seek_pts = int(max(0, (start_frame - 1) / fps) / float(src_v.time_base))
            src.seek(seek_pts, stream=src_v)

            # first_raw / last_raw hold frames *before* the score overlay so
            # that cross-fade blending is done on clean video content.  The
            # score overlay is composited after every blend, keeping the box
            # at full opacity throughout fades.  The score switches at the
            # boundary between fade_out (this rally) and fade_in (next rally),
            # which is the midpoint of the back-to-back cross fade.
            first_raw: np.ndarray | None = None
            last_raw: np.ndarray | None = None

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

                # Extension-point overlay (ball tracking, etc.) — applied
                # before blending so it fades naturally with the video.
                overlay = _render_trail_overlay(bgr, src_frame_num, fps, accepted_tracks)
                if overlay is None:
                    overlay = _render_overlay(bgr, rally_idx, src_frame_num, out_frame_idx, rally)
                if overlay is not None:
                    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
                    bgr = (
                        bgr.astype(np.float32) * (1.0 - alpha)
                        + overlay[:, :, :3].astype(np.float32) * alpha
                    ).clip(0, 255).astype(np.uint8)

                if first_raw is None:
                    # Emit fade_in frames: median → first rally frame.
                    # alpha goes from 1/(N+1) to N/(N+1) so neither endpoint
                    # is duplicated in the output.
                    first_raw = bgr
                    for fi in range(fade_frames):
                        blend = (fi + 1) / (fade_frames + 1)
                        fade_bgr = cv2.addWeighted(
                            median_bgr, 1.0 - blend, first_raw, blend, 0
                        )
                        _encode_bgr(
                            _apply_score_ov(fade_bgr, score_ov),
                            out_frame_idx, out_v, out_container,
                        )
                        out_frame_idx += 1

                # Score overlay applied after blending, at full opacity.
                _encode_bgr(
                    _apply_score_ov(bgr, score_ov),
                    out_frame_idx, out_v, out_container,
                )
                out_frame_idx += 1
                last_raw = bgr
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
            # Score overlay lerps from this rally's score to the updated score
            # (next rally's score, or final game score for the last rally).
            if last_raw is not None:
                for fi in range(fade_frames):
                    blend = (fi + 1) / (fade_frames + 1)
                    fade_bgr = cv2.addWeighted(
                        last_raw, 1.0 - blend, median_bgr, blend, 0
                    )
                    t = fi / (fade_frames - 1) if fade_frames > 1 else 1.0
                    _encode_bgr(
                        _apply_score_ov(fade_bgr, _lerp_score_ovs(score_ov, next_score_ov, t)),
                        out_frame_idx, out_v, out_container,
                    )
                    out_frame_idx += 1

            # After the last rally's fade_out, add a hold frame: the final
            # median image with the final game score at full opacity.
            if is_last_rally:
                _encode_bgr(
                    _apply_score_ov(median_bgr, final_score_ov),
                    out_frame_idx, out_v, out_container,
                )
                out_frame_idx += 1

        # Flush encoder.
        for pkt in out_v.encode():
            out_container.mux(pkt)
        out_container.close()
        src.close()

        # ------------------------------------------------------------------
        # 8. ffmpeg mux: encoded video + spliced audio + chapter markers
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
        # 9. Write result
        # ------------------------------------------------------------------
        progress.update(0.98, "finalize", "Writing result…")
        chapter_timestamps = "\n".join(
            f"{_fmt_yt_timestamp(c['chapter_start_s'])} {c['title']} {c['serverName']} serves to {c['receiverName']}"
            for c in chapter_info
        )
        result = Pass6RawResult(
            rally_count=len(rallies),
            output_duration_s=round(total_duration_s, 3),
            chapter_timestamps=chapter_timestamps,
            rally_chapter_starts=[c["chapter_start_s"] for c in chapter_info],
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
            chapter_timestamps = raw_result.get("chapter_timestamps", "")
            rally_chapter_starts = raw_result.get("rally_chapter_starts", [])
        else:
            rally_count = raw_result.rally_count
            output_duration_s = raw_result.output_duration_s
            chapter_timestamps = raw_result.chapter_timestamps
            rally_chapter_starts = raw_result.rally_chapter_starts

        accepted = Pass6AcceptedOutput(
            rally_count=rally_count,
            output_duration_s=output_duration_s,
            chapter_timestamps=chapter_timestamps,
            rally_chapter_starts=rally_chapter_starts,
        )
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
