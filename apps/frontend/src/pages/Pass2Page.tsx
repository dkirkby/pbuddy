import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { ArtifactRef, BallAnnotation, CourtGeometry, Pass2RawResult } from '../types/api'
import { BALL_PATCH_RADIUS } from '../lib/dimensions'

const PATCH_RADIUS = BALL_PATCH_RADIUS
const PATCH_DISPLAY_ZOOM = 2
const PATCH_DISPLAY_SIZE = PATCH_RADIUS * 2 * PATCH_DISPLAY_ZOOM  // 128 px

function RadiusOverlay({ minR, maxR }: { minR: number; maxR: number }) {
  const c = PATCH_DISPLAY_SIZE / 2
  return (
    <svg style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
         width={PATCH_DISPLAY_SIZE} height={PATCH_DISPLAY_SIZE}>
      <circle cx={c} cy={c} r={minR * PATCH_DISPLAY_ZOOM} fill="none" stroke="rgba(0,220,255,0.75)" strokeWidth={1} />
      <circle cx={c} cy={c} r={maxR * PATCH_DISPLAY_ZOOM} fill="none" stroke="rgba(0,220,255,0.75)" strokeWidth={1} />
    </svg>
  )
}

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
  const previewRef = useRef<HTMLCanvasElement>(null)

  const [annotations, setAnnotations] = useState<Record<string, BallAnnotation>>({})
  const [patches, setPatches] = useState<Record<string, string>>({})
  const [patchOrder, setPatchOrder] = useState<string[]>([])
  const [minBallRadius, setMinBallRadius] = useState(4)
  const [maxBallRadius, setMaxBallRadius] = useState(16)
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
  const bgPlateArtifact = (pass1ArtResp?.data ?? []).find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'png'
  )
  const bgPlateUrl = bgPlateArtifact ? api.artifactUrl(bgPlateArtifact.id) : undefined
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
      setPatchOrder(
        Object.keys(correctionsResp.data.patches).sort((a, b) => parseInt(b) - parseInt(a))
      )
    }
    setMinBallRadius(correctionsResp.data.min_ball_radius ?? 4)
    setMaxBallRadius(correctionsResp.data.max_ball_radius ?? 16)
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
    (frameIndex: number, bgX: number, bgY: number, patchDataUrl: string | null) => {
      const key = String(frameIndex)
      setAnnotations((prev) => ({
        ...prev,
        [key]: { x: Math.round(bgX * 10) / 10, y: Math.round(bgY * 10) / 10 },
      }))
      setPatches((prev) => patchDataUrl ? { ...prev, [key]: patchDataUrl } : prev)
      setPatchOrder((prev) => [key, ...prev.filter((k) => k !== key)])
      setDirty(true)
      setStatusMsg(null)
    },
    []
  )

  function handleDeleteAnnotation(fi: string) {
    setAnnotations((prev) => { const next = { ...prev }; delete next[fi]; return next })
    setPatches((prev) => { const next = { ...prev }; delete next[fi]; return next })
    setPatchOrder((prev) => prev.filter((k) => k !== fi))
    setDirty(true)
    setStatusMsg(null)
  }

  async function handleSave() {
    setSaving(true)
    setStatusMsg(null)
    try {
      await api.savePass2Annotations(projectId!, annotations, patches, minBallRadius, maxBallRadius)
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

      <div style={{ display: 'flex', gap: 24, alignItems: 'center', margin: '0 0 8px', fontSize: 13 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          Min ball radius (px):
          <input
            type="number"
            min={2} max={32}
            value={minBallRadius}
            onChange={(e) => { setMinBallRadius(Math.max(2, Math.min(32, parseInt(e.target.value) || 2))); setDirty(true) }}
            style={{ width: 52, padding: '2px 4px' }}
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          Max ball radius (px):
          <input
            type="number"
            min={2} max={32}
            value={maxBallRadius}
            onChange={(e) => { setMaxBallRadius(Math.max(2, Math.min(32, parseInt(e.target.value) || 2))); setDirty(true) }}
            style={{ width: 52, padding: '2px 4px' }}
          />
        </label>
      </div>

      <p style={{ color: '#666', fontSize: 13, margin: '0 0 12px' }}>
        Click on the ball to mark its centre. Use the × button on a patch to remove a mark.
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
          previewCanvasRef={previewRef}
          bgPlateUrl={bgPlateUrl}
        />
      )}

      {/* Ball patch gallery — live preview slot always first, annotations to the right */}
      {resultData && (
        <div style={{
          marginTop: 12, overflowX: 'auto', display: 'flex', gap: 4,
          padding: '4px', background: '#111', borderRadius: 4, alignItems: 'flex-start',
        }}>
          {/* Live preview canvas — updated by VideoPlayer rAF loop on mouse move */}
          <div style={{ position: 'relative', flexShrink: 0 }}>
            <canvas
              ref={previewRef}
              width={PATCH_RADIUS * 2}
              height={PATCH_RADIUS * 2}
              style={{
                display: 'block',
                width: PATCH_DISPLAY_SIZE, height: PATCH_DISPLAY_SIZE,
                imageRendering: 'pixelated',
              }}
            />
            <RadiusOverlay minR={minBallRadius} maxR={maxBallRadius} />
          </div>
          {patchOrder.length > 0 && (
            <div style={{ width: 1, alignSelf: 'stretch', background: 'rgba(255,255,255,0.15)', flexShrink: 0 }} />
          )}
          {patchOrder.filter((fi) => patches[fi]).map((fi) => (
            <div
              key={fi}
              style={{ position: 'relative', flexShrink: 0, cursor: 'pointer' }}
              onClick={() => playerRef.current?.seekToFrame(parseInt(fi))}
              title={`Frame ${fi} — click to seek`}
            >
              <img
                src={patches[fi]}
                style={{
                  display: 'block',
                  width: PATCH_DISPLAY_SIZE,
                  height: PATCH_DISPLAY_SIZE,
                  imageRendering: 'pixelated',
                }}
              />
              <RadiusOverlay minR={minBallRadius} maxR={maxBallRadius} />
              {/* Centering crosshair */}
              <div style={{
                position: 'absolute', top: '50%', left: 0, right: 0,
                height: 2, background: 'rgba(0,220,255,0.45)',
                transform: 'translateY(-50%)', pointerEvents: 'none',
              }} />
              <div style={{
                position: 'absolute', left: '50%', top: 0, bottom: 0,
                width: 2, background: 'rgba(0,220,255,0.45)',
                transform: 'translateX(-50%)', pointerEvents: 'none',
              }} />
              <span style={{
                position: 'absolute', bottom: 2, left: 0, right: 0,
                textAlign: 'center', fontSize: 10, color: '#fff',
                textShadow: '0 0 3px #000', pointerEvents: 'none',
              }}>{fi}</span>
              {/* Delete button */}
              <button
                style={{
                  position: 'absolute', top: 2, right: 2,
                  width: 18, height: 18,
                  background: 'rgba(0,0,0,0.6)', color: '#fff',
                  border: 'none', borderRadius: '50%',
                  cursor: 'pointer', fontSize: 12,
                  padding: 0, lineHeight: 1,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
                onClick={(e) => { e.stopPropagation(); handleDeleteAnnotation(fi) }}
                title="Remove annotation"
              >×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
