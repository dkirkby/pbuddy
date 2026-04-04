import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'

interface BallDetection {
  cx: number
  cy: number
  radius: number
  area: number
  perimeter: number
}

export default function Pass4Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const playerRef = useRef<VideoPlayerHandle>(null)
  const [currentFrame, setCurrentFrame] = useState(0)
  const [accepting, setAccepting] = useState(false)

  const acceptPass4 = useMutation({
    mutationFn: () => api.acceptPass4(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
  })

  const { data: detectionsData, isLoading } = useQuery({
    queryKey: ['pass4-detections', projectId],
    queryFn: () => api.getPass4Detections(projectId!),
  })

  // Build a per-frame index from the flat detections array.
  const ballDetections = useMemo<Record<number, BallDetection[]>>(() => {
    if (!detectionsData?.detections) return {}
    const out: Record<number, BallDetection[]> = {}
    for (const d of detectionsData.detections) {
      if (!out[d.frame]) out[d.frame] = []
      out[d.frame].push({ cx: d.cx, cy: d.cy, radius: d.radius, area: d.area, perimeter: d.perimeter })
    }
    return out
  }, [detectionsData])

  const pass2Result = useQuery({
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

  const { data: patchData } = useQuery({
    queryKey: ['pass4-patches', projectId],
    queryFn: () => api.getPass4PatchFrames(projectId!),
  })
  const patchFrames = patchData?.frames ?? []

  // Pass 2 annotations — needed for per-frame ball radius overlays on patches.
  const { data: pass2Corrections } = useQuery({
    queryKey: ['pass2-corrections', projectId],
    queryFn: () => api.getPass2Corrections(projectId!),
  })
  const annotationsByFrame = pass2Corrections?.data?.annotations ?? {}
  const rallies = pass2Corrections?.data?.rally ?? []

  const handleFrameChange = useCallback((fi: number) => setCurrentFrame(fi), [])

  // Load detections_map.png and colorize: white → yellow/semi-transparent, black → transparent.
  const [showDetectionsMap, setShowDetectionsMap] = useState(false)
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const [overlayReady, setOverlayReady] = useState(false)

  useEffect(() => {
    if (!detectionsData) return
    const img = new Image()
    img.src = `${api.detectionsMapUrl(projectId!)}?t=${detectionsData.detection_count}`
    img.onload = () => {
      const offscreen = document.createElement('canvas')
      offscreen.width = img.naturalWidth
      offscreen.height = img.naturalHeight
      const octx = offscreen.getContext('2d')!
      octx.drawImage(img, 0, 0)
      const imageData = octx.getImageData(0, 0, offscreen.width, offscreen.height)
      const d = imageData.data
      for (let i = 0; i < d.length; i += 4) {
        if (d[i] > 128) {          // white pixel → yellow
          d[i] = 255; d[i + 1] = 220; d[i + 2] = 0; d[i + 3] = 200
        } else {                   // black pixel → transparent
          d[i + 3] = 0
        }
      }
      octx.putImageData(imageData, 0, 0)
      overlayCanvasRef.current = offscreen
      setOverlayReady(true)
    }
  }, [detectionsData, projectId])

  const fps = pass2Result.data?.fps ?? project?.video_fps ?? 30
  const bgWidth = pass2Result.data?.bg_width ?? project?.video_width ?? 960
  const bgHeight = pass2Result.data?.bg_height ?? project?.video_height ?? 540

  const [hoveredPatch, setHoveredPatch] = useState<number | null>(null)

  const totalDetections = detectionsData?.detection_count ?? 0
  const isPaused = detectionsData && 'paused' in detectionsData && (detectionsData as any).paused === true
  // Subtract 1 to match the -1 offset applied in VideoPlayer's ballDetections overlay lookup.
  const frameDets = (ballDetections[currentFrame - 1] ?? []).slice().sort((a, b) => b.radius - a.radius)

  // Circularity: 4π·area / perimeter²; perfect circle = 1.0
  function circularity(d: BallDetection): string {
    if (d.perimeter === 0) return '—'
    return (4 * Math.PI * d.area / (d.perimeter * d.perimeter)).toFixed(3)
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Pass 4 — Ball Detection Review</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 13, color: '#555' }}>
            {isPaused && <span style={{ color: '#f90', marginRight: 12 }}>⏸ Paused</span>}
            {detectionsData && (
              <span>
                {totalDetections} detections
                {detectionsData.stable_frame_count > 0 && (
                  <> in {detectionsData.stable_frame_count} frames</>
                )}
              </span>
            )}
          </div>
          <button
            onClick={() => { setAccepting(true); acceptPass4.mutate() }}
            disabled={accepting || acceptPass4.isPending || !detectionsData}
            style={{ padding: '6px 18px', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
          >
            {accepting ? 'Accepting…' : 'Accept Pass 4 →'}
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '0 0 12px' }}>
        <p style={{ color: '#666', fontSize: 13, margin: 0 }}>
          Magenta circles show detections in the current frame; cyan circles show detections in the preceding 8 frames.
        </p>
        {overlayReady && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
            <input type="checkbox" checked={showDetectionsMap} onChange={(e) => setShowDetectionsMap(e.target.checked)} />
            All detections
          </label>
        )}
      </div>

      {isLoading ? (
        <div style={{
          width: '100%', height: 360, background: '#111',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#666',
          borderRadius: 4,
        }}>
          Loading detections…
        </div>
      ) : (
        <VideoPlayer
          ref={playerRef}
          videoUrl={api.videoUrl(projectId!)}
          fps={fps}
          bgWidth={bgWidth}
          bgHeight={bgHeight}
          totalFrames={0}
          ballDetections={ballDetections}
          onFrameChange={handleFrameChange}
          storageKey={`pass4-pos-${projectId}`}
          staticOverlay={showDetectionsMap ? overlayCanvasRef.current : null}
          rallyTimeline={{
            events: rallies.map(r => ({ startFrame: r.start_frame, stopFrame: r.stop_frame, score: r.score })),
            onMarkerClick: (frame) => playerRef.current?.seekToFrame(frame),
          }}
        />
      )}

      {/* Mask patch gallery — one 64×64 BGR patch per annotated frame */}
      {patchFrames.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, color: '#555', marginBottom: 4 }}>
            Mask patches ({patchFrames.length} annotated frames) — R=motion, G=H-S, B=V-S
          </div>
          <div style={{
            overflowX: 'auto', display: 'flex', gap: 4,
            padding: 4, background: '#111', borderRadius: 4, alignItems: 'flex-start',
          }}>
            {patchFrames.map((fi) => (
              <div
                key={fi}
                style={{ position: 'relative', flexShrink: 0, cursor: 'pointer' }}
                onClick={() => playerRef.current?.seekToFrame(fi)}
                onMouseEnter={() => setHoveredPatch(fi)}
                onMouseLeave={() => setHoveredPatch(null)}
                title={`Frame ${fi} — click to seek`}
              >
                <img
                  src={hoveredPatch === fi
                    ? api.pass2AcceptedPatchUrl(projectId!, fi)
                    : api.pass4PatchUrl(projectId!, fi)}
                  style={{ display: 'block', width: 128, height: 128, imageRendering: 'pixelated' }}
                />
                {/* Centering crosshair + annotated radius circle */}
                <div style={{
                  position: 'absolute', top: '50%', left: 0, right: 0,
                  height: 1, background: 'rgba(0,220,255,0.4)', transform: 'translateY(-50%)', pointerEvents: 'none',
                }} />
                <div style={{
                  position: 'absolute', left: '50%', top: 0, bottom: 0,
                  width: 1, background: 'rgba(0,220,255,0.4)', transform: 'translateX(-50%)', pointerEvents: 'none',
                }} />
                {(() => {
                  const ann = annotationsByFrame[String(fi)]
                  if (!ann?.radius) return null
                  const r = ann.radius * 2  // bg-plate px → CSS px (zoom = 2)
                  return (
                    <svg width={128} height={128} style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}>
                      <circle cx={64} cy={64} r={r} fill="none" stroke="rgba(0,220,255,0.85)" strokeWidth={1.5} />
                    </svg>
                  )
                })()}
                <span style={{
                  position: 'absolute', bottom: 2, left: 0, right: 0,
                  textAlign: 'center', fontSize: 10, color: '#fff',
                  textShadow: '0 0 3px #000', pointerEvents: 'none',
                }}>{fi}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Detection table for current frame */}
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 13, color: '#555', marginBottom: 4 }}>
          Frame {currentFrame} — {frameDets.length} detection{frameDets.length !== 1 ? 's' : ''}
        </div>
        {frameDets.length > 0 && (
          <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%', maxWidth: 560 }}>
            <thead>
              <tr style={{ background: '#f0f0f0' }}>
                <th style={thStyle}>#</th>
                <th style={thStyle}>cx</th>
                <th style={thStyle}>cy</th>
                <th style={thStyle}>radius</th>
                <th style={thStyle}>area</th>
                <th style={thStyle}>perimeter</th>
                <th style={thStyle}>circularity</th>
              </tr>
            </thead>
            <tbody>
              {frameDets.map((d, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={tdStyle}>{i + 1}</td>
                  <td style={tdStyle}>{d.cx}</td>
                  <td style={tdStyle}>{d.cy}</td>
                  <td style={tdStyle}>{d.radius}</td>
                  <td style={tdStyle}>{d.area}</td>
                  <td style={tdStyle}>{d.perimeter}</td>
                  <td style={tdStyle}>{circularity(d)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

const thStyle: React.CSSProperties = {
  padding: '4px 10px', textAlign: 'right', fontWeight: 600, color: '#333',
}
const tdStyle: React.CSSProperties = {
  padding: '3px 10px', textAlign: 'right', color: '#444',
}
