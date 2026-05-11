import { useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { RallyRecord } from '../types/api'

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toFixed(1).padStart(4, '0')}`
}

export default function Pass6Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
  })

  const { data: pass2Corr } = useQuery({
    queryKey: ['pass2-corrections', projectId],
    queryFn: () => api.getPass2Corrections(projectId!),
  })

  const { data: pass6Result } = useQuery({
    queryKey: ['pass6-result', projectId],
    queryFn: () => api.getPass6Result(projectId!),
    retry: false,
  })

  const [copied, setCopied] = useState(false)
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fps = project?.video_fps ?? 30
  const rallies: RallyRecord[] = pass2Corr?.data?.rally ?? []
  const playerNames = pass2Corr?.data?.player_names

  // Build a map from player name → their teammate, using the fixed team pairs.
  const teammateOf = useMemo(() => {
    if (!playerNames) return new Map<string, string>()
    const pairs: [string, string][] = [
      [playerNames.far_team_left, playerNames.far_team_right],
      [playerNames.near_team_left, playerNames.near_team_right],
    ]
    const map = new Map<string, string>()
    for (const [a, b] of pairs) {
      map.set(a, b)
      map.set(b, a)
    }
    return map
  }, [playerNames])

  const rallyTimings = useMemo(() => {
    const chapterStarts = pass6Result?.rally_chapter_starts
    return rallies.map((r, i) => {
      const duration = (r.stop_frame - r.start_frame + 1) / fps
      const outputStart = chapterStarts?.[i] ?? null
      return { ...r, outputStart, duration }
    })
  }, [rallies, fps, pass6Result])

  const pass6State = project?.passes.find((p) => p.pass_name === 'pass6')?.state

  const acceptPass6 = useMutation({
    mutationFn: () => api.acceptPass6(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back
      </button>
      <h1>Pass 6 — Video Export</h1>

      {pass6Result && (
        <p style={{ color: '#555', fontSize: 14 }}>
          {pass6Result.rally_count} rallies · {pass6Result.output_duration_s.toFixed(1)}s total
        </p>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 20 }}>
        {pass6State === 'waiting_for_user' && (
          <>
            <a
              href={api.pass6ExportUrl(projectId!)}
              download="export.mp4"
              style={{
                padding: '6px 16px',
                background: '#09f',
                color: '#fff',
                borderRadius: 4,
                textDecoration: 'none',
                fontSize: 14,
              }}
            >
              Download export.mp4
            </a>
            <button
              onClick={() => acceptPass6.mutate()}
              disabled={acceptPass6.isPending}
              style={{
                padding: '6px 16px',
                background: '#0a0',
                color: '#fff',
                border: 'none',
                borderRadius: 4,
                cursor: 'pointer',
                fontSize: 14,
              }}
            >
              {acceptPass6.isPending ? 'Accepting…' : 'Accept'}
            </button>
          </>
        )}
        {pass6State === 'accepted' && (
          <>
            <span style={{ color: '#090', fontWeight: 'bold' }}>✓ Accepted</span>
            <a
              href={api.pass6ExportUrl(projectId!)}
              download="export.mp4"
              style={{
                padding: '6px 16px',
                background: '#09f',
                color: '#fff',
                borderRadius: 4,
                textDecoration: 'none',
                fontSize: 14,
              }}
            >
              Download export.mp4
            </a>
          </>
        )}
      </div>

      {/* Rally table */}
      {rallyTimings.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, marginBottom: 20 }}>
          <thead>
            <tr style={{ background: '#f5f5f5' }}>
              <th style={th}>#</th>
              <th style={th}>Score</th>
              <th style={th}>Server</th>
              <th style={th}>Srv-Partner</th>
              <th style={th}>Receiver</th>
              <th style={th}>Rcv-Partner</th>
              <th style={th}>Point?</th>
              <th style={th}>Chapter start</th>
              <th style={th}>Duration</th>
            </tr>
          </thead>
          <tbody>
            {rallyTimings.map((r, i) => (
              <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                <td style={td}>{i + 1}</td>
                <td style={{ ...td, fontWeight: 'bold' }}>{r.score}</td>
                <td style={td}>{r.serverName}</td>
                <td style={td}>{teammateOf.get(r.serverName) ?? '—'}</td>
                <td style={td}>{r.receiverName}</td>
                <td style={td}>{teammateOf.get(r.receiverName) ?? '—'}</td>
                <td style={{ ...td, color: r.servingTeamWinsRally ? '#080' : '#c00' }}>
                  {r.servingTeamWinsRally ? 'TRUE' : 'FALSE'}
                </td>
                <td style={{ ...td, fontFamily: 'monospace' }}>
                  {r.outputStart !== null ? formatTime(r.outputStart) : '—'}
                </td>
                <td style={{ ...td, fontFamily: 'monospace' }}>{r.duration.toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {/* YouTube chapter timestamps */}
      {pass6Result?.chapter_timestamps && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#333' }}>YouTube chapter timestamps</span>
            <button
              onClick={() => {
                navigator.clipboard.writeText(pass6Result.chapter_timestamps!)
                setCopied(true)
                if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current)
                copyTimeoutRef.current = setTimeout(() => setCopied(false), 2000)
              }}
              style={{ fontSize: 12, padding: '2px 10px', cursor: 'pointer' }}
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <pre style={{
            fontFamily: 'monospace', fontSize: 12, background: '#f5f5f5',
            border: '1px solid #ddd', borderRadius: 4, padding: '8px 12px',
            margin: 0, whiteSpace: 'pre-wrap', color: '#222',
          }}>
            {pass6Result.chapter_timestamps}
          </pre>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = {
  textAlign: 'left',
  padding: '6px 10px',
  fontWeight: 600,
  borderBottom: '2px solid #ddd',
}

const td: React.CSSProperties = {
  padding: '5px 10px',
}
