import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useRef, useState } from 'react'
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

const DIRTY_COLOR = '#f90'

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

  const runPass0 = useMutation({
    mutationFn: () => api.runPass0(projectId!),
    onSuccess: () => { setProgress(null); qc.invalidateQueries({ queryKey: ['project', projectId] }) },
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

  const resetPass2 = useMutation({
    mutationFn: () => api.resetPass2(projectId!),
    onSuccess: () => {
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

  const reuploadRef = useRef<HTMLInputElement>(null)
  const reupload = useMutation({
    mutationFn: (file: File) => api.uploadVideo(projectId!, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['project', projectId] }),
  })

  if (isLoading || !project) return <div style={{ padding: 24 }}>Loading…</div>

  const pass0 = project.passes.find((p) => p.pass_name === 'pass0')
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
      {!project.video_duration_s && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: '#fff8e1', border: '1px solid #f0c000', borderRadius: 6 }}>
          <span style={{ fontSize: 13, color: '#7a5c00', marginRight: 12 }}>
            No video metadata — upload failed or video was never attached.
          </span>
          <input
            ref={reuploadRef}
            type="file"
            accept="video/*"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) reupload.mutate(file)
              e.target.value = ''
            }}
          />
          <button
            onClick={() => reuploadRef.current?.click()}
            disabled={reupload.isPending}
            style={{ fontSize: 13, padding: '3px 12px', cursor: 'pointer' }}
          >
            {reupload.isPending ? 'Uploading…' : 'Upload video'}
          </button>
          {reupload.isError && (
            <span style={{ fontSize: 12, color: '#c00', marginLeft: 10 }}>
              {(reupload.error as any)?.message ?? 'Upload failed'}
            </span>
          )}
        </div>
      )}

      {/* Pass cards — shared logic:
            queued / running  → progress bar only (job in flight)
            not_started / failed / cancelled → Run button (prerequisites permitting)
            waiting_for_user  → Review → + Re-run
            accepted          → ✓ Accepted + Re-run
      */}

      {/* Pass 0 card */}
      <PassCard
        title="Pass 0 — Identify Court and Specify Camera Model"
        description="Computes a median background from frames near the video midpoint; user aligns court corners and sets the radial distortion parameter."
        pass={pass0}
        prereqMet={true}
        prereqLabel=""
        progress={progress?.passName === 'pass0' ? progress : null}
        onRun={() => runPass0.mutate()}
        isPending={runPass0.isPending}
        reviewPath={`/projects/${projectId}/pass0`}
        reviewLabel="Review →"
      />

      {/* Pass 1 card */}
      <PassCard
        title="Pass 1 — Identify Background and Court Outline"
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
        title="Pass 2 — Rally and Ball Annotation"
        description="Manually mark ball positions frame by frame to build annotation data."
        pass={pass2}
        prereqMet={pass1?.state === 'accepted'}
        prereqLabel="Accept Pass 1 first."
        progress={progress?.passName === 'pass2' ? progress : null}
        onRun={() => runPass2.mutate()}
        isPending={runPass2.isPending}
        reviewPath={`/projects/${projectId}/pass2`}
        reviewLabel="Review →"
        hideRerun
        alwaysReviewable
        extraButtons={
          (pass2?.state === 'waiting_for_user' || pass2?.state === 'accepted') ? (
            <button
              onClick={() => {
                if (window.confirm('Clear all Pass 2 annotations? This cannot be undone.')) {
                  resetPass2.mutate()
                }
              }}
              disabled={resetPass2.isPending}
              style={{ padding: '6px 16px', cursor: 'pointer', fontSize: 12, borderRadius: 4, color: '#c00', border: '1px solid #faa', background: '#fff' }}
            >
              {resetPass2.isPending ? 'Resetting…' : 'Reset'}
            </button>
          ) : undefined
        }
      />

      {/* Pass 3 card */}
      <PassCard
        title="Pass 3 — Ball Color Tagging"
        description="Samples per-pixel color data from annotated ball patches to build a ball color profile."
        pass={pass3}
        prereqMet={!!pass2}
        prereqLabel="Run Pass 2 first."
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
        prereqMet={!!pass3}
        prereqLabel="Run Pass 3 first."
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
        prereqMet={!!pass4}
        prereqLabel="Run Pass 4 first."
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
        prereqMet={!!pass2}
        prereqLabel="Run Pass 2 first."
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

function fmtDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

interface PassCardProps {
  title: string
  description: string
  pass: { state: string; is_dirty?: boolean; runnable?: boolean; last_run_duration_s?: number | null } | undefined
  prereqMet: boolean
  prereqLabel: string
  progress: { stage: string; fraction: number } | null
  onRun: () => void
  isPending: boolean
  reviewPath: string
  reviewLabel: string
  /** If true, the Re-Run button is never shown (pass has no run step worth repeating). */
  hideRerun?: boolean
  /** If true, the Review button is shown even when the pass has no results yet. */
  alwaysReviewable?: boolean
  extraControls?: React.ReactNode
  extraButtons?: React.ReactNode
}

function PassCard({
  title, description, pass, prereqMet, prereqLabel,
  progress, onRun, isPending, reviewPath, reviewLabel,
  hideRerun, alwaysReviewable, extraControls, extraButtons,
}: PassCardProps) {
  const navigate = useNavigate()
  const state = pass?.state ?? 'not_started'
  const isDirty = pass?.is_dirty ?? false
  const isRunnable = pass?.runnable ?? true
  const inFlight = IN_FLIGHT.has(state)
  const hasResults = !RUNNABLE.has(state) && !inFlight

  const rerunBtn = (
    <button
      onClick={onRun}
      disabled={isPending}
      style={{
        padding: '6px 16px', cursor: 'pointer', fontSize: 12, borderRadius: 4,
        color: isDirty ? '#7a5c00' : '#888',
        border: isDirty ? '1px solid #f0c000' : '1px solid #ccc',
        background: isDirty ? '#fff8e1' : '#fff',
      }}
    >
      {isPending ? 'Queuing…' : 'Re-run'}
    </button>
  )

  return (
    <div style={{
      border: '1px solid #ddd', borderRadius: 8, padding: 16, marginBottom: 16,
      borderLeft: isDirty ? `4px solid ${DIRTY_COLOR}` : undefined,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {isDirty && !inFlight && (
              <span style={{ fontSize: 11, fontWeight: 'bold', color: '#7a5c00', background: '#fff3cd', border: '1px solid #f0c000', borderRadius: 4, padding: '1px 6px' }}>
                Stale
              </span>
            )}
            <span style={{ color: STATE_COLOR[state] ?? '#aaa', fontWeight: 'bold' }}>
              {STATE_LABELS[state] ?? state}
            </span>
          </div>
          {pass?.last_run_duration_s != null && (
            <span style={{ fontSize: 12, color: '#888' }}>
              last run: {fmtDuration(pass.last_run_duration_s)}
            </span>
          )}
        </div>
      </div>
      <p style={{ color: '#555', fontSize: 14 }}>{description}</p>

      {/* Soft prereq warning — shown inline when pass has no results yet */}
      {!prereqMet && !hasResults && (
        <p style={{ fontSize: 13, color: '#888', margin: '0 0 8px' }}>{prereqLabel}</p>
      )}

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
        {!inFlight && RUNNABLE.has(state) && (
          <button
            onClick={onRun}
            disabled={isPending || !isRunnable}
            title={!isRunnable ? 'Required inputs from upstream passes are not ready' : undefined}
            style={{ padding: '6px 16px', cursor: isRunnable ? 'pointer' : 'not-allowed', opacity: isRunnable ? 1 : 0.5 }}
          >
            {isPending ? 'Queuing…' : state === 'not_started' ? `Run ${title.split('—')[0].trim()}` : 'Re-run'}
          </button>
        )}
        {/* Review button: shown when results are available, always-reviewable, or paused */}
        {((!inFlight && (hasResults || (alwaysReviewable && prereqMet))) || progress?.stage === 'paused') && (
          <button
            onClick={() => navigate(reviewPath)}
            style={{ padding: '6px 16px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
          >
            {reviewLabel}
          </button>
        )}
        {!inFlight && state === 'accepted' && !isDirty && (
          <span style={{ color: '#090' }}>✓ Accepted</span>
        )}
        {!hideRerun && !inFlight && (state === 'waiting_for_user' || state === 'accepted') && rerunBtn}
        {!inFlight && extraButtons}
      </div>
    </div>
  )
}
