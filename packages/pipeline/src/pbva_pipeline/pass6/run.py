"""Pass 6 — Video Export: concatenate rally segments into a highlight reel with chapter markers.

Video frames are decoded and re-encoded via PyAV so that:
  - Cuts are frame-accurate (not keyframe-bounded)
  - Per-frame overlay graphics can be composited in _render_overlay()

Audio (if present) is handled in three steps:
  1. The full source audio track is decoded to an uncompressed PCM WAV file.
  2. Segments are sliced at exactly the same sample positions that correspond
     to the video frame boundaries, giving sample-accurate sync with no gaps.
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

    This is the extension point for future overlay graphics (score display,
    player names, ball tracking, etc.).  Returning None avoids any blending
    cost and is the correct default until overlays are implemented.

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
) -> None:
    """Slice rally segments from src_wav and write a gapless spliced WAV.

    Splice points are computed from the same start_frame/fps times used by
    the PyAV encode loop, so audio and video cuts are sample-aligned.
    The full WAV is loaded into memory as a numpy array; at ~10 MB/min of
    stereo 48 kHz audio this is well within available RAM for any match.
    """
    with wave.open(str(src_wav), "r") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # pcm_s16le guaranteed by _extract_full_audio.
    audio = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_channels)

    segments = []
    for c in chapter_info:
        s0 = round(c["start_frame"] / fps * sample_rate)
        s1 = min(round((c["stop_frame"] + 1) / fps * sample_rate), n_frames)
        segments.append(audio[s0:s1])

    spliced = np.concatenate(segments, axis=0)

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

        # ------------------------------------------------------------------
        # 1. Load rally data + probe source video
        # ------------------------------------------------------------------
        progress.update(0.01, "prepare", "Loading rally data and probing video…")
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        rally_data = json.loads(rally_path.read_text())
        rallies: list[dict] = rally_data.get("rally", [])
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

        # ------------------------------------------------------------------
        # 2. Compute per-rally timing and chapter metadata
        # ------------------------------------------------------------------
        chapter_info = []
        cumulative_s = 0.0
        total_frames = 0
        for r in rallies:
            n_frames = r["stop_frame"] - r["start_frame"] + 1
            duration_s = n_frames / fps
            chapter_info.append({
                "title": r["score"],
                "start_frame": r["start_frame"],
                "stop_frame": r["stop_frame"],
                "chapter_start_s": cumulative_s,
                "duration_s": duration_s,
            })
            cumulative_s += duration_s
            total_frames += n_frames

        total_duration_s = cumulative_s

        # ------------------------------------------------------------------
        # 3. Write chapter metadata for the ffmpeg mux step
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
        # 4. Extract and splice audio (sample-accurate rally cuts)
        # ------------------------------------------------------------------
        spliced_wav_path: Path | None = None
        if has_audio:
            progress.update(0.015, "audio", "Extracting and splicing audio…")
            full_wav_path = raw_dir / "full_audio.wav"
            spliced_wav_path = raw_dir / "spliced_audio.wav"
            _extract_full_audio(ctx.settings.ffmpeg_bin, video_path, full_wav_path)
            _splice_audio(full_wav_path, spliced_wav_path, chapter_info, fps)
            full_wav_path.unlink()

        # ------------------------------------------------------------------
        # 5. PyAV encode loop: decode rally frames, composite overlay, encode
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
        frames_done = 0
        encode_start = time.monotonic()
        last_progress_t = encode_start - 1.0  # ensure first update fires immediately

        for rally_idx, (rally, c) in enumerate(zip(rallies, chapter_info)):
            start_frame = c["start_frame"]
            stop_frame = c["stop_frame"]

            # Seek to just before the start of this rally.  PyAV seeks to the
            # nearest keyframe at or before the target PTS.
            seek_pts = int(max(0, (start_frame - 1) / fps) / float(src_v.time_base))
            src.seek(seek_pts, stream=src_v)

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

                out_frame = av.VideoFrame.from_ndarray(bgr, format="bgr24")
                out_frame = out_frame.reformat(format="yuv420p")
                out_frame.pts = out_frame_idx
                for pkt in out_v.encode(out_frame):
                    out_container.mux(pkt)

                out_frame_idx += 1
                frames_done += 1

                now = time.monotonic()
                if now - last_progress_t >= 1.0:
                    frac = 0.02 + 0.93 * frames_done / total_frames
                    elapsed_s = now - encode_start
                    progress.update(
                        frac,
                        "encode",
                        f"Encoding… {frames_done}/{total_frames} frames ({elapsed_s:.0f}s)",
                    )
                    last_progress_t = now
                    progress.check_cancelled()

        # Flush encoder.
        for pkt in out_v.encode():
            out_container.mux(pkt)
        out_container.close()
        src.close()

        # ------------------------------------------------------------------
        # 6. ffmpeg mux: encoded video + spliced audio + chapter markers
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
        # 7. Write result
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
