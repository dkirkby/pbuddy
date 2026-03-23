import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useState } from 'react'
import { api } from '../api/client'
import { useProjectWebSocket } from '../api/ws'

const STATE_LABELS: Record<string, string> = {
  not_started: 'Not started',
  queued: 'Queued',
  running: 'Running…',
  produced_raw_output: 'Processing…',
  waiting_for_user: 'Ready for review',
  accepted: 'Accepted',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const STATE_COLOR: Record<string, string> = {
  not_started: '#aaa',
  queued: '#f90',
  running: '#09f',
  waiting_for_user: '#0a0',
  accepted: '#090',
  failed: '#c00',
}

export default function ProjectHome() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [progress, setProgress] = useState<{ stage: string; fraction: number } | null>(null)

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status?.includes('waiting') || status?.includes('running') || status?.includes('queued'))
        return 3000
      return false
    },
  })

  useProjectWebSocket(projectId ?? null, (msg) => {
    if (msg.type === 'job_progress') {
      setProgress({
        stage: msg.payload.stage as string,
        fraction: msg.payload.progress as number,
      })
    } else if (msg.type === 'pass_waiting_for_user' || msg.type === 'pass_accepted') {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    }
  })

  const runPass1 = useMutation({
    mutationFn: () => api.runPass1(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['project', projectId] }),
  })

  const runPass2 = useMutation({
    mutationFn: () => api.runPass2(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['project', projectId] }),
  })

  if (isLoading || !project) return <div style={{ padding: 24 }}>Loading…</div>

  const pass1 = project.passes.find((p) => p.pass_name === 'pass1')
  const pass2 = project.passes.find((p) => p.pass_name === 'pass2')

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate('/')} style={{ marginBottom: 16 }}>← Projects</button>
      <h1>{project.name}</h1>
      <div style={{ color: '#666', marginBottom: 16 }}>
        Status: <strong>{project.status}</strong>
        {project.video_duration_s && (
          <span> · {Math.round(project.video_duration_s / 60)} min · {project.video_width}×{project.video_height}</span>
        )}
      </div>

      {/* Pass 1 card */}
      <div style={{ border: '1px solid #ddd', borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0 }}>Pass 1 — Scene Calibration</h2>
          <span style={{ color: STATE_COLOR[pass1?.state ?? ''] ?? '#aaa', fontWeight: 'bold' }}>
            {STATE_LABELS[pass1?.state ?? 'not_started']}
          </span>
        </div>
        <p style={{ color: '#555', fontSize: 14 }}>
          Detects stable video bounds and generates a median background image for court alignment.
        </p>

        {/* Progress bar */}
        {progress && (pass1?.state === 'running' || pass1?.state === 'queued') && (
          <div style={{ margin: '8px 0' }}>
            <div style={{ background: '#eee', borderRadius: 4, height: 8 }}>
              <div style={{
                width: `${Math.round(progress.fraction * 100)}%`,
                background: '#09f', borderRadius: 4, height: 8,
                transition: 'width 0.3s',
              }} />
            </div>
            <div style={{ fontSize: 12, color: '#555', marginTop: 4 }}>
              {progress.stage} — {Math.round(progress.fraction * 100)}%
            </div>
          </div>
        )}

        <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          {(pass1?.state === 'not_started' || pass1?.state === 'failed') && (
            <button
              onClick={() => runPass1.mutate()}
              disabled={runPass1.isPending}
              style={{ padding: '6px 16px', cursor: 'pointer' }}
            >
              {runPass1.isPending ? 'Queuing…' : 'Run Pass 1'}
            </button>
          )}
          {pass1?.state === 'waiting_for_user' && (
            <button
              onClick={() => navigate(`/projects/${projectId}/pass1`)}
              style={{ padding: '6px 16px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
            >
              Review →
            </button>
          )}
          {pass1?.state === 'accepted' && (
            <span style={{ color: '#090' }}>✓ Accepted</span>
          )}
          {(pass1?.state === 'waiting_for_user' || pass1?.state === 'accepted') && (
            <button
              onClick={() => runPass1.mutate()}
              disabled={runPass1.isPending}
              style={{ padding: '6px 16px', cursor: 'pointer', fontSize: 12, color: '#888', border: '1px solid #ccc', borderRadius: 4, background: '#fff' }}
            >
              {runPass1.isPending ? 'Queuing…' : 'Re-run'}
            </button>
          )}
        </div>
      </div>

      {/* Pass 2 card */}
      <div style={{
        border: '1px solid #ddd', borderRadius: 8, padding: 16, marginBottom: 16,
        opacity: pass1?.state === 'accepted' ? 1 : 0.4,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0 }}>Pass 2 — Ball Annotation</h2>
          <span style={{ color: STATE_COLOR[pass2?.state ?? ''] ?? '#aaa', fontWeight: 'bold' }}>
            {STATE_LABELS[pass2?.state ?? 'not_started']}
          </span>
        </div>
        <p style={{ color: '#555', fontSize: 14 }}>
          Manually mark ball positions frame by frame to build annotation data.
        </p>

        {progress && (pass2?.state === 'running' || pass2?.state === 'queued') && (
          <div style={{ margin: '8px 0' }}>
            <div style={{ background: '#eee', borderRadius: 4, height: 8 }}>
              <div style={{
                width: `${Math.round(progress.fraction * 100)}%`,
                background: '#09f', borderRadius: 4, height: 8, transition: 'width 0.3s',
              }} />
            </div>
            <div style={{ fontSize: 12, color: '#555', marginTop: 4 }}>
              {progress.stage} — {Math.round(progress.fraction * 100)}%
            </div>
          </div>
        )}

        <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          {pass1?.state === 'accepted' && (pass2?.state === 'not_started' || pass2?.state === 'failed') && (
            <button
              onClick={() => runPass2.mutate()}
              disabled={runPass2.isPending}
              style={{ padding: '6px 16px', cursor: 'pointer' }}
            >
              {runPass2.isPending ? 'Queuing…' : 'Run Pass 2'}
            </button>
          )}
          {pass2?.state === 'waiting_for_user' && (
            <button
              onClick={() => navigate(`/projects/${projectId}/pass2`)}
              style={{ padding: '6px 16px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
            >
              Annotate →
            </button>
          )}
          {pass2?.state === 'accepted' && (
            <span style={{ color: '#090' }}>✓ Accepted</span>
          )}
          {(pass2?.state === 'waiting_for_user' || pass2?.state === 'accepted') && pass1?.state === 'accepted' && (
            <button
              onClick={() => runPass2.mutate()}
              disabled={runPass2.isPending}
              style={{ padding: '6px 16px', cursor: 'pointer', fontSize: 12, color: '#888', border: '1px solid #ccc', borderRadius: 4, background: '#fff' }}
            >
              {runPass2.isPending ? 'Queuing…' : 'Re-run'}
            </button>
          )}
          {pass1?.state !== 'accepted' && (
            <span style={{ color: '#aaa', fontSize: 14 }}>Complete Pass 1 first.</span>
          )}
        </div>
      </div>

      {/* Passes 3–4: locked until pass2 accepted */}
      {(['pass3', 'pass4'] as const).map((pn, i) => (
        <div
          key={pn}
          style={{
            border: '1px solid #eee', borderRadius: 8, padding: 16, marginBottom: 16,
            opacity: pass2?.state === 'accepted' ? 1 : 0.4,
          }}
        >
          <h2 style={{ margin: 0 }}>
            Pass {i + 3} — {['Player & Ball Tracking', '3D Reconstruction'][i]}
          </h2>
          <p style={{ color: '#888', fontSize: 14, margin: '8px 0 0' }}>
            {pass2?.state !== 'accepted' ? 'Complete Pass 2 first.' : 'Not yet implemented.'}
          </p>
        </div>
      ))}
    </div>
  )
}
