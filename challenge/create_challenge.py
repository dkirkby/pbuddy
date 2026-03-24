#!/usr/bin/env python3
"""
Create a ball detection challenge dataset from a PBuddy project.

Reads annotated ball positions from pass 2 (accepted if available, else
corrections) and writes:
  challenge/data/truth.json         — frame number + bg-plate (x, y) per annotation
  challenge/data/images/frame_NNNNN.jpg — decoded video frame for each annotation

Usage:
    uv run challenge/create_challenge.py <project-name> [--data-root PATH]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import av  # type: ignore
import cv2  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create ball detection challenge dataset from a PBuddy project"
    )
    parser.add_argument("project_name", help="Project name as shown in the PBuddy UI")
    parser.add_argument(
        "--data-root", default="data", help="Path to data directory (default: data)"
    )
    args = parser.parse_args()

    data_root = Path(args.data_root)
    db_path = data_root / "pbuddy.db"

    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # Look up project by name.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, name, root_path FROM projects WHERE name = ?",
        (args.project_name,),
    ).fetchone()
    conn.close()

    if row is None:
        print(
            f"Error: no project named '{args.project_name}' found in {db_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    project_id = row["id"]
    root_path = Path(row["root_path"])
    print(f"Project: {args.project_name} ({project_id})")

    # Load annotations — prefer accepted, fall back to corrections.
    accepted_path = root_path / "passes" / "pass2" / "accepted" / "annotations.json"
    corrections_path = root_path / "passes" / "pass2" / "corrections" / "annotations.json"

    if accepted_path.exists():
        ann_path = accepted_path
        source = "accepted"
    elif corrections_path.exists():
        ann_path = corrections_path
        source = "corrections"
    else:
        print(
            f"Error: no pass2 annotations found for project '{args.project_name}'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Annotations source: {source} ({ann_path})")

    with open(ann_path) as f:
        data = json.load(f)

    # Keys are frame numbers stored as strings.
    raw_annotations: dict[str, dict] = data.get("annotations", {})
    frames_to_annotate: dict[int, dict] = {int(k): v for k, v in raw_annotations.items()}
    sorted_frames = sorted(frames_to_annotate)

    print(f"Annotations found: {len(sorted_frames)}")
    if not sorted_frames:
        print("Nothing to do.")
        sys.exit(0)

    # Set up output paths.
    output_dir = Path("challenge/data")
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Locate video.
    video_path = root_path / "uploads" / "original.mp4"
    if not video_path.exists():
        print(f"Error: video not found at {video_path}", file=sys.stderr)
        sys.exit(1)

    # Single sequential pass through the video to extract annotated frames.
    remaining = set(sorted_frames)
    extracted: set[int] = set()

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate)
        stream.codec_context.skip_frame = "DEFAULT"

        for packet in container.demux(stream):
            if not remaining:
                break
            for frame in packet.decode():
                pts = frame.pts if frame.pts is not None else frame.dts
                if pts is None:
                    continue
                frame_index = round(float(pts * stream.time_base) * fps)

                if frame_index not in remaining:
                    continue

                img = frame.to_ndarray(format="bgr24")
                out_path = images_dir / f"frame_{frame_index:05d}.jpg"
                cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])

                remaining.discard(frame_index)
                extracted.add(frame_index)

    if remaining:
        print(
            f"Warning: {len(remaining)} frame(s) not found in video: {sorted(remaining)}",
            file=sys.stderr,
        )

    # Write truth.json — only for successfully extracted frames, sorted by frame.
    truth = [
        {
            "frame": f,
            "x": frames_to_annotate[f]["x"],
            "y": frames_to_annotate[f]["y"],
        }
        for f in sorted_frames
        if f in extracted
    ]

    truth_path = output_dir / "truth.json"
    with open(truth_path, "w") as f:
        json.dump(truth, f, indent=2)

    print(f"Wrote {len(truth)} entries to {truth_path}")
    print(f"Extracted {len(extracted)} images to {images_dir}/")


if __name__ == "__main__":
    main()
