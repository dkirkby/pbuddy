import { useMemo, useRef, useState } from 'react'
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
  const [accepting, setAccepting] = useState(false)

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

  // Segment endpoint index: frame number → list of {cx, cy, id, isStart}.
  // Uses the first/last detection in each segment for position.
  const segmentEndpoints = useMemo(() => {
    const out: Record<number, { cx: number; cy: number; id: number; isStart: boolean }[]> = {}
    for (const seg of segments) {
      const first = seg.detections[0]
      const last = seg.detections[seg.detections.length - 1]
      if (first) {
        if (!out[first.frame]) out[first.frame] = []
        out[first.frame].push({ cx: first.cx, cy: first.cy, id: seg.id, isStart: true })
      }
      if (last && last.frame !== first?.frame) {
        if (!out[last.frame]) out[last.frame] = []
        out[last.frame].push({ cx: last.cx, cy: last.cy, id: seg.id, isStart: false })
      }
    }
    return out
  }, [segments])

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
              {' · '}dist ≤ {segData.max_pixels_per_frame} px/frame
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
        Cyan/magenta circles: ball detections within ±8 frames.
        Green diamonds: segment starts · Orange diamonds: segment ends (within ±8 frames, labelled by segment number).
      </p>

      <VideoPlayer
        ref={playerRef}
        videoUrl={api.videoUrl(projectId!)}
        fps={fps}
        bgWidth={bgWidth}
        bgHeight={bgHeight}
        totalFrames={0}
        ballDetections={ballDetections}
        segmentEndpoints={segmentEndpoints}
        storageKey={`pass5-pos-${projectId}`}
      />

      {isLoading ? (
        <div style={{ color: '#888', marginTop: 16 }}>Loading segments…</div>
      ) : segments.length === 0 ? (
        <div style={{ color: '#888', marginTop: 16 }}>No segments found.</div>
      ) : (
        <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%', marginTop: 16 }}>
          <thead>
            <tr style={{ background: '#f0f0f0' }}>
              <th style={th}>#</th>
              <th style={th}>First frame</th>
              <th style={th}>Last frame</th>
              <th style={th}>Detections</th>
              <th style={th}>Span (frames)</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((seg) => (
              <tr
                key={seg.id}
                style={{ borderBottom: '1px solid #eee', cursor: 'pointer' }}
                onClick={() => playerRef.current?.seekToFrame(seg.first_frame + 1)}
                title="Click to seek to segment start"
              >
                <td style={td}>{seg.id + 1}</td>
                <td style={td}>{seg.first_frame}</td>
                <td style={td}>{seg.last_frame}</td>
                <td style={td}>{seg.length}</td>
                <td style={td}>{seg.last_frame - seg.first_frame + 1}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '4px 12px', textAlign: 'right', fontWeight: 600, color: '#333' }
const td: React.CSSProperties = { padding: '3px 12px', textAlign: 'right', color: '#444' }
