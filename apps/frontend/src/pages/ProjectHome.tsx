import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useState } from 'react'
import { api } from '../api/client'
import { useProjectWebSocket } from '../api/ws'

const STATE_LABELS: Record<string, string> = {
  not_started: 'Not started',
  queued: 'Queued',
  running: 'Running…',
  waiting_for_user: 'Ready for review',
  accepted: 'Accepted',
  failed: 'Failed',
  cancelled: 'Not started',
}

const STATE_COLOR: Record<string, string> = {
  not_started: '#aaa',
  queued: '#f90',
  running: '#09f',
  waiting_for_user: '#0a0',
  accepted: '#090',
  failed: '#c00',
  cancelled: '#aaa',
}

export default function ProjectHome() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [progress, setProgress] = useState<{ passName: string; stage: string; fraction: number } | null>(null)

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
    refetchInterval: (query) => {
      const passes = query.state.data?.passes ?? []
      const anyInFlight = passes.some((p: { state: string }) =>
        p.state === 'queued' || p.state === 'running'
      )
      return anyInFlight ? 3000 : false
    },
  })

  useProjectWebSocket(projectId ?? null, (msg) => {
    if (msg.type === 'job_progress') {
      const p = { passName: msg.payload.pass_name as string, stage: msg.payload.stage as string, fraction: msg.payload.progress as number }
      console.log('[progress]', p.passName, p.stage, (p.fraction * 100).toFixed(1) + '%')
      setProgress(p)
    } else if (msg.type === 'pass_waiting_for_user' || msg.type === 'pass_accepted') {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    }
  })

  const runPass1 = useMutation({
    mutationFn: () => api.runPass1(projectId!),
    onSuccess: () => { setProgress(null); qc.invalidateQueries({ queryKey: ['project', projectId] }) },
  })

  const runPass2 = useMutation({
    mutationFn: () => api.runPass2(projectId!),
    onSuccess: () => {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['pass2-corrections', projectId] })
      sessionStorage.removeItem(`pass2-pos-${projectId}`)
    },
  })

  const runPass3 = useMutation({
    mutationFn: () => api.runPass3(projectId!),
    onSuccess: () => { setProgress(null); qc.invalidateQueries({ queryKey: ['project', projectId] }) },
  })

  const runPass4 = useMutation({
    mutationFn: () => api.runPass4(projectId!),
    onSuccess: () => {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const runPass5 = useMutation({
    mutationFn: () => api.runPass5(projectId!),
    onSuccess: () => {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const runPass6 = useMutation({
    mutationFn: () => api.runPass6(projectId!),
    onSuccess: () => {
      setProgress(null)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  if (isLoading || !project) return <div style={{ padding: 24 }}>Loading…</div>

  const pass1 = project.passes.find((p) => p.pass_name === 'pass1')
  const pass2 = project.passes.find((p) => p.pass_name === 'pass2')
  const pass3 = project.passes.find((p) => p.pass_name === 'pass3')
  const pass4 = project.passes.find((p) => p.pass_name === 'pass4')
  const pass5 = project.passes.find((p) => p.pass_name === 'pass5')
  const pass6 = project.passes.find((p) => p.pass_name === 'pass6')

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

      {/* Pass cards — shared logic:
            queued / running  → progress bar only (job in flight)
            not_started / failed / cancelled → Run button (prerequisites permitting)
            waiting_for_user  → Review → + Re-run
            accepted          → ✓ Accepted + Re-run
      */}

      {/* Pass 1 card */}
      <PassCard
        title="Pass 1 — Scene Calibration"
        description="Detects stable video bounds and generates a median background image for court alignment."
        pass={pass1}
        prereqMet={true}
        prereqLabel=""
        progress={progress?.passName === 'pass1' ? progress : null}
        onRun={() => runPass1.mutate()}
        isPending={runPass1.isPending}
        reviewPath={`/projects/${projectId}/pass1`}
        reviewLabel="Review →"
      />

      {/* Pass 2 card */}
      <PassCard
        title="Pass 2 — Ball Annotation"
        description="Manually mark ball positions frame by frame to build annotation data."
        pass={pass2}
        prereqMet={pass1?.state === 'accepted'}
        prereqLabel="Complete Pass 1 first."
        progress={progress?.passName === 'pass2' ? progress : null}
        onRun={() => runPass2.mutate()}
        isPending={runPass2.isPending}
        reviewPath={`/projects/${projectId}/pass2`}
        reviewLabel="Annotate →"
      />

      {/* Pass 3 card */}
      <PassCard
        title="Pass 3 — Ball Color Tagging"
        description="Samples per-pixel color data from annotated ball patches to build a ball color profile."
        pass={pass3}
        prereqMet={pass2?.state === 'accepted'}
        prereqLabel="Complete Pass 2 first."
        progress={progress?.passName === 'pass3' ? progress : null}
        onRun={() => runPass3.mutate()}
        isPending={runPass3.isPending}
        reviewPath={`/projects/${projectId}/pass3`}
        reviewLabel="Review →"
      />

      {/* Pass 4 card — also has pause/resume controls */}
      <PassCard
        title="Pass 4 — Ball Detection"
        description="Detects ball candidates in each frame using motion, color, and silhouette masks."
        pass={pass4}
        prereqMet={pass3?.state === 'accepted'}
        prereqLabel="Complete Pass 3 first."
        progress={progress?.passName === 'pass4' ? progress : null}
        onRun={() => runPass4.mutate()}
        isPending={runPass4.isPending}
        reviewPath={`/projects/${projectId}/pass4`}
        reviewLabel="Review →"
        extraControls={
          progress?.passName === 'pass4' && (pass4?.state === 'running' || pass4?.state === 'queued') ? (
            <button
              onClick={() => progress.stage === 'paused'
                ? api.resumePass4(projectId!)
                : api.pausePass4(projectId!)}
              style={{ marginTop: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12 }}
            >
              {progress.stage === 'paused' ? 'Resume' : 'Pause'}
            </button>
          ) : null
        }
      />

      {/* Pass 5 card */}
      <PassCard
        title="Pass 5 — Segment Building"
        description="Groups ball detections in consecutive frames into trajectory segments."
        pass={pass5}
        prereqMet={pass4?.state === 'accepted'}
        prereqLabel="Complete Pass 4 first."
        progress={progress?.passName === 'pass5' ? progress : null}
        onRun={() => runPass5.mutate()}
        isPending={runPass5.isPending}
        reviewPath={`/projects/${projectId}/pass5`}
        reviewLabel="Review →"
      />

      {/* Pass 6 card */}
      <PassCard
        title="Pass 6 — Video Export"
        description="Concatenates rally segments into a highlight reel with chapter markers, preserving source quality."
        pass={pass6}
        prereqMet={pass2?.state === 'accepted'}
        prereqLabel="Complete Pass 2 first."
        progress={progress?.passName === 'pass6' ? progress : null}
        onRun={() => runPass6.mutate()}
        isPending={runPass6.isPending}
        reviewPath={`/projects/${projectId}/pass6`}
        reviewLabel="Review →"
      />
    </div>
  )
}

// ─── Shared pass card ─────────────────────────────────────────────────────────

const IN_FLIGHT = new Set(['queued', 'running'])
const RUNNABLE  = new Set(['not_started', 'failed', 'cancelled'])

interface PassCardProps {
  title: string
  description: string
  pass: { state: string } | undefined
  prereqMet: boolean
  prereqLabel: string
  progress: { stage: string; fraction: number } | null
  onRun: () => void
  isPending: boolean
  reviewPath: string
  reviewLabel: string
  extraControls?: React.ReactNode
}

function PassCard({
  title, description, pass, prereqMet, prereqLabel,
  progress, onRun, isPending, reviewPath, reviewLabel, extraControls,
}: PassCardProps) {
  const navigate = useNavigate()
  const state = pass?.state ?? 'not_started'
  const inFlight = IN_FLIGHT.has(state)

  const rerunBtn = (
    <button
      onClick={onRun}
      disabled={isPending}
      style={{ padding: '6px 16px', cursor: 'pointer', fontSize: 12, color: '#888', border: '1px solid #ccc', borderRadius: 4, background: '#fff' }}
    >
      {isPending ? 'Queuing…' : 'Re-run'}
    </button>
  )

  return (
    <div style={{
      border: '1px solid #ddd', borderRadius: 8, padding: 16, marginBottom: 16,
      opacity: prereqMet ? 1 : 0.4,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <span style={{ color: STATE_COLOR[state] ?? '#aaa', fontWeight: 'bold' }}>
          {STATE_LABELS[state] ?? state}
        </span>
      </div>
      <p style={{ color: '#555', fontSize: 14 }}>{description}</p>

      {/* Progress bar — shown while job is in flight */}
      {inFlight && progress && (
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
          {extraControls}
        </div>
      )}

      <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
        {!inFlight && prereqMet && RUNNABLE.has(state) && (
          <button
            onClick={onRun}
            disabled={isPending}
            style={{ padding: '6px 16px', cursor: 'pointer' }}
          >
            {isPending ? 'Queuing…' : state === 'not_started' ? `Run ${title.split('—')[0].trim()}` : 'Re-run'}
          </button>
        )}
        {/* Review button: always shown when waiting_for_user, or while paused (partial results available) */}
        {((!inFlight && state === 'waiting_for_user') || progress?.stage === 'paused') && (
          <button
            onClick={() => navigate(reviewPath)}
            style={{ padding: '6px 16px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
          >
            {reviewLabel}
          </button>
        )}
        {!inFlight && state === 'accepted' && (
          <span style={{ color: '#090' }}>✓ Accepted</span>
        )}
        {!inFlight && prereqMet && (state === 'waiting_for_user' || state === 'accepted') && rerunBtn}
        {!prereqMet && (
          <span style={{ color: '#aaa', fontSize: 14 }}>{prereqLabel}</span>
        )}
      </div>
    </div>
  )
}
