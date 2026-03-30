import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Pass5Segment } from '../types/api'

export default function Pass5Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [accepting, setAccepting] = useState(false)

  const { data: segData, isLoading } = useQuery({
    queryKey: ['pass5-segments', projectId],
    queryFn: () => api.getPass5Segments(projectId!),
  })

  const acceptPass5 = useMutation({
    mutationFn: () => api.acceptPass5(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const segments: Pass5Segment[] = segData?.segments ?? []

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
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

      {isLoading ? (
        <div style={{ color: '#888' }}>Loading segments…</div>
      ) : segments.length === 0 ? (
        <div style={{ color: '#888' }}>No segments found.</div>
      ) : (
        <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
          <thead>
            <tr style={{ background: '#f0f0f0' }}>
              <th style={th}>#</th>
              <th style={th}>First frame</th>
              <th style={th}>Last frame</th>
              <th style={th}>Length (frames)</th>
              <th style={th}>Duration (frames)</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((seg) => (
              <tr key={seg.id} style={{ borderBottom: '1px solid #eee' }}>
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
