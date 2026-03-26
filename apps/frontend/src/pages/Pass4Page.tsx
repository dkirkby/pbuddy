import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'

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
  const [currentFrame, setCurrentFrame] = useState(0)

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

  const handleFrameChange = useCallback((fi: number) => setCurrentFrame(fi), [])

  // Load detections_map.png and colorize: white → yellow/semi-transparent, black → transparent.
  const [showDetectionsMap, setShowDetectionsMap] = useState(false)
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const [overlayReady, setOverlayReady] = useState(false)

  useEffect(() => {
    if (!detectionsData) return
    const img = new Image()
    img.src = api.detectionsMapUrl(projectId!)
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
          videoUrl={api.videoUrl(projectId!)}
          fps={fps}
          bgWidth={bgWidth}
          bgHeight={bgHeight}
          totalFrames={0}
          ballDetections={ballDetections}
          onFrameChange={handleFrameChange}
          storageKey={`pass4-pos-${projectId}`}
          staticOverlay={showDetectionsMap ? overlayCanvasRef.current : null}
        />
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
