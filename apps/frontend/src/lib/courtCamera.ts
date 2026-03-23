/**
 * courtCamera.ts — Derive a pinhole camera model from the Pass-1 court homography
 * and use it to project the valid-ball 3-D volume into screen coordinates.
 *
 * Coordinate systems
 * ──────────────────
 *  Normalised court   u ∈ [0,1] left→right, v ∈ [0,1] top→bottom
 *  Physical 3-D       X (m) along width, Y (m) along length, Z (m) up; origin at court centre
 *  Image              pixels in the median-background plate (origin top-left)
 *
 * Court physical dimensions (from dimensions.json):
 *   total_width  = 6.10 m  → halfW = 3.05 m
 *   total_length = 13.41 m → halfL = 6.705 m
 *
 * Valid-ball volume (from dimensions.json valid_ball_volume):
 *   boundary_extension = 0.50 m beyond every court edge
 *   corner_height      = 1.00 m at the four extended corners
 *   net_height         = 3.00 m at the two sideline peaks (Y = 0)
 *   Height along each extended sideline rises linearly from corner_height → net_height.
 */

import type { CourtGeometry } from '../types/api'
import {
  COURT_TOTAL_WIDTH, COURT_TOTAL_LENGTH,
  VOLUME_BOUNDARY_EXTENSION, VOLUME_CORNER_HEIGHT, VOLUME_NET_HEIGHT,
} from './dimensions'

// ─── Derived constants ────────────────────────────────────────────────────────

const HALF_W = COURT_TOTAL_WIDTH  / 2   // 3.05 m — half court width
const HALF_L = COURT_TOTAL_LENGTH / 2   // 6.705 m — half court length

const HW_EXT = HALF_W + VOLUME_BOUNDARY_EXTENSION  // 3.55 m
const HL_EXT = HALF_L + VOLUME_BOUNDARY_EXTENSION  // 7.205 m

// ─── Homography helpers ───────────────────────────────────────────────────────

/** Build the 3×3 homography [row-major, 9 elements] mapping (u,v)→(px,py). */
function buildH(g: CourtGeometry): number[] {
  const { top_left: TL, top_right: TR, bottom_left: BL, bottom_right: BR } = g
  const A = TR.x - BR.x, B = BL.x - BR.x, C = TL.x - TR.x - BL.x + BR.x
  const D = TR.y - BR.y, E = BL.y - BR.y, F = TL.y - TR.y - BL.y + BR.y
  const det = A * E - B * D
  const gh  = (C * E - B * F) / det
  const hh  = (A * F - C * D) / det
  return [
    TR.x * (gh + 1) - TL.x,  BL.x * (hh + 1) - TL.x,  TL.x,
    TR.y * (gh + 1) - TL.y,  BL.y * (hh + 1) - TL.y,  TL.y,
    gh, hh, 1,
  ]
}

/** Multiply two 3×3 matrices (row-major). */
function matMul33(A: number[], B: number[]): number[] {
  const C = new Array<number>(9)
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      C[r * 3 + c] = A[r * 3 + 0] * B[0 * 3 + c]
                   + A[r * 3 + 1] * B[1 * 3 + c]
                   + A[r * 3 + 2] * B[2 * 3 + c]
    }
  }
  return C
}

/** Multiply 3×3 matrix (row-major) by a 3-vector; returns 3-vector. */
function matVec33(M: number[], v: [number, number, number]): [number, number, number] {
  return [
    M[0] * v[0] + M[1] * v[1] + M[2] * v[2],
    M[3] * v[0] + M[4] * v[1] + M[5] * v[2],
    M[6] * v[0] + M[7] * v[1] + M[8] * v[2],
  ]
}

/** Cross product of two 3-vectors. */
function cross(a: number[], b: number[]): [number, number, number] {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ]
}

/** Euclidean norm of a vector. */
function norm(v: number[]): number {
  return Math.sqrt(v.reduce((s, x) => s + x * x, 0))
}

// ─── Camera derivation ────────────────────────────────────────────────────────

/**
 * Derive the 3×4 projection matrix P (row-major, 12 elements) from the court
 * homography g and the background image dimensions.
 *
 * Method
 * ──────
 * 1. Convert the (u,v)→px homography H to a physical (X_m,Y_m)→px homography H_phys
 *    by composing with M⁻¹ where M maps physical coords to normalised coords:
 *       u = X/(2·halfW) + 0.5   ⟹   M⁻¹ scales X by 2·halfW and shifts by –halfW
 *       v = Y/(2·halfL) + 0.5
 * 2. Shift image origin to the principal point (cx=bgW/2, cy=bgH/2) so K is diagonal.
 * 3. Recover focal length f from the orthonormality constraint r₁·r₂ = 0:
 *       f² = –(h1ₓ·h2ₓ + h1ᵧ·h2ᵧ) / (h1_z·h2_z)
 *    where h1,h2 are columns of the centred H_phys.
 * 4. Assemble K, extract [r₁ r₂ r₃ | t], build P = K [R|t].
 */
export function deriveCameraMatrix(
  g: CourtGeometry,
  bgW: number,
  bgH: number,
): number[] {
  // Step 1: H_phys = H * M
  //   We want H_phys to map (X,Y,1) → (px,py,w).
  //   H maps (u,v,1) → (px,py,w), and M maps (X,Y,1) → (u,v,1):
  //     u = X/(2·halfW) + 0.5,  v = Y/(2·halfL) + 0.5
  //   So H_phys = H * M.
  const M = [
    1 / (2 * HALF_W),  0,                  0.5,
    0,                  1 / (2 * HALF_L),  0.5,
    0,                  0,                  1,
  ]
  const H     = buildH(g)
  const Hphys = matMul33(H, M)   // maps (X,Y,1) → (px,py,w) in bg-plate space

  // Step 2: centre — shift principal point to origin
  const cx = bgW / 2
  const cy = bgH / 2
  // T_centre · Hphys  where T_centre = [[1,0,-cx],[0,1,-cy],[0,0,1]]
  const Hc = [
    Hphys[0] - cx * Hphys[6],  Hphys[1] - cx * Hphys[7],  Hphys[2] - cx * Hphys[8],
    Hphys[3] - cy * Hphys[6],  Hphys[4] - cy * Hphys[7],  Hphys[5] - cy * Hphys[8],
    Hphys[6],                   Hphys[7],                   Hphys[8],
  ]
  // Columns of Hc
  const h1 = [Hc[0], Hc[3], Hc[6]]
  const h2 = [Hc[1], Hc[4], Hc[7]]

  // Step 3: focal length from r₁·r₂ = 0
  const num   = h1[0] * h2[0] + h1[1] * h2[1]
  const denom = h1[2] * h2[2]
  // Guard: if constraint is degenerate (camera directly overhead) fall back to heuristic
  let f: number
  if (Math.abs(denom) < 1e-12 || num / denom > 0) {
    // Degenerate or inconsistent — use geometric mean of col magnitudes as fallback
    f = Math.sqrt(Math.abs(num / denom) || 1) * (bgW + bgH) / 4
  } else {
    f = Math.sqrt(-num / denom)
  }

  // Step 4: Assemble camera
  // K_inv applied to columns of Hphys (working in full pixel coords again)
  const K_inv = [1/f, 0, -cx/f, 0, 1/f, -cy/f, 0, 0, 1]
  const r1_raw = matVec33(K_inv, [Hphys[0], Hphys[3], Hphys[6]])
  const r2_raw = matVec33(K_inv, [Hphys[1], Hphys[4], Hphys[7]])
  const t_raw  = matVec33(K_inv, [Hphys[2], Hphys[5], Hphys[8]])

  const lambda = norm(r1_raw)
  const r1 = r1_raw.map(x => x / lambda)
  const r2 = r2_raw.map(x => x / lambda)
  const r3 = cross(r1, r2)
  const t  = t_raw.map(x => x / lambda)

  // P = K [r1 r2 r3 | t]  (3×4 row-major)
  // P[row][col] = sum_k K[row,k] * Rt[k,col]
  // Rt (3×4): columns are r1, r2, r3, t
  const Rt = [
    r1[0], r2[0], r3[0], t[0],
    r1[1], r2[1], r3[1], t[1],
    r1[2], r2[2], r3[2], t[2],
  ]
  const K = [f, 0, cx, 0, f, cy, 0, 0, 1]
  const P = new Array<number>(12)
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 4; c++) {
      P[r * 4 + c] = K[r * 3 + 0] * Rt[0 * 4 + c]
                   + K[r * 3 + 1] * Rt[1 * 4 + c]
                   + K[r * 3 + 2] * Rt[2 * 4 + c]
    }
  }
  return P
}

/**
 * Project a 3-D world point (X,Y,Z) to image coordinates using P.
 * Returns [px, py] in bg-plate pixel space, or null if the point is behind the camera.
 */
export function project3D(
  P: number[],
  X: number, Y: number, Z: number,
): [number, number] | null {
  // Negate Z: r3 = r1×r2 derived from the ground-plane homography points in the
  // world's −Z direction, so we flip the sign to restore the Z-up convention.
  const Zn = -Z
  const u = P[0]  * X + P[1]  * Y + P[2]  * Zn + P[3]
  const v = P[4]  * X + P[5]  * Y + P[6]  * Zn + P[7]
  const w = P[8]  * X + P[9]  * Y + P[10] * Zn + P[11]
  if (w <= 0) return null
  return [u / w, v / w]
}

// ─── Valid-ball volume geometry ────────────────────────────────────────────────

/**
 * The 10 key vertices of the tent-shaped valid-ball volume.
 * Indices:
 *   0..3  base corners  (±hw_ext, ±hl_ext, 0)
 *   4..7  top corners   (±hw_ext, ±hl_ext, VOL_CORNER)
 *   8..9  tent peaks    (±hw_ext, 0,       VOL_NET)
 *
 * X-signs: even index = −hw_ext, odd index = +hw_ext
 * Y-signs for base/top: indices 0,1 = −hl_ext; indices 2,3 = +hl_ext
 */
const VOLUME_VERTICES: [number, number, number][] = [
  // base corners
  [-HW_EXT, -HL_EXT, 0],   // 0
  [ HW_EXT, -HL_EXT, 0],   // 1
  [ HW_EXT,  HL_EXT, 0],   // 2
  [-HW_EXT,  HL_EXT, 0],   // 3
  // top corners
  [-HW_EXT, -HL_EXT, VOLUME_CORNER_HEIGHT],  // 4
  [ HW_EXT, -HL_EXT, VOLUME_CORNER_HEIGHT],  // 5
  [ HW_EXT,  HL_EXT, VOLUME_CORNER_HEIGHT],  // 6
  [-HW_EXT,  HL_EXT, VOLUME_CORNER_HEIGHT],  // 7
  // tent peaks at net (Y=0)
  [-HW_EXT,  0,      VOLUME_NET_HEIGHT],     // 8
  [ HW_EXT,  0,      VOLUME_NET_HEIGHT],     // 9
]

/** Edges as pairs of vertex indices into VOLUME_VERTICES. */
const VOLUME_EDGES: [number, number][] = [
  // base rectangle
  [0, 1], [1, 2], [2, 3], [3, 0],
  // vertical pillars at four corners
  [0, 4], [1, 5], [2, 6], [3, 7],
  // top baseline edges (front and back)
  [4, 5], [6, 7],
  // tent ridge at net
  [8, 9],
  // tent slopes: each sideline top rises corner→peak→corner
  [4, 8], [8, 7],   // left sideline  (X = −hw_ext)
  [5, 9], [9, 6],   // right sideline (X = +hw_ext)
]

// ─── 2-D convex hull (Graham scan) ────────────────────────────────────────────

function convexHull(pts: [number, number][]): [number, number][] {
  if (pts.length < 3) return pts
  // Sort by x then y
  const sorted = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1])

  function cross2(O: [number, number], A: [number, number], B: [number, number]): number {
    return (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0])
  }

  const lower: [number, number][] = []
  for (const p of sorted) {
    while (lower.length >= 2 && cross2(lower[lower.length - 2], lower[lower.length - 1], p) <= 0)
      lower.pop()
    lower.push(p)
  }
  const upper: [number, number][] = []
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i]
    while (upper.length >= 2 && cross2(upper[upper.length - 2], upper[upper.length - 1], p) <= 0)
      upper.pop()
    upper.push(p)
  }
  upper.pop()
  lower.pop()
  return lower.concat(upper)
}

// ─── Sutherland-Hodgman polygon clipping ─────────────────────────────────────

function clipPolygonToRect(
  poly: [number, number][],
  w: number,
  h: number,
): [number, number][] {
  // Four half-planes: x≥0, x≤w, y≥0, y≤h
  const planes: [(p: [number, number]) => boolean, (a: [number, number], b: [number, number]) => [number, number]][] = [
    [p => p[0] >= 0,  (a, b) => { const t = a[0] / (a[0] - b[0]); return [0,              a[1] + t * (b[1] - a[1])] }],
    [p => p[0] <= w,  (a, b) => { const t = (a[0] - w) / (a[0] - b[0]); return [w,        a[1] + t * (b[1] - a[1])] }],
    [p => p[1] >= 0,  (a, b) => { const t = a[1] / (a[1] - b[1]); return [a[0] + t * (b[0] - a[0]), 0]              }],
    [p => p[1] <= h,  (a, b) => { const t = (a[1] - h) / (a[1] - b[1]); return [a[0] + t * (b[0] - a[0]), h]        }],
  ]

  let output = poly
  for (const [inside, intersect] of planes) {
    if (output.length === 0) break
    const input = output
    output = []
    for (let i = 0; i < input.length; i++) {
      const cur  = input[i]
      const prev = input[(i + input.length - 1) % input.length]
      if (inside(cur)) {
        if (!inside(prev)) output.push(intersect(prev, cur))
        output.push(cur)
      } else if (inside(prev)) {
        output.push(intersect(prev, cur))
      }
    }
  }
  return output
}

// ─── Public API ───────────────────────────────────────────────────────────────

export interface VolumeOverlay {
  /** Projected edges in bg-plate pixel space: [x0, y0, x1, y1][]. */
  edges: [number, number, number, number][]
  /** Convex silhouette polygon in bg-plate pixel space (clipped to image rect). */
  silhouette: [number, number][]
}

/**
 * Compute the screen-space edges and silhouette of the valid-ball volume.
 * All coordinates are in bg-plate pixel space; caller must scale by (sx, sy)
 * to convert to canvas display pixels.
 */
export function computeVolumeOverlay(
  g: CourtGeometry,
  bgW: number,
  bgH: number,
): VolumeOverlay {
  const P = deriveCameraMatrix(g, bgW, bgH)

  // Project all vertices
  const projected = VOLUME_VERTICES.map(([X, Y, Z]) => project3D(P, X, Y, Z))

  // Silhouette: convex hull of all visible projected vertices, clipped to image
  const visible = projected.filter((p): p is [number, number] => p !== null)
  const hull    = convexHull(visible)
  const silhouette = clipPolygonToRect(hull, bgW, bgH)

  // Edges: include only edges where both endpoints are visible
  const edges: [number, number, number, number][] = []
  for (const [i, j] of VOLUME_EDGES) {
    const pi = projected[i]
    const pj = projected[j]
    if (pi && pj) edges.push([pi[0], pi[1], pj[0], pj[1]])
  }

  return { edges, silhouette }
}
