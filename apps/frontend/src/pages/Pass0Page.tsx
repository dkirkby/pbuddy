import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import {
  COURT_KV, COURT_TOTAL_WIDTH, COURT_TOTAL_LENGTH,
  NET_POST_HEIGHT, NET_CENTER_HEIGHT, NET_POST_TO_POST,
} from '../lib/dimensions'
import type { ArtifactRef, CourtCorner, CourtGeometry, Pass0RawResult } from '../types/api'

// ─── Court geometry defaults ─────────────────────────────────────────────────

function defaultCourt(bgW: number, bgH: number): CourtGeometry {
  return {
    top_left:     { x: 0.35 * bgW, y: 0.30 * bgH },
    top_right:    { x: 0.65 * bgW, y: 0.30 * bgH },
    bottom_left:  { x: 0.05 * bgW, y: 0.90 * bgH },
    bottom_right: { x: 0.95 * bgW, y: 0.90 * bgH },
  }
}

// ─── Distortion model helpers ─────────────────────────────────────────────────
// Single-term division model: r_u = r_d / (1 + k1 * r_d²)
// scale = image half-diagonal; normalises r so K1 is dimensionless and ~O(1).

function undistortPoint(
  xd: number, yd: number, cx: number, cy: number, k1: number, scale: number,
): [number, number] {
  const dx = (xd - cx) / scale, dy = (yd - cy) / scale
  const r2 = dx * dx + dy * dy
  if (Math.abs(k1) < 1e-9 || r2 < 1e-9) return [xd, yd]
  const rd = Math.sqrt(r2)
  const ru = rd / (1 + k1 * rd * rd)
  return [cx + dx * (ru / rd) * scale, cy + dy * (ru / rd) * scale]
}

function distortPoint(
  xu: number, yu: number, cx: number, cy: number, k1: number, scale: number,
): [number, number] {
  const dx = (xu - cx) / scale, dy = (yu - cy) / scale
  const r2 = dx * dx + dy * dy
  if (Math.abs(k1) < 1e-9 || r2 < 1e-9) return [xu, yu]
  const ru = Math.sqrt(r2)
  const disc = 1 - 4 * k1 * r2
  if (disc < 0) return [xu, yu]
  const rd = (1 - Math.sqrt(disc)) / (2 * k1 * ru)
  return [cx + dx * (rd / ru) * scale, cy + dy * (rd / ru) * scale]
}

// ─── Net 3D projection helpers ────────────────────────────────────────────────
// Coordinate system: X across net (left→right), Y up, Z along court (Z=0 at net).
// Court corners at (±HALF_W, 0, ±HALF_L); net posts at (±NET_HALF_W, *, 0).

const HALF_W = COURT_TOTAL_WIDTH  / 2   // 3.05 m
const HALF_L = COURT_TOTAL_LENGTH / 2   // 6.705 m
const NET_HALF_W = NET_POST_TO_POST / 2 // 3.355 m

// Net profile vertices: left-post-base → left-post-top → center-top → right-post-top → right-post-base
const NET_3D_POINTS: [number, number, number][] = [
  [-NET_HALF_W, 0,                 0],
  [-NET_HALF_W, NET_POST_HEIGHT,   0],
  [0,           NET_CENTER_HEIGHT, 0],
  [ NET_HALF_W, NET_POST_HEIGHT,   0],
  [ NET_HALF_W, 0,                 0],
]

function mul33(A: number[], B: number[]): number[] {
  const C = new Array<number>(9)
  for (let r = 0; r < 3; r++)
    for (let c = 0; c < 3; c++)
      C[r*3+c] = A[r*3+0]*B[0*3+c] + A[r*3+1]*B[1*3+c] + A[r*3+2]*B[2*3+c]
  return C
}

function mv33(M: number[], v: [number, number, number]): [number, number, number] {
  return [
    M[0]*v[0] + M[1]*v[1] + M[2]*v[2],
    M[3]*v[0] + M[4]*v[1] + M[5]*v[2],
    M[6]*v[0] + M[7]*v[1] + M[8]*v[2],
  ]
}

/**
 * Build SVG polyline points for the net overlay using a pinhole camera model.
 * f = cx / tan(horizFOVdeg/2 in radians); K derived from f with square pixels
 * and principal point at (cx, cy). Extrinsics solved from ground-plane homography.
 * Returns null if any net vertex is behind the camera.
 */
function buildNetPolyline(
  g: CourtGeometry,
  bgW: number, bgH: number,
  k1: number,
  horizFOVdeg: number,
): string | null {
  const cx = bgW / 2, cy = bgH / 2
  const scale = Math.sqrt(cx*cx + cy*cy)

  // Ground-plane homography: (X, Z) → undistorted image; Z along court, Z=0 at net.
  // Hphys = H_uv * M  where u = X/(2·HALF_W)+0.5, v = Z/(2·HALF_L)+0.5
  const H = buildUndistortedH(g, cx, cy, k1, scale)
  const M = [1/(2*HALF_W), 0, 0.5, 0, 1/(2*HALF_L), 0.5, 0, 0, 1]
  const Hp = mul33(H, M)

  const f = cx / Math.tan(horizFOVdeg * Math.PI / 360)
  const Ki = [1/f, 0, -cx/f, 0, 1/f, -cy/f, 0, 0, 1]

  // Decompose: K_inv * Hphys gives [r_X | r_Z | t] up to scale lambda
  const h1 = mv33(Ki, [Hp[0], Hp[3], Hp[6]])
  const h2 = mv33(Ki, [Hp[1], Hp[4], Hp[7]])
  const h3 = mv33(Ki, [Hp[2], Hp[5], Hp[8]])

  const vn = (v: number[]) => Math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
  const lam = (vn(h1) + vn(h2)) / 2
  if (lam < 1e-10) return null

  const rX = h1.map(x => x/lam)
  const rZ = h2.map(x => x/lam)
  const t  = h3.map(x => x/lam)
  // r_Y = r_Z × r_X  → world-up in camera space (det(R)=1 for proper rotation)
  const rY = [
    rZ[1]*rX[2] - rZ[2]*rX[1],
    rZ[2]*rX[0] - rZ[0]*rX[2],
    rZ[0]*rX[1] - rZ[1]*rX[0],
  ]

  // P = K * [rX | rY | rZ | t]  (3×4 row-major)
  const K = [f, 0, cx, 0, f, cy, 0, 0, 1]
  const Rt = [
    rX[0], rY[0], rZ[0], t[0],
    rX[1], rY[1], rZ[1], t[1],
    rX[2], rY[2], rZ[2], t[2],
  ]
  const P = new Array<number>(12)
  for (let r = 0; r < 3; r++)
    for (let c = 0; c < 4; c++)
      P[r*4+c] = K[r*3+0]*Rt[0*4+c] + K[r*3+1]*Rt[1*4+c] + K[r*3+2]*Rt[2*4+c]

  const N_SAMPLES = 12  // samples per segment, including endpoints

  function projectAndDistort(X: number, Y: number, Z: number): [number, number] | null {
    const u = P[0]*X + P[1]*Y + P[2]*Z + P[3]
    const v = P[4]*X + P[5]*Y + P[6]*Z + P[7]
    const w = P[8]*X + P[9]*Y + P[10]*Z + P[11]
    if (w <= 0) return null
    return distortPoint(u/w, v/w, cx, cy, k1, scale)
  }

  const pts: string[] = []
  for (let seg = 0; seg < NET_3D_POINTS.length - 1; seg++) {
    const [X0, Y0, Z0] = NET_3D_POINTS[seg]
    const [X1, Y1, Z1] = NET_3D_POINTS[seg + 1]
    const start = seg === 0 ? 0 : 1  // skip first point of each segment (already added as last of prev)
    for (let i = start; i <= N_SAMPLES; i++) {
      const t = i / N_SAMPLES
      const p = projectAndDistort(X0 + t*(X1-X0), Y0 + t*(Y1-Y0), Z0 + t*(Z1-Z0))
      if (!p) return null
      pts.push(`${p[0].toFixed(1)},${p[1].toFixed(1)}`)
    }
  }
  return pts.join(' ')
}

// ─── Homography helpers ───────────────────────────────────────────────────────

function buildH(TL: CourtCorner, TR: CourtCorner, BL: CourtCorner, BR: CourtCorner): number[] {
  const A = TR.x - BR.x, B = BL.x - BR.x, C = TL.x - TR.x - BL.x + BR.x
  const D = TR.y - BR.y, E = BL.y - BR.y, F = TL.y - TR.y - BL.y + BR.y
  const det = A * E - B * D
  const gh = (C * E - B * F) / det
  const hh = (A * F - C * D) / det
  return [
    TR.x * (gh + 1) - TL.x, BL.x * (hh + 1) - TL.x, TL.x,
    TR.y * (gh + 1) - TL.y, BL.y * (hh + 1) - TL.y, TL.y,
    gh, hh, 1,
  ]
}

function buildUndistortedH(g: CourtGeometry, cx: number, cy: number, k1: number, scale: number): number[] {
  const [tlx, tly]  = undistortPoint(g.top_left.x,     g.top_left.y,     cx, cy, k1, scale)
  const [trx, try_] = undistortPoint(g.top_right.x,    g.top_right.y,    cx, cy, k1, scale)
  const [blx, bly]  = undistortPoint(g.bottom_left.x,  g.bottom_left.y,  cx, cy, k1, scale)
  const [brx, bry]  = undistortPoint(g.bottom_right.x, g.bottom_right.y, cx, cy, k1, scale)
  return buildH({ x: tlx, y: tly }, { x: trx, y: try_ }, { x: blx, y: bly }, { x: brx, y: bry })
}

function sampleCourtLine(
  H: number[], u0: number, v0: number, u1: number, v1: number,
  cx: number, cy: number, k1: number, scale: number, nSamples = 24,
): string {
  const pts: string[] = []
  for (let i = 0; i <= nSamples; i++) {
    const t = i / nSamples
    const u = u0 + t * (u1 - u0), v = v0 + t * (v1 - v0)
    const w  = H[6] * u + H[7] * v + 1
    const xu = (H[0] * u + H[1] * v + H[2]) / w
    const yu = (H[3] * u + H[4] * v + H[5]) / w
    const [xd, yd] = distortPoint(xu, yu, cx, cy, k1, scale)
    pts.push(`${xd.toFixed(1)},${yd.toFixed(1)}`)
  }
  return pts.join(' ')
}

// ─── Court line definitions ───────────────────────────────────────────────────

const KV = COURT_KV

const OUTER_LINES = [
  [0, 0, 1, 0], [1, 0, 1, 1], [1, 1, 0, 1], [0, 1, 0, 0],
]
const INNER_LINES = [
  [0, KV, 1, KV],
  [0, 1 - KV, 1, 1 - KV],
  [0.5, 0, 0.5, KV],
  [0.5, 1 - KV, 0.5, 1],
]

// ─── Main image overlay (interactive) ────────────────────────────────────────

type CornerKey = keyof CourtGeometry
const CORNER_KEYS: CornerKey[] = ['top_left', 'top_right', 'bottom_left', 'bottom_right']

interface OverlayProps {
  geometry: CourtGeometry
  imageWidth: number
  imageHeight: number
  k1: number
  horizFOV: number
  onChange: (g: CourtGeometry) => void
  draggable?: boolean
  strokeScale?: number
}

function DistortedCourtOverlay({ geometry, imageWidth, imageHeight, k1, horizFOV, onChange, draggable = true, strokeScale = 1 }: OverlayProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragging, setDragging] = useState<CornerKey | null>(null)

  const cx = imageWidth / 2, cy = imageHeight / 2
  const scale = Math.sqrt(cx * cx + cy * cy)
  const H = buildUndistortedH(geometry, cx, cy, k1, scale)
  const netPts = buildNetPolyline(geometry, imageWidth, imageHeight, k1, horizFOV)

  function toImageCoords(clientX: number, clientY: number): CourtCorner {
    const rect = svgRef.current!.getBoundingClientRect()
    const raw = {
      x: (clientX - rect.left) * (imageWidth  / rect.width),
      y: (clientY - rect.top)  * (imageHeight / rect.height),
    }
    return { x: Math.round(raw.x * 4) / 4, y: Math.round(raw.y * 4) / 4 }
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragging || !draggable) return
    onChange({ ...geometry, [dragging]: toImageCoords(e.clientX, e.clientY) })
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${imageWidth} ${imageHeight}`}
      style={{
        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
        overflow: 'visible', cursor: dragging ? 'grabbing' : 'default',
      }}
      onMouseMove={onMouseMove}
      onMouseUp={() => setDragging(null)}
      onMouseLeave={() => setDragging(null)}
    >
      {OUTER_LINES.map((seg, i) => (
        <polyline key={`out-${i}`}
          points={sampleCourtLine(H, seg[0], seg[1], seg[2], seg[3], cx, cy, k1, scale)}
          fill="none" stroke="#f00" strokeWidth={2 * strokeScale} strokeOpacity={0.85} />
      ))}
      {INNER_LINES.map((seg, i) => (
        <polyline key={`inn-${i}`}
          points={sampleCourtLine(H, seg[0], seg[1], seg[2], seg[3], cx, cy, k1, scale)}
          fill="none" stroke="#f00" strokeWidth={1.5 * strokeScale} strokeOpacity={0.7} />
      ))}
      {netPts && (
        <polyline points={netPts}
          fill="none" stroke="#f00" strokeWidth={1 * strokeScale} strokeOpacity={0.9} />
      )}
      {CORNER_KEYS.map((key) => {
        const c = geometry[key] as CourtCorner
        return (
          <circle key={key} cx={c.x} cy={c.y} r={10 * strokeScale}
            fill="#f00" fillOpacity={dragging === key ? 0.9 : (draggable ? 0.6 : 0.3)}
            stroke="#fff" strokeWidth={2 * strokeScale}
            style={{ cursor: draggable ? 'grab' : 'default' }}
            onMouseDown={draggable ? (e) => { e.stopPropagation(); setDragging(key) } : undefined} />
        )
      })}
    </svg>
  )
}

// ─── Corner zoom box ──────────────────────────────────────────────────────────

const ZOOM   = 4
const BOX_W  = 240
const BOX_H  = 180

const CORNER_LABELS: Record<CornerKey, string> = {
  top_left:     'Top Left',
  top_right:    'Top Right',
  bottom_left:  'Bottom Left',
  bottom_right: 'Bottom Right',
}

interface ZoomBoxProps {
  cornerKey: CornerKey
  geometry: CourtGeometry
  bgUrl: string
  bgW: number
  bgH: number
  k1: number
  onChange: (g: CourtGeometry) => void
  draggable?: boolean
}

function CornerZoomBox({ cornerKey, geometry, bgUrl, bgW, bgH, k1, onChange, draggable = true }: ZoomBoxProps) {
  const corner = geometry[cornerKey] as CourtCorner
  const geometryRef = useRef(geometry)
  geometryRef.current = geometry
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const [dragging, setDragging] = useState(false)

  // Document-level listeners while dragging so cursor can leave the box freely.
  useEffect(() => {
    if (!dragging) return
    function onMove(e: MouseEvent) {
      const g = geometryRef.current
      const c = g[cornerKey] as CourtCorner
      onChangeRef.current({
        ...g,
        [cornerKey]: {
          x: Math.round((c.x + e.movementX / ZOOM) * 4) / 4,
          y: Math.round((c.y + e.movementY / ZOOM) * 4) / 4,
        },
      })
    }
    function onUp() { setDragging(false); document.body.style.cursor = '' }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'grabbing'
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
    }
  }, [dragging, cornerKey])

  const cx = bgW / 2, cy = bgH / 2
  const scale = Math.sqrt(cx * cx + cy * cy)
  const H = buildUndistortedH(geometry, cx, cy, k1, scale)

  const innerLeft = BOX_W / 2 - ZOOM * corner.x
  const innerTop  = BOX_H / 2 - ZOOM * corner.y

  return (
    <div style={{
      width: BOX_W, height: BOX_H, position: 'relative', overflow: 'hidden',
      border: '1px solid #555', background: '#111', flexShrink: 0,
    }}>
      {/* Zoomed image + court lines — pointer events disabled; handle SVG owns interaction */}
      <div style={{
        position: 'absolute', width: bgW, height: bgH,
        transformOrigin: '0 0',
        transform: `scale(${ZOOM})`,
        left: innerLeft, top: innerTop,
        pointerEvents: 'none',
      }}>
        <img src={bgUrl} style={{ width: bgW, height: bgH, display: 'block' }} alt="" />
        <svg
          viewBox={`0 0 ${bgW} ${bgH}`}
          style={{ position: 'absolute', top: 0, left: 0, width: bgW, height: bgH, overflow: 'hidden' }}
        >
          {OUTER_LINES.map((seg, i) => (
            <polyline key={`out-${i}`}
              points={sampleCourtLine(H, seg[0], seg[1], seg[2], seg[3], cx, cy, k1, scale)}
              fill="none" stroke="#f00" strokeWidth={2 / ZOOM} strokeOpacity={0.85} />
          ))}
          {INNER_LINES.map((seg, i) => (
            <polyline key={`inn-${i}`}
              points={sampleCourtLine(H, seg[0], seg[1], seg[2], seg[3], cx, cy, k1, scale)}
              fill="none" stroke="#f00" strokeWidth={1.5 / ZOOM} strokeOpacity={0.7} />
          ))}
        </svg>
      </div>

      {/* Interaction layer — fixed to zoom-box bounds, so no overflow event leakage */}
      <svg style={{ position: 'absolute', inset: 0, width: BOX_W, height: BOX_H,
                    cursor: dragging ? 'grabbing' : 'default' }}>
        {/* Thin crosshair at box centre */}
        <line x1={BOX_W / 2 - 8} y1={BOX_H / 2} x2={BOX_W / 2 + 8} y2={BOX_H / 2}
              stroke="#f00" strokeWidth={0.5} strokeOpacity={0.7} />
        <line x1={BOX_W / 2} y1={BOX_H / 2 - 8} x2={BOX_W / 2} y2={BOX_H / 2 + 8}
              stroke="#f00" strokeWidth={0.5} strokeOpacity={0.7} />
        {/* Draggable handle — always at box centre; drag moves corner via movementX/Y */}
        <circle cx={BOX_W / 2} cy={BOX_H / 2} r={10}
          fill="#f00" fillOpacity={dragging ? 0.85 : (draggable ? 0.4 : 0.15)}
          stroke="#fff" strokeWidth={1.5}
          style={{ cursor: draggable ? 'grab' : 'default' }}
          onMouseDown={draggable ? (e) => { e.preventDefault(); setDragging(true) } : undefined} />
      </svg>

      {/* Corner label + live coords */}
      <div style={{
        position: 'absolute', bottom: 3, left: 5, fontSize: 10, color: '#ccc',
        pointerEvents: 'none', textShadow: '0 0 4px #000',
      }}>
        {CORNER_LABELS[cornerKey]} ({corner.x.toFixed(2)}, {corner.y.toFixed(2)})
      </div>
    </div>
  )
}

// ─── Time label helper ────────────────────────────────────────────────────────

function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// ─── Pass0Page ────────────────────────────────────────────────────────────────

export default function Pass0Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [corners, setCorners] = useState<CourtGeometry | null>(null)
  const [k1, setK1] = useState(0)
  const [horizFOV, setHorizFOV] = useState(60)
  // null = track midpoint automatically; number = explicit user selection
  const [chunkIndex, setChunkIndex] = useState<number | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  const { data: artResp } = useQuery({
    queryKey: ['pass0-artifacts', projectId],
    queryFn: () => api.getPass0Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []
  const rawJsonArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )
  // All raw PNG artifacts from the medians/ subdir, sorted by path → chunk order.
  // Re-running pass0 appends duplicate registrations; deduplicate by path keeping the
  // most recent, then sort by path for correct chunk ordering.
  const bgArtifacts = (() => {
    const seen = new Set<string>()
    return artifacts
      .filter(a => a.artifact_role === 'raw' && a.artifact_type === 'png' && a.path.includes('/medians/'))
      .sort((a, b) => a.path.localeCompare(b.path) || b.created_at.localeCompare(a.created_at))
      .filter(a => { if (seen.has(a.path)) return false; seen.add(a.path); return true })
  })()

  const { data: rawResult } = useQuery<Pass0RawResult>({
    queryKey: ['pass0-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

  const { data: corrResp } = useQuery({
    queryKey: ['pass0-corrections', projectId],
    queryFn: () => api.getPass0Corrections(projectId!),
    enabled: !!rawJsonArtifact,
  })

  useEffect(() => {
    if (!rawResult || corrResp === undefined) return
    const corr = corrResp.data
    setCorners(corr?.court_geometry ?? defaultCourt(rawResult.bg_width, rawResult.bg_height))
    setK1(corr?.k1 ?? 0)
    setHorizFOV(corr?.horiz_fov ?? 60)
    setIsDirty(false)
  }, [rawResult, corrResp])

  function handleCornersChange(g: CourtGeometry) {
    setCorners(g)
    setIsDirty(true)
  }

  function handleK1Change(v: number) {
    setK1(v)
    setIsDirty(true)
  }

  function handleHorizFOVChange(v: number) {
    setHorizFOV(v)
    setIsDirty(true)
  }

  async function handleSave() {
    if (!corners) return
    setSaving(true)
    setStatusMsg(null)
    try {
      await api.submitPass0Corrections(projectId!, { court_geometry: corners, k1, horiz_fov: horizFOV })
      setIsDirty(false)
      setStatusMsg('Corrections saved.')
      qc.invalidateQueries({ queryKey: ['pass0-corrections', projectId] })
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleAccept() {
    setAccepting(true)
    setStatusMsg(null)
    try {
      if (isDirty) await handleSave()
      await api.acceptPass0(projectId!)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setAccepting(false)
    }
  }

  const midpointChunk = rawResult?.midpoint_chunk ?? 0
  const totalChunks   = rawResult?.median_count   ?? bgArtifacts.length
  const fps           = rawResult?.video_fps       ?? 30
  const chunkSizeSec  = Math.round(4 * fps) / fps   // matches backend chunk_size / fps

  const effectiveChunk = chunkIndex ?? midpointChunk
  const isAtMidpoint   = chunkIndex === null

  const bgArtifact = bgArtifacts[effectiveChunk] ?? null
  const bgUrl      = bgArtifact ? api.artifactUrl(bgArtifact.id) : null

  // Time label for the current chunk
  const chunkStartSec = effectiveChunk * chunkSizeSec
  const chunkEndSec   = chunkStartSec + chunkSizeSec
  const timeLabel     = totalChunks > 0
    ? `${fmtTime(chunkStartSec)} – ${fmtTime(chunkEndSec)}`
    : ''

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>
      <h1>Pass 0 Review — Identify Court and Specify Camera Model</h1>

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {/* ── Left column: controls ── */}
        <div style={{ flex: '0 0 260px' }}>

          <section style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>Radial Distortion (K1)</h3>
            <p style={{ fontSize: 12, color: '#555', marginTop: 0 }}>
              r_u = r_d / (1 + K1·r_d²)<br />
              Negative = barrel, positive = pincushion.
            </p>
            <input
              type="range" min={-0.5} max={0.5} step={0.001} value={k1}
              onChange={(e) => handleK1Change(parseFloat(e.target.value))}
              style={{ width: '100%' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#555' }}>
              <span>-0.500</span>
              <strong>{k1.toFixed(3)}</strong>
              <span>+0.500</span>
            </div>
            <button
              onClick={() => handleK1Change(0)} disabled={k1 === 0}
              style={{ marginTop: 6, fontSize: 12, padding: '2px 8px' }}
            >
              Reset to 0
            </button>
          </section>

          <section style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>Horizontal FOV (°)</h3>
            <p style={{ fontSize: 12, color: '#555', marginTop: 0 }}>
              Sets focal length for 3D→2D projection. Adjust until the red net line aligns with the actual net.
            </p>
            <input
              type="range" min={10} max={120} step={0.5} value={horizFOV}
              onChange={(e) => handleHorizFOVChange(parseFloat(e.target.value))}
              style={{ width: '100%' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#555' }}>
              <span>10°</span>
              <strong>{horizFOV.toFixed(1)}°</strong>
              <span>120°</span>
            </div>
            <button
              onClick={() => handleHorizFOVChange(60)} disabled={horizFOV === 60}
              style={{ marginTop: 6, fontSize: 12, padding: '2px 8px' }}
            >
              Reset to 60°
            </button>
          </section>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {isDirty && <div style={{ color: '#f90', fontSize: 12 }}>⚠ Unsaved changes</div>}
            <button onClick={handleSave} disabled={saving || !isDirty} style={{ padding: '8px 0' }}>
              {saving ? 'Saving…' : 'Save Corrections'}
            </button>
            <button
              onClick={handleAccept} disabled={accepting}
              style={{ padding: '8px 0', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            >
              {accepting ? 'Accepting…' : 'Accept Pass 0 →'}
            </button>
            {statusMsg && (
              <p style={{ fontSize: 12, color: statusMsg.startsWith('Error') ? 'red' : 'green', margin: 0 }}>
                {statusMsg}
              </p>
            )}
          </div>
        </div>

        {/* ── Right column: median image + zoom grid ── */}
        <div style={{ flex: '1 1 500px' }}>
          <h3 style={{ marginTop: 0 }}>Median Background</h3>
          <p style={{ fontSize: 12, color: '#666', marginTop: 0 }}>
            Drag red handles in the main image for coarse positioning.
            Drag handles in the zoom boxes below for 1/4-pixel precision.{' '}
            <span style={{ color: isAtMidpoint ? '#888' : '#f90' }}>
              Dragging is only enabled at the midpoint image.
            </span>
          </p>

          {/* Background image slider */}
          {totalChunks > 1 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="range"
                  min={0}
                  max={totalChunks - 1}
                  step={1}
                  value={effectiveChunk}
                  onChange={(e) => setChunkIndex(parseInt(e.target.value, 10))}
                  style={{ flex: 1 }}
                />
                <button
                  onClick={() => setChunkIndex(null)}
                  disabled={isAtMidpoint}
                  style={{ fontSize: 12, padding: '2px 8px', whiteSpace: 'nowrap' }}
                >
                  ⌖ Midpoint
                </button>
              </div>
              <div style={{ fontSize: 12, color: isAtMidpoint ? '#6b6' : '#aaa', marginTop: 2 }}>
                {timeLabel}{isAtMidpoint ? ' (midpoint)' : ''}
                {' '}— chunk {effectiveChunk} of {totalChunks - 1}
              </div>
            </div>
          )}

          {/* Main image */}
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
            {bgUrl ? (
              <img src={bgUrl} alt="Median background" style={{ maxWidth: '100%', display: 'block' }} />
            ) : (
              <div style={{
                width: 600, height: 338, background: '#222',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555',
              }}>
                {rawJsonArtifact ? 'Loading image…' : 'No artifacts yet — pass 0 may still be running.'}
              </div>
            )}
            {bgUrl && corners && rawResult && (
              <DistortedCourtOverlay
                geometry={corners}
                imageWidth={rawResult.bg_width}
                imageHeight={rawResult.bg_height}
                k1={k1}
                horizFOV={horizFOV}
                onChange={handleCornersChange}
                draggable={isAtMidpoint}
              />
            )}
          </div>

          {/* 2×2 zoom grid — shown once image and corners are ready */}
          {bgUrl && corners && rawResult && (() => {
            const bgW = rawResult.bg_width
            const bgH = rawResult.bg_height
            return (
              <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                {(['top_left', 'top_right', 'bottom_left', 'bottom_right'] as CornerKey[]).map(key => (
                  <CornerZoomBox
                    key={key}
                    cornerKey={key}
                    geometry={corners}
                    bgUrl={bgUrl}
                    bgW={bgW}
                    bgH={bgH}
                    k1={k1}
                    onChange={handleCornersChange}
                    draggable={isAtMidpoint}
                  />
                ))}
              </div>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
