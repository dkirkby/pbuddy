#!/usr/bin/env python3
"""
Create a ball detection challenge dataset from a PBuddy project.

Reads annotated ball positions from pass 2 (accepted if available, else
corrections) and writes per annotated frame:
  challenge/data/truth.json              — frame number + bg-plate (x, y)
  challenge/data/images/frame_NNNNN.jpg  — decoded video frame at bg-plate resolution
  challenge/data/images/bsub_NNNNN.jpg   — |frame(n) - median bg| in bg-plate space
  challenge/data/images/prev_NNNNN.jpg   — |frame(n) - frame(n-1)| in bg-plate space
  challenge/data/images/next_NNNNN.jpg   — |frame(n+1) - frame(n)| in bg-plate space

All images are masked to the tent silhouette (pixels outside set to black).
Annotations at the first or last video frame are silently ignored.

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
import numpy as np


# ─── Tent silhouette ──────────────────────────────────────────────────────────

def _build_homography(g: dict) -> np.ndarray:
    """Build the 3×3 homography mapping (u,v,1) → (px,py,w) from court corners."""
    TL, TR = g["top_left"],    g["top_right"]
    BL, BR = g["bottom_left"], g["bottom_right"]

    A = TR["x"] - BR["x"];  B = BL["x"] - BR["x"]
    C = TL["x"] - TR["x"] - BL["x"] + BR["x"]
    D = TR["y"] - BR["y"];  E = BL["y"] - BR["y"]
    F = TL["y"] - TR["y"] - BL["y"] + BR["y"]
    det = A * E - B * D
    gh = (C * E - B * F) / det
    hh = (A * F - C * D) / det

    return np.array([
        [TR["x"] * (gh + 1) - TL["x"],  BL["x"] * (hh + 1) - TL["x"],  TL["x"]],
        [TR["y"] * (gh + 1) - TL["y"],  BL["y"] * (hh + 1) - TL["y"],  TL["y"]],
        [gh,                             hh,                             1      ],
    ])


def compute_tent_mask(court_geometry: dict, dims: dict, bg_w: int, bg_h: int) -> np.ndarray:
    """Return a uint8 mask (bg_h × bg_w) with 255 inside the tent silhouette.

    Mirrors the TypeScript deriveCameraMatrix / computeVolumeOverlay logic in
    apps/frontend/src/lib/courtCamera.ts.
    """
    half_w = dims["court_dimensions"]["total_width"]  / 2   # 3.05 m
    half_l = dims["court_dimensions"]["total_length"] / 2   # 6.705 m
    vol    = dims["valid_ball_volume"]
    ext    = vol["boundary_extension"]
    hw_ext = half_w + ext
    hl_ext = half_l + ext
    ch     = vol["corner_height"]
    nh     = vol["net_height"]

    # 10 tent vertices in physical (X, Y, Z) metres.
    vertices = [
        (-hw_ext, -hl_ext, 0 ),  # 0 base corners
        ( hw_ext, -hl_ext, 0 ),  # 1
        ( hw_ext,  hl_ext, 0 ),  # 2
        (-hw_ext,  hl_ext, 0 ),  # 3
        (-hw_ext, -hl_ext, ch),  # 4 top corners
        ( hw_ext, -hl_ext, ch),  # 5
        ( hw_ext,  hl_ext, ch),  # 6
        (-hw_ext,  hl_ext, ch),  # 7
        (-hw_ext,  0,      nh),  # 8 tent peaks at net
        ( hw_ext,  0,      nh),  # 9
    ]

    # H maps (u,v,1) → (px,py,w); M maps (X,Y,1) → (u,v,1).
    H = _build_homography(court_geometry)
    M = np.array([
        [1 / (2 * half_w), 0,               0.5],
        [0,                1 / (2 * half_l), 0.5],
        [0,                0,               1  ],
    ])
    Hphys = H @ M   # maps (X,Y,1) → (px,py,w)

    # Shift principal point to origin to isolate focal length.
    cx, cy = bg_w / 2.0, bg_h / 2.0
    Hc = np.array([
        [Hphys[0, 0] - cx * Hphys[2, 0],  Hphys[0, 1] - cx * Hphys[2, 1],  Hphys[0, 2] - cx * Hphys[2, 2]],
        [Hphys[1, 0] - cy * Hphys[2, 0],  Hphys[1, 1] - cy * Hphys[2, 1],  Hphys[1, 2] - cy * Hphys[2, 2]],
        [Hphys[2, 0],                      Hphys[2, 1],                      Hphys[2, 2]                    ],
    ])
    h1, h2 = Hc[:, 0], Hc[:, 1]

    # Focal length from orthonormality constraint r1·r2 = 0.
    num, denom = h1[0] * h2[0] + h1[1] * h2[1], h1[2] * h2[2]
    if abs(denom) < 1e-12 or num / denom > 0:
        f = np.sqrt(abs(num / denom) or 1) * (bg_w + bg_h) / 4
    else:
        f = np.sqrt(-num / denom)

    # Camera intrinsics and extrinsics.
    K     = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
    K_inv = np.array([[1/f, 0, -cx/f], [0, 1/f, -cy/f], [0, 0, 1]])

    r1_raw = K_inv @ Hphys[:, 0]
    r2_raw = K_inv @ Hphys[:, 1]
    t_raw  = K_inv @ Hphys[:, 2]

    lam = float(np.linalg.norm(r1_raw))
    r1  = r1_raw / lam
    r2  = r2_raw / lam
    r3  = np.cross(r1, r2)
    t   = t_raw  / lam

    # Projection matrix P = K [r1 r2 r3 | t] (3×4).
    Rt = np.column_stack([r1, r2, r3, t])
    P  = K @ Rt

    # Project vertices; negate Z (r3 derived from ground plane points in −Z direction).
    projected = []
    for X, Y, Z in vertices:
        uvw = P @ np.array([X, Y, -Z, 1.0])
        if uvw[2] > 0:
            projected.append([uvw[0] / uvw[2], uvw[1] / uvw[2]])

    if len(projected) < 3:
        return np.full((bg_h, bg_w), 255, dtype=np.uint8)

    pts  = np.array(projected, dtype=np.float32)
    hull = cv2.convexHull(pts)   # ordered convex polygon

    mask = np.zeros((bg_h, bg_w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, hull.astype(np.int32), 255)
    return mask


# ─── Main ─────────────────────────────────────────────────────────────────────

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
    db_path   = data_root / "pbuddy.db"

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
        print(f"Error: no project named '{args.project_name}' found in {db_path}", file=sys.stderr)
        sys.exit(1)

    project_id = row["id"]
    root_path  = Path(row["root_path"])
    print(f"Project: {args.project_name} ({project_id})")

    # Load annotations — prefer accepted, fall back to corrections.
    accepted_path    = root_path / "passes" / "pass2" / "accepted"    / "annotations.json"
    corrections_path = root_path / "passes" / "pass2" / "corrections" / "annotations.json"

    if accepted_path.exists():
        ann_path, source = accepted_path, "accepted"
    elif corrections_path.exists():
        ann_path, source = corrections_path, "corrections"
    else:
        print(f"Error: no pass2 annotations found for project '{args.project_name}'", file=sys.stderr)
        sys.exit(1)

    print(f"Annotations source: {source} ({ann_path})")

    with open(ann_path) as f:
        data = json.load(f)

    frames_to_annotate: dict[int, dict] = {int(k): v for k, v in data.get("annotations", {}).items()}
    annotation_frames  = set(frames_to_annotate)
    sorted_annotations = sorted(annotation_frames)

    print(f"Annotations found: {len(sorted_annotations)}")
    if not sorted_annotations:
        print("Nothing to do.")
        sys.exit(0)

    # Load median background plate.
    # FIXME: Pass 1 writes median_background_0.png (indexed), not median_background.png.
    # This path should be read from pass1/raw/result.json (median_background_paths[0]).
    bg_path = root_path / "passes" / "pass1" / "raw" / "median_background.png"
    if not bg_path.exists():
        print(f"Error: median background not found at {bg_path}", file=sys.stderr)
        sys.exit(1)

    bg    = cv2.imread(str(bg_path))
    bg_h, bg_w = bg.shape[:2]
    bg_i16 = bg.astype(np.int16)
    print(f"Background plate: {bg_w}x{bg_h}")

    # Load court geometry from pass1 accepted result.
    p1_accepted = root_path / "passes" / "pass1" / "accepted" / "result.json"
    if not p1_accepted.exists():
        print(f"Error: pass1 accepted result not found at {p1_accepted}", file=sys.stderr)
        sys.exit(1)

    with open(p1_accepted) as f:
        p1_result = json.load(f)
    court_geometry = p1_result["court_geometry"]

    # Load sport dimensions.
    dims_path = Path(__file__).parent.parent / "dimensions.json"
    with open(dims_path) as f:
        dims = json.load(f)

    # Compute tent silhouette mask (same for every frame).
    tent_mask = compute_tent_mask(court_geometry, dims, bg_w, bg_h)
    print(f"Tent mask: {int(tent_mask.sum() / 255)} / {bg_w * bg_h} pixels inside silhouette")

    # Locate video.
    video_path = root_path / "uploads" / "original.mp4"
    if not video_path.exists():
        print(f"Error: video not found at {video_path}", file=sys.stderr)
        sys.exit(1)

    # We need frames n-1, n, n+1 for each annotation n.
    needed_set: set[int] = set()
    for n in annotation_frames:
        needed_set.update([n - 1, n, n + 1])
    needed_set = {f for f in needed_set if f >= 0}

    # Sequential pass — collect all needed frames at bg-plate resolution.
    bg_cache: dict[int, np.ndarray] = {}
    remaining = set(needed_set)

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        fps    = float(stream.average_rate)
        stream.codec_context.skip_frame = "DEFAULT"

        for packet in container.demux(stream):
            if not remaining:
                break
            for frame in packet.decode():
                pts = frame.pts if frame.pts is not None else frame.dts
                if pts is None:
                    continue
                fi = round(float(pts * stream.time_base) * fps)

                if fi not in remaining:
                    continue

                arr = frame.to_ndarray(format="bgr24")
                bg_cache[fi] = cv2.resize(arr, (bg_w, bg_h))
                remaining.discard(fi)

    if remaining:
        print(
            f"Warning: {len(remaining)} needed frame(s) not found in video: {sorted(remaining)}",
            file=sys.stderr,
        )

    # Set up output paths.
    output_dir = Path("challenge/data")
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    def masked(img: np.ndarray) -> np.ndarray:
        """Zero out pixels outside the tent silhouette."""
        out = img.copy()
        out[tent_mask == 0] = 0
        return out

    JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 85]

    # Write images and collect valid annotations.
    valid_annotations: list[int] = []

    for n in sorted_annotations:
        if n - 1 not in bg_cache or n not in bg_cache or n + 1 not in bg_cache:
            continue  # first/last frame of video, or frame not found

        prev_i16 = bg_cache[n - 1].astype(np.int16)
        curr_i16 = bg_cache[n    ].astype(np.int16)
        next_i16 = bg_cache[n + 1].astype(np.int16)

        cv2.imwrite(str(images_dir / f"frame_{n:05d}.jpg"),
                    masked(bg_cache[n]), JPEG_PARAMS)
        cv2.imwrite(str(images_dir / f"bsub_{n:05d}.jpg"),
                    masked(np.abs(curr_i16 - bg_i16 ).astype(np.uint8)), JPEG_PARAMS)
        cv2.imwrite(str(images_dir / f"prev_{n:05d}.jpg"),
                    masked(np.abs(curr_i16 - prev_i16).astype(np.uint8)), JPEG_PARAMS)
        cv2.imwrite(str(images_dir / f"next_{n:05d}.jpg"),
                    masked(np.abs(next_i16 - curr_i16).astype(np.uint8)), JPEG_PARAMS)

        valid_annotations.append(n)

    skipped = len(sorted_annotations) - len(valid_annotations)
    if skipped:
        print(f"Skipped {skipped} annotation(s) at video boundaries.")

    # Write truth.json.
    truth = [
        {"frame": n, "x": frames_to_annotate[n]["x"], "y": frames_to_annotate[n]["y"]}
        for n in valid_annotations
    ]

    truth_path = output_dir / "truth.json"
    with open(truth_path, "w") as f:
        json.dump(truth, f, indent=2)

    print(f"Wrote {len(truth)} entries to {truth_path}")
    print(f"Wrote {len(valid_annotations) * 4} images to {images_dir}/")


if __name__ == "__main__":
    main()
