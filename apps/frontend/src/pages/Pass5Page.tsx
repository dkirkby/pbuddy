import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { Pass5Segment } from '../types/api'

export default function Pass5Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const playerRef = useRef<VideoPlayerHandle>(null)
  const tableRef = useRef<HTMLDivElement>(null)
  const [accepting, setAccepting] = useState(false)
  const [currentFrame, setCurrentFrame] = useState(0)

  const { data: segData, isLoading } = useQuery({
    queryKey: ['pass5-segments', projectId],
    queryFn: () => api.getPass5Segments(projectId!),
  })

  // Pass 4 detections for ball overlay.
  const { data: detectionsData } = useQuery({
    queryKey: ['pass4-detections', projectId],
    queryFn: () => api.getPass4Detections(projectId!),
  })

  // Pass 2 result for fps and frame dimensions.
  const { data: pass2Meta } = useQuery({
    queryKey: ['pass2-result-meta', projectId],
    queryFn: async () => {
      const artResp = await api.getPass2Artifacts(projectId!)
      const resultArt = artResp.data.find(
        (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
      )
      if (!resultArt) return null
      const resp = await fetch(api.artifactUrl(resultArt.id))
      return resp.json()
    },
  })

  const acceptPass5 = useMutation({
    mutationFn: () => api.acceptPass5(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const segments: Pass5Segment[] = segData?.segments ?? []

  // Per-frame ball detection index (OpenCV frame numbers).
  const ballDetections = useMemo(() => {
    if (!detectionsData?.detections) return {}
    const out: Record<number, { cx: number; cy: number; radius: number }[]> = {}
    for (const d of detectionsData.detections) {
      if (!out[d.frame]) out[d.frame] = []
      out[d.frame].push({ cx: d.cx, cy: d.cy, radius: d.radius })
    }
    return out
  }, [detectionsData])

  // Segment paths for polyline rendering.
  const segmentPaths = useMemo(() =>
    segments.map(seg => ({ id: seg.id, detections: seg.detections })),
  [segments])

  // Segments visible at the current frame: those that contain it or have an endpoint within 8 frames.
  const visibleSegmentIds = useMemo(() => {
    const currentFi = currentFrame - 1  // convert browser→OpenCV numbering
    const ids = new Set<number>()
    for (const seg of segments) {
      const inSeg = currentFi >= seg.first_frame && currentFi <= seg.last_frame
      const nearStart = Math.abs(seg.first_frame - currentFi) <= 8
      const nearEnd = Math.abs(seg.last_frame - currentFi) <= 8
      if (inSeg || nearStart || nearEnd) ids.add(seg.id)
    }
    return ids
  }, [segments, currentFrame])

  // Auto-scroll the table so the first highlighted row has at most 3 rows above it visible.
  useEffect(() => {
    const container = tableRef.current
    if (!container) return
    const firstIdx = segments.findIndex(seg => visibleSegmentIds.has(seg.id))
    if (firstIdx < 0) return
    const rows = container.querySelectorAll('tbody tr')
    const targetRow = rows[firstIdx] as HTMLElement | undefined
    if (!targetRow) return
    const rowHeight = targetRow.offsetHeight || 24
    container.scrollTop = Math.max(0, (firstIdx - 3) * rowHeight)
  }, [visibleSegmentIds, segments])

  const fps = pass2Meta?.fps ?? 30
  const bgWidth = pass2Meta?.bg_width ?? 960
  const bgHeight = pass2Meta?.bg_height ?? 540

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Pass 5 — Segment Building</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {segData && (
            <span style={{ fontSize: 13, color: '#555' }}>
              {segData.segment_count} segment{segData.segment_count !== 1 ? 's' : ''}
              {' · '}gap ≤ {segData.max_gap_frames} frames
              {' · '}gate {segData.large_gate_px}px → {segData.small_gate_px}px
              {' · '}min length {segData.min_segment_length}
            </span>
          )}
          <button
            onClick={() => { setAccepting(true); acceptPass5.mutate() }}
            disabled={accepting || acceptPass5.isPending || !segData}
            style={{ padding: '6px 18px', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
          >
            {accepting ? 'Accepting…' : 'Accept Pass 5 →'}
          </button>
        </div>
      </div>

      <p style={{ color: '#666', fontSize: 13, margin: '0 0 12px' }}>
        Magenta circle: ball detection in current frame.
        Cyan polyline: segments containing the current frame or with an endpoint within 8 frames (full segment shown).
      </p>

      <VideoPlayer
        ref={playerRef}
        videoUrl={api.videoUrl(projectId!)}
        fps={fps}
        bgWidth={bgWidth}
        bgHeight={bgHeight}
        totalFrames={0}
        ballDetections={ballDetections}
        segmentPaths={segmentPaths}
        onFrameChange={setCurrentFrame}
        storageKey={`pass5-pos-${projectId}`}
      />

      {isLoading ? (
        <div style={{ color: '#888', marginTop: 16 }}>Loading segments…</div>
      ) : segments.length === 0 ? (
        <div style={{ color: '#888', marginTop: 16 }}>No segments found.</div>
      ) : (
        <div ref={tableRef} style={{ maxHeight: 280, overflowY: 'auto', marginTop: 16 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
            <thead style={{ position: 'sticky', top: 0, background: '#f0f0f0', zIndex: 1 }}>
              <tr>
                <th style={th}>#</th>
                <th style={th}>First frame</th>
                <th style={th}>Last frame</th>
                <th style={th}>Detections</th>
                <th style={th}>Span (frames)</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((seg) => {
                const highlighted = visibleSegmentIds.has(seg.id)
                return (
                  <tr
                    key={seg.id}
                    style={{ borderBottom: '1px solid #eee', cursor: 'pointer', background: highlighted ? '#def' : undefined }}
                    onClick={() => playerRef.current?.seekToFrame(seg.first_frame + 1)}
                    title="Click to seek to segment start"
                  >
                    <td style={td}>{seg.id + 1}</td>
                    <td style={td}>{seg.first_frame}</td>
                    <td style={td}>{seg.last_frame}</td>
                    <td style={td}>{seg.length}</td>
                    <td style={td}>{seg.last_frame - seg.first_frame + 1}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '4px 12px', textAlign: 'right', fontWeight: 600, color: '#333' }
const td: React.CSSProperties = { padding: '3px 12px', textAlign: 'right', color: '#444' }
