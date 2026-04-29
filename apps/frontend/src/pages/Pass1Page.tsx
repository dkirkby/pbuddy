import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ArtifactRef, Pass1CourtLine, Pass1RawResult, Pass1SamplePoint } from '../types/api'

// ─── Subplot ──────────────────────────────────────────────────────────────────

const PLOT_W = 140
const PLOT_H = 90
const PAD = { top: 6, right: 6, bottom: 18, left: 28 }
const INNER_W = PLOT_W - PAD.left - PAD.right
const INNER_H = PLOT_H - PAD.top - PAD.bottom

function SegmentPlot({ pt, index, color }: { pt: Pass1SamplePoint; index: number; color: string }) {
  if (!pt.samples?.length) return null

  const toX = (s: number) => ((s + 1) / 2) * INNER_W
  const toY = (v: number) => INNER_H - (v / 255) * INNER_H

  const points = pt.samples.map(s => `${toX(s.s).toFixed(1)},${toY(s.val).toFixed(1)}`).join(' ')
  const xMid = toX(0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} style={{ width: '100%', height: 'auto', display: 'block', overflow: 'visible' }}>
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          <rect x={0} y={0} width={INNER_W} height={INNER_H}
            fill="#111" stroke="#444" strokeWidth={0.5} />
          <line x1={xMid} y1={0} x2={xMid} y2={INNER_H}
            stroke="#555" strokeWidth={0.5} strokeDasharray="2,2" />
          <polyline points={points} fill="none" stroke={color} strokeWidth={1} />
          <text x={0}       y={INNER_H + 12} textAnchor="middle" fontSize={8} fill="#888">-1</text>
          <text x={xMid}    y={INNER_H + 12} textAnchor="middle" fontSize={8} fill="#888">0</text>
          <text x={INNER_W} y={INNER_H + 12} textAnchor="middle" fontSize={8} fill="#888">+1</text>
          <text x={-3} y={INNER_H} textAnchor="end" fontSize={7} fill="#888">0</text>
          <text x={-3} y={6}       textAnchor="end" fontSize={7} fill="#888">255</text>
        </g>
      </svg>
      <div style={{ fontSize: 10, color: '#888', marginTop: 1 }}>seg {index + 1}</div>
    </div>
  )
}

function CourtLineGrid({ line }: { line: Pass1CourtLine }) {
  const ROW = 4
  const rows: Pass1SamplePoint[][] = []
  for (let i = 0; i < line.points.length; i += ROW)
    rows.push(line.points.slice(i, i + ROW))

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 12, color: line.color, marginBottom: 6, fontFamily: 'monospace' }}>
        {line.name} ({line.points.length} segments)
      </div>
      {rows.map((row, ri) => (
        <div key={ri} style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${ROW}, 1fr)`,
          gap: 8,
          marginBottom: rows.length > 1 ? 8 : 0,
        }}>
          {row.map((pt, i) => (
            <SegmentPlot key={i} pt={pt} index={ri * ROW + i} color={line.color} />
          ))}
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

  const { data: artResp } = useQuery({
    queryKey: ['pass1-artifacts', projectId],
    queryFn: () => api.getPass1Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []

  const rawJsonArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )
  const rawPngArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'png' && a.path.includes('single_channel')
  )

  const { data: rawResult } = useQuery<Pass1RawResult>({
    queryKey: ['pass1-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

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
  const courtLines = rawResult?.court_lines ?? []
  const bgW = rawResult?.bg_width ?? 1
  const bgH = rawResult?.bg_height ?? 1

  const totalSegments = courtLines.reduce((n, l) => n + l.points.length, 0)

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
            Single-channel image (V&nbsp;−&nbsp;S/2) with ±{rawResult?.perp_seg_length_px ?? 64}&nbsp;px
            perpendicular profiles across the near baseline and near sidelines.
          </p>
          {rawResult && (
            <p style={{ fontSize: 12, color: '#777' }}>
              {totalSegments} segments · {rawResult.perp_seg_points} samples/seg · chunk {rawResult.median_chunk_index}
            </p>
          )}
          {rawResult && courtLines.map(l => (
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
          <h3 style={{ marginTop: 0 }}>Single-Channel Median (V − S/2)</h3>
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
            {imgUrl ? (
              <img src={imgUrl} alt="Single-channel median" style={{ maxWidth: '100%', display: 'block' }} />
            ) : (
              <div style={{
                width: 600, height: 338, background: '#222',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555',
              }}>
                {rawJsonArtifact ? 'Loading image…' : 'No artifacts yet — pass 1 may still be running.'}
              </div>
            )}

            {imgUrl && courtLines.length > 0 && (
              <svg
                viewBox={`0 0 ${bgW} ${bgH}`}
                style={{
                  position: 'absolute', top: 0, left: 0,
                  width: '100%', height: '100%',
                  overflow: 'visible', pointerEvents: 'none',
                }}
              >
                {courtLines.map(line =>
                  line.points.map((pt, i) => (
                    <line
                      key={`${line.name}-${i}`}
                      x1={pt.px1} y1={pt.py1} x2={pt.px2} y2={pt.py2}
                      stroke={line.color} strokeWidth={2} strokeOpacity={0.85}
                    />
                  ))
                )}
              </svg>
            )}
          </div>
        </div>
      </div>

      {/* ── Subplot grids, one per court line ── */}
      {courtLines.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h3 style={{ marginTop: 0 }}>V − S/2 Profiles Along Each Perpendicular Segment</h3>
          {courtLines.map(line => (
            <CourtLineGrid key={line.name} line={line} />
          ))}
        </div>
      )}
    </div>
  )
}
