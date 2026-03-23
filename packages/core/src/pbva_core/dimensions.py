"""
Single authoritative loader for repo-root dimensions.json.

Import named constants from here rather than hardcoding pickleball
physical dimensions anywhere in the Python codebase.

Usage::

    from pbva_core.dimensions import COURT_TOTAL_LENGTH, NET_POST_HEIGHT
"""
from __future__ import annotations

import json
from pathlib import Path

_DIMENSIONS_PATH = Path(__file__).parents[4] / "dimensions.json"
_d = json.loads(_DIMENSIONS_PATH.read_text())

# ── Court dimensions ──────────────────────────────────────────────────────────
COURT_TOTAL_LENGTH:        float = _d["court_dimensions"]["total_length"]          # 13.41 m
COURT_TOTAL_WIDTH:         float = _d["court_dimensions"]["total_width"]           # 6.10 m
COURT_NON_VOLLEY_DEPTH:    float = _d["court_dimensions"]["non_volley_zone_depth"] # 2.13 m
COURT_SERVICE_AREA_LENGTH: float = _d["court_dimensions"]["service_area_length"]   # 4.57 m
COURT_SERVICE_AREA_WIDTH:  float = _d["court_dimensions"]["service_area_width"]    # 3.05 m
COURT_LINE_THICKNESS:      float = _d["court_dimensions"]["line_thickness"]        # 0.05 m

# ── Net specifications ────────────────────────────────────────────────────────
NET_POST_HEIGHT:   float = _d["net_specifications"]["post_height"]        # 0.91 m
NET_CENTER_HEIGHT: float = _d["net_specifications"]["center_height_dip"]  # 0.86 m
NET_POST_TO_POST:  float = _d["net_specifications"]["post_to_post_width"]  # 6.71 m

# ── Ball specifications ───────────────────────────────────────────────────────
BALL_DIAMETER_MIN: float = _d["ball_specifications"]["diameter_range"]["min"]  # 73 mm
BALL_DIAMETER_MAX: float = _d["ball_specifications"]["diameter_range"]["max"]  # 75 mm
BALL_WEIGHT_MIN:   float = _d["ball_specifications"]["weight_range"]["min"]    # 22.1 g
BALL_WEIGHT_MAX:   float = _d["ball_specifications"]["weight_range"]["max"]    # 26.5 g
BALL_PATCH_RADIUS: int   = _d["ball_specifications"]["patch_radius_px"]        # 32 px

# ── Valid-ball volume ─────────────────────────────────────────────────────────
VOLUME_BOUNDARY_EXTENSION: float = _d["valid_ball_volume"]["boundary_extension"]  # 0.5 m
VOLUME_CORNER_HEIGHT:      float = _d["valid_ball_volume"]["corner_height"]       # 1.0 m
VOLUME_NET_HEIGHT:         float = _d["valid_ball_volume"]["net_height"]          # 3.0 m

# ── Derived constants ─────────────────────────────────────────────────────────
# Normalised v-coordinate of the kitchen line (0 = baseline, 0.5 = net).
COURT_KV: float = (COURT_TOTAL_LENGTH / 2 - COURT_NON_VOLLEY_DEPTH) / COURT_TOTAL_LENGTH
