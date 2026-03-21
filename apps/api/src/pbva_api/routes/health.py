"""Health and diagnostics endpoints."""

from __future__ import annotations

import subprocess
import sys

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    return {"ok": True, "status": "healthy"}


@router.get("/diagnostics")
def diagnostics():
    info: dict = {
        "python_version": sys.version,
    }
    try:
        import cv2
        info["opencv_version"] = cv2.__version__
    except ImportError:
        info["opencv_version"] = "not installed"
    try:
        import numpy as np
        info["numpy_version"] = np.__version__
    except ImportError:
        info["numpy_version"] = "not installed"
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        first_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        info["ffmpeg_version"] = first_line
    except Exception as e:
        info["ffmpeg_version"] = f"error: {e}"
    return {"ok": True, "data": info}
