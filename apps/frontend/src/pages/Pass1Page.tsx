import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ArtifactRef, Pass1CourtLine, Pass1RawResult, Pass1Sample, Pass1SamplePoint } from '../types/api'

// ─── Subplot ──────────────────────────────────────────────────────────────────

const PLOT_W = 100
const PLOT_H = 33
const PAD = { top: 2, right: 2, bottom: 2, left: 2 }
const INNER_W = PLOT_W - PAD.left - PAD.right
const INNER_H = PLOT_H - PAD.top - PAD.bottom

// Max gradient of a Gaussian-blurred step with amplitude 128 and sigma=2 px.
const GAUSS_SIGMA = 2
const GRAD_LIMIT = 128 / (Math.sqrt(2 * Math.PI) * GAUSS_SIGMA)

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

function SegmentPlot({ pt, index, color, midpointSamples }: {
  pt: Pass1SamplePoint; index: number; color: string
  midpointSamples?: Pass1Sample[]
}) {
  if (!pt.samples?.length) return null

  const toX = (s: number) => ((s + 1) / 2) * INNER_W
  const toY = (v: number) => (INNER_H / 2) * (1 - v / GRAD_LIMIT)
  const pts = (samples: Pass1Sample[]) =>
    samples.map(s => `${toX(s.s).toFixed(1)},${toY(s.val).toFixed(1)}`).join(' ')
  const xMid = toX(0)
  const yZero = toY(0)
  const label = String.fromCharCode(65 + index)
  const grad = npGradient(pt.samples)
  const refGrad = midpointSamples && midpointSamples.length > 0 ? npGradient(midpointSamples) : null

  return (
    <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <g transform={`translate(${PAD.left},${PAD.top})`}>
        <rect x={0} y={0} width={INNER_W} height={INNER_H} fill="#111" stroke="#444" strokeWidth={0.5} />
        <line x1={0} y1={yZero} x2={INNER_W} y2={yZero} stroke="#555" strokeWidth={0.5} strokeDasharray="2,2" />
        <line x1={xMid} y1={0} x2={xMid} y2={INNER_H} stroke="#555" strokeWidth={0.5} strokeDasharray="2,2" />
        {refGrad && (
          <polyline points={pts(refGrad)} fill="none" stroke={color} strokeWidth={0.75} strokeOpacity={0.5} strokeDasharray="2,2" />
        )}
        <polyline points={pts(grad)} fill="none" stroke={color} strokeWidth={1} />
        <text x={2} y={INNER_H - 2} fontSize={7} fill={color} opacity={0.75}>{label}</text>
      </g>
    </svg>
  )
}

function CourtLineGrid({ line, midpointLine }: { line: Pass1CourtLine; midpointLine?: Pass1CourtLine }) {
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
            return (
              <SegmentPlot key={i} pt={pt} index={absIdx} color={line.color}
                midpointSamples={midpointLine?.points[absIdx]?.samples} />
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
    queryKey: ['pass1-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

  // Resolve effective chunk position: null means use midpoint
  const chunkPos = useMemo(() => {
    if (selectedChunkPos !== null) return selectedChunkPos
    if (!rawResult) return 0
    const midPos = rawResult.chunks.findIndex(c => c.chunk_index === rawResult.midpoint_chunk_index)
    return midPos >= 0 ? midPos : 0
  }, [selectedChunkPos, rawResult])

  const selectedChunk = rawResult?.chunks[chunkPos]
  const selectedChunkIndex = selectedChunk?.chunk_index ?? 0

  // Reconstruct court lines with samples for the selected chunk
  const displayCourtLines: Pass1CourtLine[] = useMemo(() => {
    if (!rawResult || !selectedChunk) return []
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

  const refCourtLines: Pass1CourtLine[] = useMemo(() => {
    if (!rawResult) return []
    const midPos = rawResult.chunks.findIndex(c => c.chunk_index === rawResult.midpoint_chunk_index)
    if (midPos < 0 || chunkPos === midPos) return []
    const refPos = chunkPos > midPos ? chunkPos - 1 : chunkPos + 1
    const refChunk = rawResult.chunks[refPos]
    if (!refChunk) return []
    const n = rawResult.perp_seg_points
    return rawResult.court_lines.map((line, li) => ({
      ...line,
      points: line.points.map((pt, pi) => ({
        ...pt,
        samples: (refChunk.vals[li]?.[pi] ?? []).map((val, j): Pass1Sample => ({
          s: n > 1 ? -1 + 2 * j / (n - 1) : 0,
          val,
        })),
      })),
    }))
  }, [rawResult, chunkPos])

  const { data: pass0ArtResp } = useQuery({
    queryKey: ['pass0-artifacts', projectId],
    queryFn: () => api.getPass0Artifacts(projectId!),
    enabled: !!rawResult,
  })
  const pass0Artifacts: ArtifactRef[] = pass0ArtResp?.data ?? []

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
          {rawResult && rawResult.court_lines.map(l => (
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

            {rawResult && rawResult.chunks.length > 0 && (
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
                  min={0} max={rawResult.chunks.length - 1}
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
                {displayCourtLines.map(line =>
                  line.points.map((pt, i) => {
                    const dx = pt.px1 - pt.px2
                    const dy = pt.py1 - pt.py2
                    const len = Math.sqrt(dx * dx + dy * dy) || 1
                    const ux = dx / len, uy = dy / len
                    const AL = 14, AW = 7  // arrowhead length and half-width in image px
                    const bx = pt.px1 - AL * ux, by = pt.py1 - AL * uy
                    const arrowPts = `${pt.px1},${pt.py1} ${bx - AW * uy},${by + AW * ux} ${bx + AW * uy},${by - AW * ux}`
                    const label = String.fromCharCode(65 + i)
                    const lx = pt.px2 - 12 * ux, ly = pt.py2 - 12 * uy
                    return (
                      <g key={`${line.name}-${i}`} opacity={0.9}>
                        <line x1={pt.px1} y1={pt.py1} x2={pt.px2} y2={pt.py2}
                          stroke={line.color} strokeWidth={2} fill="none" />
                        <polygon points={arrowPts} fill={line.color} stroke="none" />
                        <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle"
                          fontSize={14} fontFamily="monospace" fontWeight="bold"
                          stroke="black" strokeWidth={3} paintOrder="stroke"
                          fill={line.color}>
                          {label}
                        </text>
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
              midpointLine={refCourtLines[li]} />
          ))}
        </div>
      )}
    </div>
  )
}
