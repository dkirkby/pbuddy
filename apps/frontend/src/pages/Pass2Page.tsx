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

function makeGame(names: PlayerNames, farTeamServesFirst: boolean): PickleballDoublesGame {
  return new PickleballDoublesGame(
    names.far_team_right, names.far_team_left,
    names.near_team_right, names.near_team_left,
    farTeamServesFirst,
  )
}

function replayGame(
  rallies: RallyRecord[],
  names: PlayerNames,
  farTeamServesFirst: boolean,
): { updatedRallies: RallyRecord[]; finalGame: PickleballDoublesGame } {
  const sorted = [...rallies].sort((a, b) => a.start_frame - b.start_frame)
  const game = makeGame(names, farTeamServesFirst)
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
    far_team_right: '',
    far_team_left: '',
    near_team_right: '',
    near_team_left: '',
  })
  const [namesFinalized, setNamesFinalized] = useState(false)
  const [farTeamServesFirst, setFarTeamServesFirst] = useState<boolean | null>(null)
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
      if (Object.values(correctionsResp.data.player_names).every(v => v.trim())) {
        setNamesFinalized(true)
      }
    }
    if (correctionsResp.data.far_team_serves_first !== undefined) {
      setFarTeamServesFirst(correctionsResp.data.far_team_serves_first ?? null)
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
  }, [playerNames.far_team_right, playerNames.far_team_left, playerNames.near_team_right, playerNames.near_team_left])

  // Derive current physical positions from canonical names + how many times each team has won while serving.
  // Odd win count means that team's right/left have physically swapped from their initial positions.
  const { displayNames, farSwapped, nearSwapped } = useMemo(() => {
    const farWins  = rallies.filter(r => r.servingTeamWinsRally &&
      (r.serverName === playerNames.far_team_right || r.serverName === playerNames.far_team_left)).length
    const nearWins = rallies.filter(r => r.servingTeamWinsRally &&
      (r.serverName === playerNames.near_team_right || r.serverName === playerNames.near_team_left)).length
    const fs = farWins  % 2 === 1
    const ns = nearWins % 2 === 1
    return {
      displayNames: {
        far_team_right:  fs ? playerNames.far_team_left  : playerNames.far_team_right,
        far_team_left:   fs ? playerNames.far_team_right : playerNames.far_team_left,
        near_team_right: ns ? playerNames.near_team_left : playerNames.near_team_right,
        near_team_left:  ns ? playerNames.near_team_right: playerNames.near_team_left,
      },
      farSwapped: fs,
      nearSwapped: ns,
    }
  }, [rallies, playerNames])

  // Reset farTeamServesFirst when all rally data is cleared.
  useEffect(() => {
    if (rallies.length === 0 && pendingRally === null) {
      setFarTeamServesFirst(null)
    }
  }, [rallies.length, pendingRally])

  // Derived game state at the current frame position.
  const gameDisplay = useMemo(() => {
    if (farTeamServesFirst === null) return null
    const game = makeGame(playerNames, farTeamServesFirst)
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
  }, [currentFrameIndex, rallies, pendingRally, farTeamServesFirst, playerNames])

  const phaseInfo = useMemo(
    () => computePhaseInfo(currentFrameIndex, rallies, pendingRally, resultData?.fps ?? 30),
    [currentFrameIndex, rallies, pendingRally, resultData?.fps],
  )
  const servePhase = phaseInfo.isServePhase

  // Map a display-order key back to the canonical playerNames key, accounting for swaps.
  function canonicalKey(displayKey: keyof PlayerNames): keyof PlayerNames {
    if (displayKey === 'far_team_right')  return farSwapped ? 'far_team_left'  : 'far_team_right'
    if (displayKey === 'far_team_left')   return farSwapped ? 'far_team_right' : 'far_team_left'
    if (displayKey === 'near_team_right') return nearSwapped ? 'near_team_left' : 'near_team_right'
    return nearSwapped ? 'near_team_right' : 'near_team_left'  // near_team_left
  }

  function handleNameEdit(displayKey: keyof PlayerNames, value: string) {
    setPlayerNames(prev => ({ ...prev, [canonicalKey(displayKey)]: value }))
    setDirty(true)
  }

  // Handle a Serve button click. isFarTeam identifies which team row was clicked.
  // When farTeamServesFirst is null (first serve ever), the clicked team becomes the first server.
  function handleServeClick(isFarTeam: boolean) {
    let resolvedFarFirst = farTeamServesFirst
    if (resolvedFarFirst === null) {
      resolvedFarFirst = isFarTeam
      setFarTeamServesFirst(isFarTeam)
    }

    const { targetType, targetIndex } = phaseInfo
    if (targetType === 'new-serve') {
      const { finalGame } = replayGame(rallies, playerNames, resolvedFarFirst)
      const pos = finalGame.positions
      setPendingRally({
        score: finalGame.toString(),
        start_frame: currentFrameIndex,
        serverName: pos[finalGame.serverPosition],
        receiverName: pos[finalGame.receiverPosition],
      })
    } else if (targetType === 'existing-serve') {
      const updated = [...rallies]
      updated[targetIndex] = { ...updated[targetIndex], start_frame: currentFrameIndex }
      setRallies(replayGame(updated, playerNames, resolvedFarFirst).updatedRallies)
    } else if (targetType === 'pending-serve') {
      setPendingRally(prev => prev ? { ...prev, start_frame: currentFrameIndex } : null)
    }

    // Start playback if paused.
    playerRef.current?.play()
    setDirty(true)
  }

  function handleRallyWinner(isFarTeamWinner: boolean) {
    if (farTeamServesFirst === null) return
    const { targetType, targetIndex } = phaseInfo
    if (targetType === 'pending-end') {
      if (!pendingRally) return
      const serverIsFarTeam = pendingRally.serverName === playerNames.far_team_right || pendingRally.serverName === playerNames.far_team_left
      const servingTeamWinsRally = isFarTeamWinner === serverIsFarTeam
      const record: RallyRecord = { ...pendingRally, stop_frame: currentFrameIndex, servingTeamWinsRally }
      setRallies(replayGame([...rallies, record], playerNames, farTeamServesFirst).updatedRallies)
      setPendingRally(null)
    } else if (targetType === 'existing-end') {
      const serverIsFarTeam = rallies[targetIndex].serverName === playerNames.far_team_right || rallies[targetIndex].serverName === playerNames.far_team_left
      const servingTeamWinsRally = isFarTeamWinner === serverIsFarTeam
      const updated = [...rallies]
      updated[targetIndex] = { ...updated[targetIndex], stop_frame: currentFrameIndex, servingTeamWinsRally }
      setRallies(replayGame(updated, playerNames, farTeamServesFirst).updatedRallies)
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

  function handleDeleteRally(sortedAscIndex: number) {
    if (farTeamServesFirst === null) return
    const sorted = [...rallies].sort((a, b) => a.start_frame - b.start_frame)
    sorted.splice(sortedAscIndex, 1)
    setRallies(sorted.length === 0 ? [] : replayGame(sorted, playerNames, farTeamServesFirst).updatedRallies)
    setDirty(true)
  }

  function handleRallyStartFrameEdit(sortedAscIndex: number) {
    if (farTeamServesFirst === null) return
    const sorted = [...rallies].sort((a, b) => a.start_frame - b.start_frame)
    const rally = sorted[sortedAscIndex]
    const prevStop = sortedAscIndex > 0 ? sorted[sortedAscIndex - 1].stop_frame : -Infinity
    if (currentFrameIndex > prevStop && currentFrameIndex < rally.stop_frame) {
      sorted[sortedAscIndex] = { ...rally, start_frame: currentFrameIndex }
      setRallies(replayGame(sorted, playerNames, farTeamServesFirst).updatedRallies)
      playerRef.current?.seekToFrame(currentFrameIndex)
      setDirty(true)
    }
  }

  function handleRallyStopFrameEdit(sortedAscIndex: number) {
    if (farTeamServesFirst === null) return
    const sorted = [...rallies].sort((a, b) => a.start_frame - b.start_frame)
    const rally = sorted[sortedAscIndex]
    const nextStart = sortedAscIndex < sorted.length - 1
      ? sorted[sortedAscIndex + 1].start_frame
      : pendingRally !== null ? pendingRally.start_frame : Infinity
    if (currentFrameIndex > rally.start_frame && currentFrameIndex < nextStart) {
      sorted[sortedAscIndex] = { ...rally, stop_frame: currentFrameIndex }
      setRallies(replayGame(sorted, playerNames, farTeamServesFirst).updatedRallies)
      playerRef.current?.seekToFrame(currentFrameIndex)
      setDirty(true)
    }
  }

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
      await api.savePass2Annotations(projectId!, annotations, patches, playerNames, farTeamServesFirst, rallies)
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

      {(() => {
        const rows: { isFar: boolean; keys: [keyof PlayerNames, keyof PlayerNames]; labels: [string, string] }[] = [
          { isFar: true,  keys: ['far_team_right',  'far_team_left'],   labels: ['Far Team Right',  'Far Team Left'] },
          { isFar: false, keys: ['near_team_left',  'near_team_right'], labels: ['Near Team Left',  'Near Team Right'] },
        ]

        // Whether a name cell is clickable as a serve trigger.
        // Before first serve: only right-side players (far_team_right, near_team_right) are eligible.
        // After first serve: only the current server's cell.
        const isServeClickable = (displayKey: keyof PlayerNames): boolean => {
          if (!servePhase) return false
          if (farTeamServesFirst === null) {
            return displayKey === 'far_team_right' || displayKey === 'near_team_right'
          }
          return gameDisplay?.server === displayNames[displayKey]
        }

        if (!namesFinalized) {
          const allFilled = Object.values(playerNames).every(v => v.trim())
          return (
            <div style={{ marginBottom: 8 }}>
              {rows.map(({ keys, labels }) => (
                <div key={keys[0]} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginBottom: 8 }}>
                  {keys.map((key, ki) => (
                    <label key={key} style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13, flex: '1 1 0' }}>
                      <span style={{ color: '#444' }}>{labels[ki]}</span>
                      <input
                        type="text"
                        value={playerNames[key]}
                        onChange={(e) => { setPlayerNames(prev => ({ ...prev, [key]: e.target.value })); setDirty(true) }}
                        placeholder={labels[ki]}
                        style={{
                          padding: '4px 8px', fontSize: 14, borderRadius: 3,
                          border: '1px solid #555', background: '#1a1a1a',
                          color: '#eee', width: '100%', boxSizing: 'border-box',
                        }}
                      />
                    </label>
                  ))}
                </div>
              ))}
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                <button
                  onClick={() => setNamesFinalized(true)}
                  disabled={!allFilled}
                  style={{ padding: '5px 14px', fontSize: 13, cursor: allFilled ? 'pointer' : 'default' }}
                >
                  Finalize Names
                </button>
              </div>
            </div>
          )
        }

        return (
          <div style={{ marginBottom: 8 }}>
            {rows.map(({ isFar, keys, labels }) => {
              const rallyWinnerEnabled = !servePhase && farTeamServesFirst !== null
              return (
                <div key={keys[0]} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginBottom: 8 }}>
                  {keys.map((key, ki) => {
                    const clickable = isServeClickable(key)
                    return (
                      <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13, flex: '1 1 0' }}>
                        <span style={{ color: '#444' }}>{labels[ki]}</span>
                        <div
                          onClick={clickable ? () => handleServeClick(isFar) : undefined}
                          style={{
                            padding: '4px 8px', fontSize: 14, borderRadius: 3,
                            border: '1px solid #555',
                            background: clickable ? '#8b0000' : '#1a1a1a',
                            color: '#eee', cursor: clickable ? 'pointer' : 'default',
                            userSelect: 'none',
                          }}
                        >
                          {displayNames[key]}
                        </div>
                      </div>
                    )
                  })}
                  <button
                    onClick={() => handleRallyWinner(isFar)}
                    disabled={!rallyWinnerEnabled}
                    style={{
                      padding: '5px 12px', fontSize: 13, whiteSpace: 'nowrap', alignSelf: 'flex-end',
                      cursor: rallyWinnerEnabled ? 'pointer' : 'default',
                      background: rallyWinnerEnabled ? '#8b0000' : undefined,
                      color: rallyWinnerEnabled ? '#fff' : undefined,
                      border: rallyWinnerEnabled ? '1px solid #cc0000' : undefined,
                    }}
                  >
                    Rally Winner
                  </button>
                </div>
              )
            })}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
              {gameDisplay ? (
                <span style={{ fontSize: 14, color: '#ccc' }}>
                  <span style={{ fontFamily: 'monospace' }}>{gameDisplay.score}</span>
                  {' '}{gameDisplay.server} serving to {gameDisplay.receiver}
                </span>
              ) : servePhase ? (
                <span style={{ fontSize: 14, color: '#888' }}>Click a highlighted name to mark the first serve</span>
              ) : null}
            </div>
          </div>
        )
      })()}

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
      {/* Rally table */}
      {rallies.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 16 }}>
          <thead>
            <tr style={{ color: '#888', textAlign: 'left', borderBottom: '1px solid #333' }}>
              <th style={{ padding: '4px 8px', fontWeight: 'normal' }}>Start</th>
              <th style={{ padding: '4px 8px', fontWeight: 'normal' }}>Stop</th>
              <th style={{ padding: '4px 8px', fontWeight: 'normal' }}>Description</th>
              <th style={{ padding: '4px 8px', fontWeight: 'normal' }}></th>
            </tr>
          </thead>
          <tbody>
            {[...rallies]
              .sort((a, b) => b.start_frame - a.start_frame)
              .map((rally, displayIdx) => {
                const ascIdx = rallies.length - 1 - displayIdx
                return (
                  <tr key={rally.start_frame} style={{ borderBottom: '1px solid #222' }}>
                    <td
                      style={{ padding: '4px 8px', fontFamily: 'monospace', cursor: 'pointer', userSelect: 'none', textDecoration: 'underline dotted #555' }}
                      onDoubleClick={() => handleRallyStartFrameEdit(ascIdx)}
                      title="Double-click to set to current frame"
                    >
                      {rally.start_frame}
                    </td>
                    <td
                      style={{ padding: '4px 8px', fontFamily: 'monospace', cursor: 'pointer', userSelect: 'none', textDecoration: 'underline dotted #555' }}
                      onDoubleClick={() => handleRallyStopFrameEdit(ascIdx)}
                      title="Double-click to set to current frame"
                    >
                      {rally.stop_frame}
                    </td>
                    <td style={{ padding: '4px 8px', color: '#ccc' }}>
                      {rally.score} {rally.serverName} serving to {rally.receiverName}
                    </td>
                    <td style={{ padding: '4px 8px' }}>
                      <button
                        onClick={() => handleDeleteRally(ascIdx)}
                        style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', fontSize: 16, padding: '0 4px', lineHeight: 1 }}
                        title="Remove rally"
                      >×</button>
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      )}
    </div>
  )
}
