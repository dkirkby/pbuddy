import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { ArtifactRef, BallAnnotation, CourtGeometry, Pass2RawResult } from '../types/api'

const PATCH_RADIUS = 32   // must match VideoPlayer PATCH_RADIUS
const PATCH_DISPLAY_ZOOM = 3
const PATCH_DISPLAY_SIZE = PATCH_RADIUS * 2 * PATCH_DISPLAY_ZOOM  // 192 px

interface Pass1AcceptedOutput {
  court_geometry: CourtGeometry
  bg_width: number
  bg_height: number
}

export default function Pass2Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const playerRef = useRef<VideoPlayerHandle>(null)

  const [annotations, setAnnotations] = useState<Record<string, BallAnnotation>>({})
  const [patches, setPatches] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  // Load pass2 raw result (fps, bg dimensions).
  const { data: artResp } = useQuery({
    queryKey: ['pass2-artifacts', projectId],
    queryFn: () => api.getPass2Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []
  const resultArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )
  const { data: resultData } = useQuery<Pass2RawResult>({
    queryKey: ['pass2-result', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(resultArtifact!.id))
      return resp.json()
    },
    enabled: !!resultArtifact,
  })

  // Load pass1 accepted result for court geometry.
  const { data: pass1ArtResp } = useQuery({
    queryKey: ['pass1-artifacts-accepted', projectId],
    queryFn: () => api.getPass1Artifacts(projectId!),
  })
  const pass1AcceptedArt = (pass1ArtResp?.data ?? []).find(
    (a) => a.artifact_role === 'accepted' && a.artifact_type === 'json'
  )
  const { data: pass1Accepted } = useQuery<Pass1AcceptedOutput>({
    queryKey: ['pass1-accepted', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(pass1AcceptedArt!.id))
      return resp.json()
    },
    enabled: !!pass1AcceptedArt,
  })

  // Load saved annotations and patches on mount.
  const { data: correctionsResp } = useQuery({
    queryKey: ['pass2-corrections', projectId],
    queryFn: () => api.getPass2Corrections(projectId!),
    staleTime: Infinity,
  })
  useEffect(() => {
    if (!correctionsResp?.data) return
    if (correctionsResp.data.annotations) {
      setAnnotations(correctionsResp.data.annotations)
    }
    if (correctionsResp.data.patches) {
      setPatches(correctionsResp.data.patches)
    }
    setDirty(false)
  }, [correctionsResp])

  // Convert annotations to number-keyed map for VideoPlayer.
  const annotationsById = useMemo<Record<number, BallAnnotation>>(() => {
    const out: Record<number, BallAnnotation> = {}
    for (const [k, v] of Object.entries(annotations)) {
      out[parseInt(k, 10)] = v
    }
    return out
  }, [annotations])

  const handleVideoClick = useCallback(
    (frameIndex: number, bgX: number, bgY: number, shiftKey: boolean, patchDataUrl: string | null) => {
      const key = String(frameIndex)
      setAnnotations((prev) => {
        const next = { ...prev }
        if (shiftKey) {
          delete next[key]
        } else {
          next[key] = { x: Math.round(bgX * 10) / 10, y: Math.round(bgY * 10) / 10 }
        }
        return next
      })
      setPatches((prev) => {
        const next = { ...prev }
        if (shiftKey) {
          delete next[key]
        } else if (patchDataUrl) {
          next[key] = patchDataUrl
        }
        return next
      })
      setDirty(true)
      setStatusMsg(null)
    },
    []
  )

  async function handleSave() {
    setSaving(true)
    setStatusMsg(null)
    try {
      await api.savePass2Annotations(projectId!, annotations, patches)
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['pass2-corrections', projectId] })
      setStatusMsg('Saved.')
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleAccept() {
    setAccepting(true)
    setStatusMsg(null)
    try {
      if (dirty) await handleSave()
      await api.acceptPass2(projectId!)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setAccepting(false)
    }
  }

  const fps = resultData?.fps ?? 30
  const bgWidth = resultData?.bg_width ?? 960
  const bgHeight = resultData?.bg_height ?? 540
  const ballCount = Object.keys(annotations).length

  const sortedPatchEntries = Object.entries(patches).sort(
    ([a], [b]) => parseInt(a) - parseInt(b)
  )

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Pass 2 — Ball Annotation</h1>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              style={{ padding: '8px 16px', cursor: 'pointer' }}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={handleAccept}
              disabled={accepting}
              style={{
                padding: '8px 20px', background: '#0a0', color: '#fff',
                border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 15,
              }}
            >
              {accepting ? 'Accepting…' : 'Accept →'}
            </button>
          </div>
          {statusMsg && (
            <p style={{ fontSize: 12, color: statusMsg.startsWith('Error') ? 'red' : 'green', margin: 0 }}>
              {statusMsg}
            </p>
          )}
        </div>
      </div>

      <p style={{ color: '#666', fontSize: 13, margin: '0 0 12px' }}>
        Click on the ball to mark its centre. Shift-click to remove a mark.
        {dirty && <span style={{ color: '#f90', marginLeft: 8 }}>⚠ Unsaved changes</span>}
      </p>

      {!resultData ? (
        <div style={{
          width: '100%', height: 360, background: '#111',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#666',
          borderRadius: 4,
        }}>
          {resultArtifact ? 'Loading…' : 'No artifacts yet — pass 2 may still be running.'}
        </div>
      ) : (
        <VideoPlayer
          ref={playerRef}
          videoUrl={api.videoUrl(projectId!)}
          fps={fps}
          bgWidth={bgWidth}
          bgHeight={bgHeight}
          totalFrames={0}
          courtGeometry={pass1Accepted?.court_geometry}
          annotations={annotationsById}
          onVideoClick={handleVideoClick}
          ballCount={ballCount}
          storageKey={`pass2-pos-${projectId}`}
        />
      )}

      {/* Ball patch gallery */}
      {sortedPatchEntries.length > 0 && (
        <div style={{
          marginTop: 12, overflowX: 'auto', display: 'flex', gap: 6,
          padding: '8px 4px', background: '#111', borderRadius: 4,
        }}>
          {sortedPatchEntries.map(([fi, dataUrl]) => (
            <div
              key={fi}
              style={{ flexShrink: 0, textAlign: 'center', cursor: 'pointer' }}
              onClick={() => playerRef.current?.seekToFrame(parseInt(fi))}
              title={`Frame ${fi} — click to seek`}
            >
              <img
                src={dataUrl}
                style={{
                  display: 'block',
                  width: PATCH_DISPLAY_SIZE,
                  height: PATCH_DISPLAY_SIZE,
                  imageRendering: 'pixelated',
                }}
              />
              <div style={{ fontSize: 10, color: '#888', marginTop: 2 }}>{fi}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
