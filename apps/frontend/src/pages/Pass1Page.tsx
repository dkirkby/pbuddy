import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ArtifactRef, Pass1CameraModelEntry, Pass1ChunkVertices, Pass1CourtLine, Pass1RawResult, Pass1Sample, Pass1SamplePoint, Pass1SegmentAnalysis } from '../types/api'

// ─── Distortion helpers ───────────────────────────────────────────────────────

function undistort(xd: number, yd: number, cx: number, cy: number, k1: number, scale: number): [number, number] {
  const dx = (xd - cx) / scale
  const dy = (yd - cy) / scale
  const r2 = dx * dx + dy * dy
  if (Math.abs(k1) < 1e-9 || r2 < 1e-9) return [xd, yd]
  const rd = Math.sqrt(r2)
  const ru = rd / (1 + k1 * r2)
  const f = ru / rd
  return [cx + dx * f * scale, cy + dy * f * scale]
}

function distort(xu: number, yu: number, cx: number, cy: number, k1: number, scale: number): [number, number] {
  const dx = (xu - cx) / scale
  const dy = (yu - cy) / scale
  const r2 = dx * dx + dy * dy
  if (Math.abs(k1) < 1e-9 || r2 < 1e-9) return [xu, yu]
  const ru = Math.sqrt(r2)
  const disc = 1 - 4 * k1 * r2
  if (disc < 0) return [xu, yu]
  const rd = (1 - Math.sqrt(disc)) / (2 * k1 * ru)
  const f = rd / ru
  return [cx + dx * f * scale, cy + dy * f * scale]
}

function distortedEdgePts(
  p1: [number, number], p2: [number, number],
  cx: number, cy: number, k1: number, scale: number,
  n = 24,
): string {
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    const [xd, yd] = distort(p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]), cx, cy, k1, scale)
    pts.push(`${xd.toFixed(1)},${yd.toFixed(1)}`)
  }
  return pts.join(' ')
}

// ─── Vertex overlay ───────────────────────────────────────────────────────────

const VERTEX_EDGES: { p1: keyof Pass1ChunkVertices; p2: keyof Pass1ChunkVertices; color: string }[] = [
  { p1: 'baseline_left',   p2: 'baseline_right',  color: '#0ff' },
  { p1: 'kitchen_left',    p2: 'kitchen_right',    color: '#f80' },
  { p1: 'baseline_left',   p2: 'kitchen_left',     color: '#f0f' },
  { p1: 'baseline_right',  p2: 'kitchen_right',    color: '#ff0' },
  { p1: 'baseline_center', p2: 'kitchen_center',   color: '#0f0' },
]

function VertexOverlay({ vertices, cx, cy, k1, scale }: {
  vertices: Pass1ChunkVertices
  cx: number; cy: number; k1: number; scale: number
}): ReactNode {
  return (
    <g opacity={0.75}>
      {VERTEX_EDGES.map(({ p1, p2, color }) => {
        const a = vertices[p1] as [number, number] | null | undefined
        const b = vertices[p2] as [number, number] | null | undefined
        if (!a || !b) return null
        return (
          <polyline key={`${p1}-${p2}`}
            points={distortedEdgePts(a, b, cx, cy, k1, scale)}
            fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round"
          />
        )
      })}
    </g>
  )
}

// Dashed far-side court edges extrapolated from the camera model.
// kitchen_left/right are undistorted; camera corners are distorted and undistorted here.
function CourtOutlineOverlay({ kitchenLeft, kitchenRight, corners, cx, cy, k1, scale }: {
  kitchenLeft: [number, number] | null | undefined
  kitchenRight: [number, number] | null | undefined
  corners: { x: number; y: number }[]   // [near-left, near-right, far-right, far-left] distorted
  cx: number; cy: number; k1: number; scale: number
}): ReactNode {
  if (corners.length < 4) return null
  const farRight = undistort(corners[2].x, corners[2].y, cx, cy, k1, scale)
  const farLeft  = undistort(corners[3].x, corners[3].y, cx, cy, k1, scale)

  const edges: { p1: [number, number]; p2: [number, number]; color: string }[] = [
    { p1: farLeft, p2: farRight, color: '#0ff' },  // far baseline
  ]
  if (kitchenLeft)  edges.push({ p1: kitchenLeft  as [number, number], p2: farLeft,  color: '#f0f' })
  if (kitchenRight) edges.push({ p1: kitchenRight as [number, number], p2: farRight, color: '#ff0' })

  return (
    <g opacity={0.6} strokeDasharray="6,4">
      {edges.map(({ p1, p2, color }, i) => (
        <polyline key={i}
          points={distortedEdgePts(p1, p2, cx, cy, k1, scale)}
          fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round"
        />
      ))}
    </g>
  )
}

// ─── Subplot ──────────────────────────────────────────────────────────────────

const PLOT_W = 100
const PLOT_H = 33
const PAD = { top: 2, right: 2, bottom: 2, left: 2 }
const INNER_W = PLOT_W - PAD.left - PAD.right
const INNER_H = PLOT_H - PAD.top - PAD.bottom

function maxAbsGrad(vals: number[]): number {
  const n = vals.length
  if (n < 2) return 0
  let max = 0
  for (let i = 0; i < n; i++) {
    const dv = i === 0       ? vals[1] - vals[0]
             : i === n - 1   ? vals[n - 1] - vals[n - 2]
             : (vals[i + 1] - vals[i - 1]) / 2
    if (Math.abs(dv) > max) max = Math.abs(dv)
  }
  return max
}

function npGradient(samples: Pass1Sample[]): Pass1Sample[] {
  const n = samples.length
  if (n < 2) return samples.map(s => ({ ...s, val: 0 }))
  return samples.map((s, i) => ({
    ...s,
    val: i === 0       ? samples[1].val - samples[0].val
       : i === n - 1   ? samples[n - 1].val - samples[n - 2].val
       : (samples[i + 1].val - samples[i - 1].val) / 2,
  }))
}

function SegmentPlot({ pt, index, color, referenceCurve, lagPx, isInterpolated, isOutlier, gradLimit }: {
  pt: Pass1SamplePoint; index: number; color: string
  referenceCurve?: number[]
  lagPx?: number | null
  isInterpolated?: boolean
  isOutlier?: boolean
  gradLimit: number
}) {
  if (!pt.samples?.length) return null

  const n = pt.samples.length
  const toX = (s: number) => ((s + 1) / 2) * INNER_W
  const toY = (v: number) => (INNER_H / 2) * (1 - v / gradLimit)
  const pts = (samples: Pass1Sample[]) =>
    samples.map(s => `${toX(s.s).toFixed(1)},${toY(s.val).toFixed(1)}`).join(' ')
  const xMid = toX(0)
  const yZero = toY(0)
  const label = String.fromCharCode(65 + index)
  const grad = npGradient(pt.samples)
  const refGrad: Pass1Sample[] | null = referenceCurve?.length === n
    ? referenceCurve.map((val, j) => ({ s: n > 1 ? -1 + 2 * j / (n - 1) : 0, val }))
    : null
  const lagLabel = lagPx != null ? `${lagPx > 0 ? '+' : ''}${lagPx.toFixed(1)}` : null

  const bgFill = isOutlier ? '#1a0000' : '#111'

  return (
    <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <g transform={`translate(${PAD.left},${PAD.top})`}>
        <rect x={0} y={0} width={INNER_W} height={INNER_H} fill={bgFill} stroke="#444" strokeWidth={0.5} />
        <line x1={0} y1={yZero} x2={INNER_W} y2={yZero} stroke="#555" strokeWidth={0.5} strokeDasharray="2,2" />
        <line x1={xMid} y1={0} x2={xMid} y2={INNER_H} stroke="#555" strokeWidth={0.5} strokeDasharray="2,2" />
        {refGrad && (
          <polyline points={pts(refGrad)} fill="none" stroke={color} strokeWidth={0.5} strokeOpacity={0.5} strokeDasharray="2,2" />
        )}
        <polyline points={pts(grad)} fill="none" stroke={color} strokeWidth={0.75}
          strokeOpacity={isInterpolated ? 0.45 : 1} />
        <text x={2} y={INNER_H - 2} fontSize={7} fill={color} opacity={0.75}>{label}</text>
        {lagLabel && (
          <text x={INNER_W - 1} y={INNER_H - 2} fontSize={6} fill={color} opacity={0.9} textAnchor="end">{lagLabel}px</text>
        )}
        {isInterpolated && (
          <text x={2} y={7} fontSize={6} fill="#aaa" opacity={0.8}>~</text>
        )}
        {isOutlier && (
          <text x={INNER_W - 1} y={7} fontSize={6} fill="#f66" textAnchor="end">!</text>
        )}
      </g>
    </svg>
  )
}

function CourtLineGrid({ line, lineAnalyses, chunkPos, pixPerSample, gradLimit }: {
  line: Pass1CourtLine
  lineAnalyses?: Pass1SegmentAnalysis[]
  chunkPos: number
  pixPerSample: number
  gradLimit: number
}) {
  const ROW = 4
  const rows: Pass1SamplePoint[][] = []
  for (let i = 0; i < line.points.length; i += ROW)
    rows.push(line.points.slice(i, i + ROW))

  return (
    <div style={{ marginBottom: 4 }}>
      {rows.map((row, ri) => (
        <div key={ri} style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${ROW}, 1fr)`,
          gap: 2,
          marginBottom: rows.length > 1 ? 2 : 0,
        }}>
          {row.map((pt, i) => {
            const absIdx = ri * ROW + i
            const analysis = lineAnalyses?.[absIdx]
            const lagSamples = analysis?.lags?.[chunkPos] ?? null
            const lagPx = lagSamples != null ? lagSamples * pixPerSample : null
            const isInterpolated = analysis?.is_interpolated?.[chunkPos] ?? false
            const isOutlier = analysis?.is_outlier?.[chunkPos] ?? false
            return (
              <SegmentPlot key={i} pt={pt} index={absIdx} color={line.color}
                referenceCurve={analysis?.reference}
                lagPx={lagPx}
                isInterpolated={isInterpolated}
                isOutlier={isOutlier}
                gradLimit={gradLimit} />
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ─── Pass1Page ────────────────────────────────────────────────────────────────

export default function Pass1Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  // null = track midpoint automatically; number = explicit slider position
  const [selectedChunkPos, setSelectedChunkPos] = useState<number | null>(null)

  const { data: artResp } = useQuery({
    queryKey: ['pass1-artifacts', projectId],
    queryFn: () => api.getPass1Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []

  const rawJsonArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )

  const { data: rawResult } = useQuery<Pass1RawResult>({
    queryKey: ['pass1-raw', projectId, rawJsonArtifact?.id],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      if (!resp.ok) throw new Error(`Artifact fetch failed: ${resp.status}`)
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

  // Resolve effective chunk position: null means use midpoint
  const chunkPos = useMemo(() => {
    if (selectedChunkPos !== null) return selectedChunkPos
    if (!rawResult?.chunks?.length) return 0
    const midPos = rawResult.chunks.findIndex(c => c.chunk_index === rawResult.midpoint_chunk_index)
    return midPos >= 0 ? midPos : 0
  }, [selectedChunkPos, rawResult])

  const selectedChunk = rawResult?.chunks?.[chunkPos]
  const selectedChunkIndex = selectedChunk?.chunk_index ?? 0

  // Reconstruct court lines with samples for the selected chunk
  const displayCourtLines: Pass1CourtLine[] = useMemo(() => {
    if (!rawResult?.court_lines || !selectedChunk) return []
    const n = rawResult.perp_seg_points
    return rawResult.court_lines.map((line, li) => ({
      ...line,
      points: line.points.map((pt, pi) => ({
        ...pt,
        samples: (selectedChunk.vals[li]?.[pi] ?? []).map((val, j): Pass1Sample => ({
          s: n > 1 ? -1 + 2 * j / (n - 1) : 0,
          val,
        })),
      })),
    }))
  }, [rawResult, chunkPos])

  const pixPerSample = rawResult
    ? 2 * rawResult.perp_seg_length_px / (rawResult.perp_seg_points - 1)
    : 1

  // Per-line gradient limits: max |gradient| across all chunks and all segment points.
  const lineGradLimits: number[] = useMemo(() => {
    if (!rawResult?.court_lines) return []
    return rawResult.court_lines.map((_, li) => {
      let max = 0
      for (const chunk of rawResult.chunks ?? []) {
        const lineVals = chunk.vals[li]
        if (!lineVals) continue
        for (const ptVals of lineVals) {
          const m = maxAbsGrad(ptVals)
          if (m > max) max = m
        }
      }
      return max || 1
    })
  }, [rawResult])

  const { data: pass0ArtResp } = useQuery({
    queryKey: ['pass0-artifacts', projectId],
    queryFn: () => api.getPass0Artifacts(projectId!),
    enabled: !!rawResult,
  })
  const pass0Artifacts: ArtifactRef[] = pass0ArtResp?.data ?? []

  const cameraModelArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.path.endsWith('camera-model.json')
  )
  const { data: cameraModel } = useQuery<Pass1CameraModelEntry[]>({
    queryKey: ['pass1-camera-model', projectId, cameraModelArtifact?.id],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(cameraModelArtifact!.id))
      if (!resp.ok) throw new Error(`Artifact fetch failed: ${resp.status}`)
      return resp.json()
    },
    enabled: !!cameraModelArtifact,
  })

  const cameraEntry = useMemo(
    () => cameraModel?.find(e => e.chunk_id === selectedChunkIndex) ?? null,
    [cameraModel, selectedChunkIndex],
  )

  const medianTag = `median_${String(selectedChunkIndex).padStart(3, '0')}`
  const rawPngArtifact = rawResult
    ? pass0Artifacts.find((a) => a.artifact_type === 'png' && a.path.includes(medianTag))
    : undefined

  async function handleAccept() {
    setAccepting(true)
    setStatusMsg(null)
    try {
      await api.acceptPass1(projectId!)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setAccepting(false)
    }
  }

  const imgUrl = rawPngArtifact ? api.artifactUrl(rawPngArtifact.id) : null
  const bgW = rawResult?.bg_width ?? 1
  const bgH = rawResult?.bg_height ?? 1
  const cx = bgW / 2, cy = bgH / 2
  const camScale = Math.sqrt(cx * cx + cy * cy)
  const k1 = rawResult?.k1 ?? 0
  const chunkVertices = rawResult?.chunk_vertices?.[chunkPos]
  const totalSegments = (rawResult?.court_lines ?? []).reduce((n, l) => n + l.points.length, 0)

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>
      <h1>Pass 1 Review — Near-Baseline Court Outline</h1>

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {/* ── Left column: controls ── */}
        <div style={{ flex: '0 0 220px' }}>
          <p style={{ fontSize: 13, color: '#555', marginTop: 0 }}>
            Color median with ±{rawResult?.perp_seg_length_px ?? 64}&nbsp;px
            perpendicular profiles across the near-side court lines.
          </p>
          {rawResult && (
            <p style={{ fontSize: 12, color: '#777' }}>
              {totalSegments} segments · {rawResult.perp_seg_points} samples/seg
            </p>
          )}
          {rawResult?.court_lines?.map(l => (
            <div key={l.name} style={{ fontSize: 11, color: l.color, marginBottom: 2 }}>
              ■ {l.name}
            </div>
          ))}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 }}>
            <button
              onClick={handleAccept}
              disabled={accepting || !rawJsonArtifact}
              style={{
                padding: '8px 0', background: '#0a0', color: '#fff',
                border: 'none', borderRadius: 4,
                cursor: accepting || !rawJsonArtifact ? 'default' : 'pointer',
              }}
            >
              {accepting ? 'Accepting…' : 'Accept Pass 1 →'}
            </button>
            {statusMsg && (
              <p style={{ fontSize: 12, color: statusMsg.startsWith('Error') ? 'red' : 'green', margin: 0 }}>
                {statusMsg}
              </p>
            )}
          </div>
        </div>

        {/* ── Right column: image + overlay ── */}
        <div style={{ flex: '1 1 500px' }}>
          <h3 style={{ marginTop: 0 }}>Midpoint Color Median</h3>
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
            {imgUrl ? (
              <img src={imgUrl} alt="Color median" style={{ maxWidth: '100%', display: 'block' }} />
            ) : (
              <div style={{
                width: 600, height: 338, background: '#222',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555',
              }}>
                {rawResult ? 'Loading median image…' : 'No artifacts yet — pass 1 may still be running.'}
              </div>
            )}

            {rawResult && (rawResult.chunks?.length ?? 0) > 0 && (
              <div style={{
                position: 'absolute', bottom: 6, left: 6, right: 6,
                display: 'flex', alignItems: 'center', gap: 6,
                background: 'rgba(0,0,0,0.45)', borderRadius: 4, padding: '3px 6px',
                pointerEvents: 'auto',
              }}>
                <button
                  onClick={() => setSelectedChunkPos(null)}
                  disabled={selectedChunkPos === null}
                  style={{ fontSize: 10, padding: '1px 6px', whiteSpace: 'nowrap' }}
                >
                  Midpoint
                </button>
                <input
                  type="range"
                  min={0} max={(rawResult.chunks?.length ?? 1) - 1}
                  value={chunkPos}
                  onChange={e => setSelectedChunkPos(Number(e.target.value))}
                  style={{ flex: 1, minWidth: 0 }}
                />
                <span style={{ fontSize: 10, color: '#ddd', whiteSpace: 'nowrap' }}>
                  {selectedChunkIndex}{selectedChunkPos === null ? ' ●' : ''}
                </span>
              </div>
            )}

            {imgUrl && displayCourtLines.length > 0 && (
              <svg
                viewBox={`0 0 ${bgW} ${bgH}`}
                style={{
                  position: 'absolute', top: 0, left: 0,
                  width: '100%', height: '100%',
                  overflow: 'visible', pointerEvents: 'none',
                }}
              >
                {chunkVertices && cameraEntry?.img_corners_px && (
                  <CourtOutlineOverlay
                    kitchenLeft={chunkVertices.kitchen_left as [number, number] | null}
                    kitchenRight={chunkVertices.kitchen_right as [number, number] | null}
                    corners={cameraEntry.img_corners_px}
                    cx={cx} cy={cy} k1={k1} scale={camScale}
                  />
                )}
                {chunkVertices && (
                  <VertexOverlay vertices={chunkVertices} cx={cx} cy={cy} k1={k1} scale={camScale} />
                )}
                {displayCourtLines.map((line, li) =>
                  line.points.map((pt, pi) => {
                    const dx = pt.px1 - pt.px2
                    const dy = pt.py1 - pt.py2
                    const len = Math.sqrt(dx * dx + dy * dy) || 1
                    const ux = dx / len, uy = dy / len
                    // tangent direction (parallel to court line)
                    const tx = -uy, ty = ux
                    const AL = 14, AW = 7  // arrowhead length and half-width in image px
                    const bx = pt.px1 - AL * ux, by = pt.py1 - AL * uy
                    const arrowPts = `${pt.px1},${pt.py1} ${bx - AW * uy},${by + AW * ux} ${bx + AW * uy},${by - AW * ux}`
                    const label = String.fromCharCode(65 + pi)
                    const lx = pt.px2 - 12 * ux, ly = pt.py2 - 12 * uy
                    const pos = rawResult?.segment_analyses?.[li]?.[pi]?.positions?.[chunkPos]
                    const HL = 10  // half-length of lag marker line in image px
                    return (
                      <g key={`${line.name}-${pi}`} opacity={0.9}>
                        <line x1={pt.px1} y1={pt.py1} x2={pt.px2} y2={pt.py2}
                          stroke={line.color} strokeWidth={2} fill="none" />
                        <polygon points={arrowPts} fill={line.color} stroke="none" />
                        <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle"
                          fontSize={14} fontFamily="monospace" fontWeight="bold"
                          stroke="black" strokeWidth={3} paintOrder="stroke"
                          fill={line.color}>
                          {label}
                        </text>
                        {pos && (
                          <line
                            x1={pos[0] - HL * tx} y1={pos[1] - HL * ty}
                            x2={pos[0] + HL * tx} y2={pos[1] + HL * ty}
                            stroke={line.color} strokeWidth={2.5}
                            strokeLinecap="round"
                          />
                        )}
                      </g>
                    )
                  })
                )}
              </svg>
            )}
          </div>
        </div>
      </div>

      {/* ── Subplot grids, one per court line ── */}
      {displayCourtLines.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {displayCourtLines.map((line, li) => (
            <CourtLineGrid key={line.name} line={line}
              lineAnalyses={rawResult?.segment_analyses?.[li]}
              chunkPos={chunkPos}
              pixPerSample={pixPerSample}
              gradLimit={lineGradLimits[li] ?? 1} />
          ))}
        </div>
      )}
    </div>
  )
}
