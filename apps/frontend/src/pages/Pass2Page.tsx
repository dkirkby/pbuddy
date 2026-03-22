import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { ArtifactRef, CourtGeometry, Detection, DetectionsData } from '../types/api'

interface Pass1AcceptedOutput {
  court_geometry: CourtGeometry
  bg_width: number
  bg_height: number
  stable_bounds: { in_time_s: number; out_time_s: number }
}

export default function Pass2Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  // Load pass2 artifacts.
  const { data: artResp } = useQuery({
    queryKey: ['pass2-artifacts', projectId],
    queryFn: () => api.getPass2Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []

  const resultArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json' && !a.path.includes('detections')
  )
  const detectionsArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json' && a.path.includes('detections')
  )

  // Load detections JSON (can be large — show spinner until ready).
  const { data: detectionsData, isLoading: detectionsLoading } = useQuery<DetectionsData>({
    queryKey: ['pass2-detections', projectId],
    queryFn: () => api.getDetections(detectionsArtifact!.id),
    enabled: !!detectionsArtifact,
    staleTime: Infinity,
  })

  // Load pass2 result summary.
  const { data: resultData } = useQuery({
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
    queryFn: async () => api.getPass1Artifacts(projectId!),
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

  // Parse detections into a Record<number, Detection[]> for O(1) lookup.
  const detectionsIndex = useMemo<Record<number, Detection[]>>(() => {
    if (!detectionsData?.frames) return {}
    const idx: Record<number, Detection[]> = {}
    for (const [key, dets] of Object.entries(detectionsData.frames)) {
      idx[parseInt(key, 10)] = dets
    }
    return idx
  }, [detectionsData])

  async function handleAccept() {
    setAccepting(true)
    setStatusMsg(null)
    try {
      await api.acceptPass2(projectId!)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setAccepting(false)
    }
  }

  const fps = detectionsData?.fps ?? resultData?.fps ?? 30
  const bgWidth = detectionsData?.bg_width ?? resultData?.bg_width ?? 960
  const bgHeight = detectionsData?.bg_height ?? resultData?.bg_height ?? 540
  const totalFrames = resultData?.frame_count ?? 0

  const isReady = !!detectionsData && !detectionsLoading

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>Pass 2 Review — Moving Object Detection</h1>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
          <button
            onClick={handleAccept}
            disabled={accepting || !isReady}
            style={{
              padding: '8px 20px', background: '#0a0', color: '#fff',
              border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 15,
            }}
          >
            {accepting ? 'Accepting…' : 'Accept →'}
          </button>
          {statusMsg && (
            <p style={{ fontSize: 12, color: statusMsg.startsWith('Error') ? 'red' : 'green', margin: 0 }}>
              {statusMsg}
            </p>
          )}
        </div>
      </div>

      {resultData && (
        <p style={{ color: '#666', fontSize: 13, marginTop: 0, marginBottom: 16 }}>
          {resultData.frame_count.toLocaleString()} frames processed ·{' '}
          {resultData.detection_count.toLocaleString()} total detections ·{' '}
          {bgWidth}×{bgHeight} · threshold {resultData.threshold} · min area {resultData.min_area}px²
        </p>
      )}

      {!isReady ? (
        <div style={{
          width: '100%', height: 360, background: '#111',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#666',
          borderRadius: 4,
        }}>
          {detectionsLoading || detectionsArtifact
            ? 'Loading detections…'
            : 'No artifacts yet — pass 2 may still be running.'}
        </div>
      ) : (
        <VideoPlayer
          videoUrl={api.videoUrl(projectId!)}
          fps={fps}
          bgWidth={bgWidth}
          bgHeight={bgHeight}
          detections={detectionsIndex}
          courtGeometry={pass1Accepted?.court_geometry}
          totalFrames={totalFrames}
        />
      )}
    </div>
  )
}
