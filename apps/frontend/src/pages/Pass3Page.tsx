import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Pass3Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const hsvMaskUrl = `/api/projects/${projectId}/passes/pass3/raw/HSVmask.png`

  const accept = useMutation({
    mutationFn: () => api.acceptPass3(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  return (
    <div style={{ padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Project
      </button>
      <h1 style={{ marginTop: 0 }}>Pass 3 — Ball Color Tagging</h1>

      <img src={hsvMaskUrl} style={{ display: 'block', maxWidth: '100%', marginBottom: 24 }} />

      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <button
          onClick={() => accept.mutate()}
          disabled={accept.isPending}
          style={{ padding: '8px 20px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
        >
          {accept.isPending ? 'Accepting…' : 'Accept'}
        </button>
        {accept.isError && (
          <span style={{ color: '#c00', fontSize: 14 }}>
            {(accept.error as Error)?.message}
          </span>
        )}
      </div>
    </div>
  )
}
