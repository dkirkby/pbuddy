import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PickleballDoublesGame } from '../lib/PickleballDoublesGame'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { ArtifactRef, BallAnnotation, CourtGeometry, Pass1RawResult, Pass2RawResult, PlayerNames, RallyRecord } from '../types/api'
import { BALL_PATCH_RADIUS } from '../lib/dimensions'

const PATCH_RADIUS = BALL_PATCH_RADIUS
const PATCH_DISPLAY_ZOOM = 2
const PATCH_DISPLAY_SIZE = PATCH_RADIUS * 2 * PATCH_DISPLAY_ZOOM  // 128 px

function RadiusOverlay({ radius }: { radius: number }) {
  if (radius <= 0) return null
  const c = PATCH_DISPLAY_SIZE / 2
  return (
    <svg style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
         width={PATCH_DISPLAY_SIZE} height={PATCH_DISPLAY_SIZE}>
      <circle cx={c} cy={c} r={radius * PATCH_DISPLAY_ZOOM} fill="none" stroke="rgba(255,60,60,0.85)" strokeWidth={1.5} />
    </svg>
  )
}

interface Pass1AcceptedOutput {
  court_geometry: CourtGeometry
  bg_width: number
  bg_height: number
}

type PendingRally = { score: string; start_frame: number; serverName: string; receiverName: string }
type PhaseTargetType = 'new-serve' | 'existing-serve' | 'pending-serve' | 'pending-end' | 'existing-end'
interface PhaseInfo { isServePhase: boolean; targetType: PhaseTargetType; targetIndex: number }

function computePhaseInfo(
  currentFrame: number,
  rallies: RallyRecord[],
  pendingRally: PendingRally | null,
  fps: number,
): PhaseInfo {
  type Ev = { frame: number; type: 'serve' | 'end'; kind: 'existing' | 'pending'; index: number }
  const events: Ev[] = []
  for (let i = 0; i < rallies.length; i++) {
    events.push({ frame: rallies[i].start_frame, type: 'serve', kind: 'existing', index: i })
    events.push({ frame: rallies[i].stop_frame,  type: 'end',   kind: 'existing', index: i })
  }
  if (pendingRally !== null) {
    events.push({ frame: pendingRally.start_frame, type: 'serve', kind: 'pending', index: -1 })
  }
  events.sort((a, b) => a.frame - b.frame)

  if (events.length === 0) return { isServePhase: true, targetType: 'new-serve', targetIndex: -1 }

  // If inside the pending rally (past its serve, no later events), wait for rally end.
  if (pendingRally !== null && currentFrame >= pendingRally.start_frame) {
    if (!events.some(e => e.frame > currentFrame)) {
      return { isServePhase: false, targetType: 'pending-end', targetIndex: -1 }
    }
  }

  // Nearest past event (≤ currentFrame) and nearest future event (> currentFrame).
  let prev: Ev | null = null
  let next: Ev | null = null
  for (const e of events) {
    if (e.frame <= currentFrame) prev = e
    else if (next === null) next = e
  }

  let chosen: Ev
  if (prev === null) {
    chosen = next!
  } else if (next === null) {
    if (prev.type === 'end') {
      // Within 1 second of the last rally-end: keep Rally Winner enabled so it can be corrected.
      if (currentFrame - prev.frame <= fps) {
        return { isServePhase: false, targetType: 'existing-end', targetIndex: prev.index }
      }
      return { isServePhase: true, targetType: 'new-serve', targetIndex: -1 }
    }
    return { isServePhase: false, targetType: 'pending-end', targetIndex: -1 }
  } else {
    // Tie-break toward the future (next).
    chosen = (next.frame - currentFrame) <= (currentFrame - prev.frame) ? next : prev
  }

  if (chosen.type === 'serve') {
    return {
      isServePhase: true,
      targetType: chosen.kind === 'pending' ? 'pending-serve' : 'existing-serve',
      targetIndex: chosen.index,
    }
  }
  return { isServePhase: false, targetType: 'existing-end', targetIndex: chosen.index }
}

function getServingTeamIndex(serverName: string, names: PlayerNames): 0 | 1 {
  return (serverName === names.serving_team_right || serverName === names.serving_team_left) ? 0 : 1
}

function replayGame(
  rallies: RallyRecord[],
  names: PlayerNames,
): { updatedRallies: RallyRecord[]; finalGame: PickleballDoublesGame } {
  const sorted = [...rallies].sort((a, b) => a.start_frame - b.start_frame)
  const game = new PickleballDoublesGame(
    names.serving_team_right, names.serving_team_left,
    names.receiving_team_right, names.receiving_team_left,
  )
  const updatedRallies = sorted.map(r => {
    const pos = game.positions
    const updated: RallyRecord = {
      ...r,
      score: game.toString(),
      serverName: pos[game.serverPosition],
      receiverName: pos[game.receiverPosition],
    }
    game.update(r.servingTeamWinsRally)
    return updated
  })
  return { updatedRallies, finalGame: game }
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
  const [playerNames, setPlayerNames] = useState<PlayerNames>({
    serving_team_right: 'Serving Team Right',
    serving_team_left: 'Serving Team Left',
    receiving_team_right: 'Receiving Team Right',
    receiving_team_left: 'Receiving Team Left',
  })
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  const [pendingRally, setPendingRally] = useState<PendingRally | null>(null)
  const [rallies, setRallies] = useState<RallyRecord[]>([])

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

  const [currentFrameIndex, setCurrentFrameIndex] = useState(0)

  // Load pass1 artifacts for court geometry, bg plates, and window times.
  const { data: pass1ArtResp } = useQuery({
    queryKey: ['pass1-artifacts-accepted', projectId],
    queryFn: () => api.getPass1Artifacts(projectId!),
  })
  const pass1ArtifactList: ArtifactRef[] = pass1ArtResp?.data ?? []
  const pass1AcceptedArt = pass1ArtifactList.find(
    (a) => a.artifact_role === 'accepted' && a.artifact_type === 'json'
  )
  const pass1RawJsonArt = pass1ArtifactList.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )
  const bgArtifacts = pass1ArtifactList
    .filter((a) => a.artifact_role === 'raw' && a.artifact_type === 'png' && a.path.includes('median_background'))
    .sort((a, b) => a.path.localeCompare(b.path))

  const { data: pass1Accepted } = useQuery<Pass1AcceptedOutput>({
    queryKey: ['pass1-accepted', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(pass1AcceptedArt!.id))
      return resp.json()
    },
    enabled: !!pass1AcceptedArt,
  })
  const { data: pass1Raw } = useQuery<Pass1RawResult>({
    queryKey: ['pass1-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(pass1RawJsonArt!.id))
      return resp.json()
    },
    enabled: !!pass1RawJsonArt,
  })

  // Select the median background whose time-window midpoint is closest to the current frame.
  const bgPlateUrl = useMemo(() => {
    if (bgArtifacts.length === 0) return undefined
    const windowTimes = pass1Raw?.median_window_times
    if (!windowTimes || windowTimes.length <= 1) {
      return api.artifactUrl(bgArtifacts[0].id)
    }
    const fps_ = resultData?.fps ?? 30
    const t = currentFrameIndex / fps_
    let best = 0
    let bestDist = Infinity
    for (let i = 0; i < windowTimes.length; i++) {
      const mid = (windowTimes[i][0] + windowTimes[i][1]) / 2
      const dist = Math.abs(mid - t)
      if (dist < bestDist) { bestDist = dist; best = i }
    }
    const idx = Math.min(best, bgArtifacts.length - 1)
    return api.artifactUrl(bgArtifacts[idx].id)
  }, [currentFrameIndex, bgArtifacts, pass1Raw, resultData])

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
    if (correctionsResp.data.player_names) {
      setPlayerNames(correctionsResp.data.player_names)
    }
    if (correctionsResp.data.rally) {
      setRallies(correctionsResp.data.rally)
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

  // Discard any pending (unstamped) serve when player names change.
  useEffect(() => {
    setPendingRally(null)
  }, [playerNames.serving_team_right, playerNames.serving_team_left, playerNames.receiving_team_right, playerNames.receiving_team_left])

  // Derived game state at the current frame position.
  const gameDisplay = useMemo(() => {
    const game = new PickleballDoublesGame(
      playerNames.serving_team_right, playerNames.serving_team_left,
      playerNames.receiving_team_right, playerNames.receiving_team_left,
    )
    const snap = () => {
      const pos = game.positions
      return { score: game.toString(), server: pos[game.serverPosition], receiver: pos[game.receiverPosition] }
    }
    let display = snap()
    for (const r of [...rallies].sort((a, b) => a.start_frame - b.start_frame)) {
      if (r.start_frame > currentFrameIndex) break
      display = { score: r.score, server: r.serverName, receiver: r.receiverName }
      if (r.stop_frame > currentFrameIndex) break
      game.update(r.servingTeamWinsRally)
      display = snap()
    }
    if (pendingRally && pendingRally.start_frame <= currentFrameIndex) {
      display = { score: pendingRally.score, server: pendingRally.serverName, receiver: pendingRally.receiverName }
    }
    return display
  }, [currentFrameIndex, rallies, pendingRally, playerNames.serving_team_right, playerNames.serving_team_left, playerNames.receiving_team_right, playerNames.receiving_team_left])

  const phaseInfo = useMemo(
    () => computePhaseInfo(currentFrameIndex, rallies, pendingRally, resultData?.fps ?? 30),
    [currentFrameIndex, rallies, pendingRally, resultData?.fps],
  )
  const servePhase = phaseInfo.isServePhase

  function handleServe() {
    const { targetType, targetIndex } = phaseInfo
    if (targetType === 'new-serve') {
      setPendingRally({
        score: gameDisplay.score,
        start_frame: currentFrameIndex,
        serverName: gameDisplay.server,
        receiverName: gameDisplay.receiver,
      })
    } else if (targetType === 'existing-serve') {
      const updated = [...rallies]
      updated[targetIndex] = { ...updated[targetIndex], start_frame: currentFrameIndex }
      setRallies(replayGame(updated, playerNames).updatedRallies)
    } else if (targetType === 'pending-serve') {
      setPendingRally(prev => prev ? { ...prev, start_frame: currentFrameIndex } : null)
    }
    setDirty(true)
  }

  function handleRallyWinner(winningTeamIndex: 0 | 1) {
    const { targetType, targetIndex } = phaseInfo
    if (targetType === 'pending-end') {
      if (!pendingRally) return
      const servingTeamWinsRally = winningTeamIndex === getServingTeamIndex(pendingRally.serverName, playerNames)
      const record: RallyRecord = { ...pendingRally, stop_frame: currentFrameIndex, servingTeamWinsRally }
      setRallies(replayGame([...rallies, record], playerNames).updatedRallies)
      setPendingRally(null)
    } else if (targetType === 'existing-end') {
      const servingTeamWinsRally = winningTeamIndex === getServingTeamIndex(rallies[targetIndex].serverName, playerNames)
      const updated = [...rallies]
      updated[targetIndex] = { ...updated[targetIndex], stop_frame: currentFrameIndex, servingTeamWinsRally }
      setRallies(replayGame(updated, playerNames).updatedRallies)
    }
    setDirty(true)
  }

  const handleVideoClick = useCallback(
    (frameIndex: number, bgX: number, bgY: number, patchDataUrl: string | null, radius: number) => {
      const key = String(frameIndex)
      setAnnotations((prev) => ({
        ...prev,
        [key]: { x: Math.round(bgX * 10) / 10, y: Math.round(bgY * 10) / 10, radius },
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
      await api.savePass2Annotations(projectId!, annotations, patches, playerNames, rallies)
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
        <h1 style={{ margin: 0 }}>Pass 2 — Rally and Ball Annotation</h1>
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
        Click on the ball centre, then drag outward to set the ball radius. Release to confirm.
        Use the × button on a patch to remove a mark.
        {dirty && <span style={{ color: '#f90', marginLeft: 8 }}>⚠ Unsaved changes</span>}
      </p>

      <div style={{ marginBottom: 8 }}>
        {([
          { keys: ['serving_team_right', 'serving_team_left'] as (keyof PlayerNames)[], labels: ['Serving Team Right', 'Serving Team Left'], teamIndex: 0 as 0 | 1 },
          { keys: ['receiving_team_left', 'receiving_team_right'] as (keyof PlayerNames)[], labels: ['Receiving Team Left', 'Receiving Team Right'], teamIndex: 1 as 0 | 1 },
        ]).map(({ keys, labels, teamIndex }) => (
          <div key={keys[0]} style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 8 }}>
            {keys.map((key, i) => (
              <label key={key} style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13, flex: '1 1 0' }}>
                <span style={{ color: '#444' }}>{labels[i]}</span>
                <input
                  type="text"
                  value={playerNames[key]}
                  onChange={(e) => {
                    setPlayerNames((prev) => ({ ...prev, [key]: e.target.value }))
                    setDirty(true)
                  }}
                  style={{ padding: '4px 8px', fontSize: 14, borderRadius: 3, border: '1px solid #555', background: '#1a1a1a', color: '#eee', width: '100%', boxSizing: 'border-box' }}
                />
              </label>
            ))}
            <button
              onClick={() => handleRallyWinner(teamIndex)}
              disabled={servePhase}
              style={{ padding: '5px 12px', fontSize: 13, cursor: servePhase ? 'default' : 'pointer', whiteSpace: 'nowrap', alignSelf: 'flex-end' }}
            >
              Rally Winner
            </button>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <button
          onClick={handleServe}
          disabled={!servePhase}
          style={{ padding: '5px 14px', fontSize: 13, cursor: servePhase ? 'pointer' : 'default' }}
        >
          Serve
        </button>
        {gameDisplay && (
          <span style={{ fontSize: 14, color: '#333' }}>
            <span style={{ fontFamily: 'monospace', color: '#000' }}>{gameDisplay.score}</span>
            {' '}{gameDisplay.server} serving to {gameDisplay.receiver}
          </span>
        )}
      </div>

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
          onFrameChange={setCurrentFrameIndex}
          ballCount={ballCount}
          storageKey={`pass2-pos-${projectId}`}
          previewCanvasRef={previewRef}
          bgPlateUrl={bgPlateUrl}
          rallyTimeline={{
            events: [
              ...rallies.map(r => ({ startFrame: r.start_frame, stopFrame: r.stop_frame, score: r.score })),
              ...(pendingRally ? [{ startFrame: pendingRally.start_frame, score: pendingRally.score }] : []),
            ],
            onMarkerClick: (frame) => playerRef.current?.seekToFrame(frame),
          }}
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
              <RadiusOverlay radius={annotations[fi]?.radius ?? 0} />
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
