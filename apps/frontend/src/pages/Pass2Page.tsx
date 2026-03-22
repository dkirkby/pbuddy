import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { ArtifactRef, CourtGeometry, Detection, DetectionsData } from '../types/api'

interface Pass1AcceptedOutput {
  court_geometry: CourtGeometry
  bg_width: number
  bg_height: number
  stable_bounds: { in_time_s: number; out_time_s: number }
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}m ${sec.toString().padStart(2, '0')}s`
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

  const continuePass2 = useMutation({
    mutationFn: (opts: { max_duration_s?: number }) =>
      api.runPass2(projectId!, { ...opts, resume: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
    onError: (e: any) => setStatusMsg('Error: ' + e.message),
  })

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

  // Partial processing: processed_out_time_s < stable_out_time_s.
  const processedOut: number = resultData?.processed_out_time_s ?? 0
  const stableOut: number = resultData?.stable_out_time_s ?? 0
  const isPartial = stableOut > 0 && processedOut < stableOut - 0.5

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
        <div style={{ marginTop: 0, marginBottom: 16 }}>
          <p style={{ color: '#666', fontSize: 13, margin: 0 }}>
            {resultData.frame_count.toLocaleString()} frames ·{' '}
            {resultData.detection_count.toLocaleString()} detections ·{' '}
            {bgWidth}×{bgHeight} · threshold {resultData.threshold} · min area {resultData.min_area}px²
            {isPartial && (
              <span style={{ color: '#f90', marginLeft: 8 }}>
                ⏸ {fmtTime(processedOut)} / {fmtTime(stableOut)} processed
              </span>
            )}
          </p>
          {isPartial && (
            <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
              <button
                onClick={() => continuePass2.mutate({ max_duration_s: 30 })}
                disabled={continuePass2.isPending}
                style={{ padding: '6px 16px', cursor: 'pointer' }}
              >
                {continuePass2.isPending ? 'Queuing…' : 'Continue (30s)'}
              </button>
              <button
                onClick={() => continuePass2.mutate({})}
                disabled={continuePass2.isPending}
                style={{ padding: '6px 16px', cursor: 'pointer', fontSize: 12, color: '#555', border: '1px solid #ccc', borderRadius: 4, background: '#fff' }}
              >
                {continuePass2.isPending ? 'Queuing…' : 'Continue (full)'}
              </button>
            </div>
          )}
        </div>
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
