import React, { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

interface Pass3Result {
  annotation_count: number
  ball_pixel_count: number
  min_ball_radius: number
  max_ball_radius: number
}

export default function Pass3Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: pass3Result } = useQuery<Pass3Result | null>({
    queryKey: ['pass3-raw-result', projectId],
    queryFn: async () => {
      const res = await fetch(`/api/projects/${projectId}/passes/pass3/raw/result.json`)
      if (!res.ok) return null
      return res.json()
    },
  })

  const needsBorrow = pass3Result !== null && pass3Result !== undefined && pass3Result.annotation_count === 0

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.listProjects(),
    enabled: needsBorrow,
  })

  const otherProjects = projects?.filter(p => p.id !== projectId) ?? []

  const submitAndAccept = useMutation({
    mutationFn: async (sourceProjectId: string) => {
      await api.submitPass3Corrections(projectId!, sourceProjectId)
      await api.acceptPass3(projectId!)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const accept = useMutation({
    mutationFn: () => api.acceptPass3(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const hsvSigUrl = `/api/projects/${projectId}/passes/pass3/raw/HSVsig.png`
  const hsvBgUrl  = `/api/projects/${projectId}/passes/pass3/raw/HSVbg.png`

  return (
    <div style={{ padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Project
      </button>
      <h1 style={{ marginTop: 0 }}>Pass 3 — Ball Color Tagging</h1>

      {needsBorrow ? (
        <BorrowMaskPanel
          otherProjects={otherProjects}
          isPending={submitAndAccept.isPending}
          error={(submitAndAccept.error as Error)?.message}
          onAccept={sourceProjectId => submitAndAccept.mutate(sourceProjectId)}
        />
      ) : (
        <>
          <img src={hsvSigUrl} style={{ display: 'block', maxWidth: '100%', marginBottom: 16 }} />
          <img src={hsvBgUrl}  style={{ display: 'block', maxWidth: '100%', marginBottom: 24 }} />
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
        </>
      )}
    </div>
  )
}

function BorrowMaskPanel({
  otherProjects,
  isPending,
  error,
  onAccept,
}: {
  otherProjects: { id: string; name: string }[]
  isPending: boolean
  error?: string
  onAccept: (sourceProjectId: string) => void
}) {
  const [selectedId, setSelectedId] = useState('')

  return (
    <div>
      <p style={{ color: '#666', marginBottom: 16 }}>
        No ball annotations found. Select a project to borrow its HSV color mask.
      </p>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <select
          value={selectedId}
          onChange={e => setSelectedId(e.target.value)}
          style={{ padding: '6px 10px', fontSize: 14, minWidth: 240 }}
        >
          <option value=''>— select a project —</option>
          {otherProjects.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button
          onClick={() => onAccept(selectedId)}
          disabled={!selectedId || isPending}
          style={{ padding: '8px 20px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
        >
          {isPending ? 'Accepting…' : 'Accept with borrowed mask'}
        </button>
      </div>
      {error && <span style={{ color: '#c00', fontSize: 14 }}>{error}</span>}
    </div>
  )
}

