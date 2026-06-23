import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { Pass5Track } from '../types/api'

export default function Pass5Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const playerRef = useRef<VideoPlayerHandle>(null)
  const tableRef = useRef<HTMLDivElement>(null)
  const [accepting, setAccepting] = useState(false)
  const [currentFrame, setCurrentFrame] = useState(0)
  const [deletedIds, setDeletedIds] = useState<Set<number>>(new Set())
  const [deleteHistory, setDeleteHistory] = useState<number[]>([])
  const [dirty, setDirty] = useState(false)
  const [selectedTrackId, setSelectedTrackId] = useState<number | null>(null)
  const [hoveredTrackId, setHoveredTrackId] = useState<number | null>(null)
  const hasEnteredSelectedRef = useRef(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null)
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null)
  const [pendingRectIds, setPendingRectIds] = useState<number[] | null>(null)
  const isDraggingRef = useRef(false)
  const wasDraggingRef = useRef(false)

  const { data: trackData, isLoading } = useQuery({
    queryKey: ['pass5-tracks', projectId],
    queryFn: () => api.getPass5Tracks(projectId!),
  })

  const { data: savedCorrections } = useQuery({
    queryKey: ['pass5-corrections', projectId],
    queryFn: () => api.getPass5Corrections(projectId!),
  })

  const correctionsLoadedRef = useRef(false)
  useEffect(() => {
    if (!savedCorrections || correctionsLoadedRef.current) return
    correctionsLoadedRef.current = true
    const ids = savedCorrections.deleted_track_ids ?? []
    if (ids.length > 0) {
      setDeletedIds(new Set(ids))
      setDeleteHistory(ids)
    }
  }, [savedCorrections])

  // Pass 0 artifacts for median background image (use the middle median as representative).
  const { data: pass0Artifacts } = useQuery({
    queryKey: ['pass0-artifacts', projectId],
    queryFn: () => api.getPass0Artifacts(projectId!),
  })

  const bgMedianUrl = useMemo(() => {
    const pngs = (pass0Artifacts?.data ?? [])
      .filter((a) => a.artifact_role === 'raw' && a.artifact_type === 'png')
      .sort((a, b) => a.path.localeCompare(b.path))
    const mid = pngs[Math.floor(pngs.length / 2)]
    return mid ? api.artifactUrl(mid.id) : null
  }, [pass0Artifacts])

  // Pass 4 detections for ball overlay.
  const { data: detectionsData } = useQuery({
    queryKey: ['pass4-detections', projectId],
    queryFn: () => api.getPass4Detections(projectId!),
  })

  // Pass 2 result for fps and frame dimensions.
  const { data: pass2Meta } = useQuery({
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

  const { data: pass2Corrections } = useQuery({
    queryKey: ['pass2-corrections', projectId],
    queryFn: () => api.getPass2Corrections(projectId!),
  })
  const rallies = pass2Corrections?.data?.rally ?? []

  const { data: passStatus } = useQuery({
    queryKey: ['pass5-status', projectId],
    queryFn: () => api.getPass(projectId!, 'pass5'),
    refetchInterval: (query) => {
      const state = query.state.data?.data?.state
      return state === 'running' || state === 'queued' ? 2000 : false
    },
  })
  const passState = passStatus?.data?.state ?? null
  const isInFlight = passState === 'running' || passState === 'queued'

  const savePass5 = useMutation({
    mutationFn: () => api.savePass5Corrections(projectId!, [...deletedIds]),
    onSuccess: () => setDirty(false),
  })

  const acceptPass5 = useMutation({
    mutationFn: async () => {
      if (dirty) await api.savePass5Corrections(projectId!, [...deletedIds])
      return api.acceptPass5(projectId!)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  const cancelPass5 = useMutation({
    mutationFn: () => api.cancelPass5(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pass5-status', projectId] })
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const rerunPass5 = useMutation({
    mutationFn: () => api.runPass5(projectId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pass5-status', projectId] })
      qc.invalidateQueries({ queryKey: ['pass5-tracks', projectId] })
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })

  const tracks: Pass5Track[] = (trackData?.tracks ?? []).filter(t => !deletedIds.has(t.id))

  // Per-frame ball detection index (OpenCV frame numbers), restricted to detections used in a track.
  const ballDetections = useMemo(() => {
    if (!detectionsData?.detections) return {}
    const trackKeys = new Set<string>()
    for (const track of tracks) {
      for (const d of track.detections) trackKeys.add(`${d.frame}:${d.cx}:${d.cy}`)
    }
    const out: Record<number, { cx: number; cy: number; radius: number }[]> = {}
    for (const d of detectionsData.detections) {
      if (!trackKeys.has(`${d.frame}:${d.cx}:${d.cy}`)) continue
      if (!out[d.frame]) out[d.frame] = []
      out[d.frame].push({ cx: d.cx, cy: d.cy, radius: d.radius })
    }
    return out
  }, [detectionsData, tracks])

  // Clear selection when playback moves outside the selected track's range.
  useEffect(() => {
    hasEnteredSelectedRef.current = false
  }, [selectedTrackId])

  useEffect(() => {
    if (selectedTrackId === null) return
    const sel = tracks.find(t => t.id === selectedTrackId)
    if (!sel) { setSelectedTrackId(null); return }
    const currentFi = currentFrame - 1
    const inRange = currentFi >= sel.first_frame && currentFi <= sel.last_frame
    if (inRange) {
      hasEnteredSelectedRef.current = true
    } else if (hasEnteredSelectedRef.current) {
      setSelectedTrackId(null)
    }
  }, [currentFrame, selectedTrackId, tracks])

  const fps = pass2Meta?.fps ?? 30
  const bgWidth = pass2Meta?.bg_width ?? 960
  const bgHeight = pass2Meta?.bg_height ?? 540

  const pendingRectSet = useMemo(() => new Set(pendingRectIds ?? []), [pendingRectIds])

  // Track paths for VideoPlayer overlay: smooth array → frame-indexed detections format.
  const trackPaths = useMemo(() =>
    tracks.map(track => ({
      id: track.id,
      highlighted: track.id === selectedTrackId,
      detections: track.smooth.map((pt, i) => ({
        frame: track.smooth_first_frame + i,
        cx: pt[0],
        cy: pt[1],
      })),
    })),
  [tracks, selectedTrackId])

  // Per-track count of other tracks that share at least one frame.
  const overlapCounts = useMemo(() => {
    const counts: Record<number, number> = {}
    for (const a of tracks) {
      let count = 0
      for (const b of tracks) {
        if (b.id !== a.id && a.first_frame <= b.last_frame && b.first_frame <= a.last_frame) count++
      }
      counts[a.id] = count
    }
    return counts
  }, [tracks])

  // Tracks visible at the current frame: those that contain it or have an endpoint within ~0.25 s.
  const visibleTrackIds = useMemo(() => {
    const currentFi = currentFrame - 1  // convert browser→OpenCV numbering
    const nearWindow = Math.round(0.25 * fps)
    const ids = new Set<number>()
    for (const track of tracks) {
      const inTrack  = currentFi >= track.first_frame && currentFi <= track.last_frame
      const nearStart = Math.abs(track.first_frame - currentFi) <= nearWindow
      const nearEnd   = Math.abs(track.last_frame  - currentFi) <= nearWindow
      if (inTrack || nearStart || nearEnd) ids.add(track.id)
    }
    return ids
  }, [tracks, currentFrame, fps])

  // Auto-scroll table so the first highlighted row has at most 3 rows above it visible.
  useEffect(() => {
    const container = tableRef.current
    if (!container) return
    const firstIdx = tracks.findIndex(track => visibleTrackIds.has(track.id))
    if (firstIdx < 0) return
    const rows = container.querySelectorAll('tbody tr')
    const targetRow = rows[firstIdx] as HTMLElement | undefined
    if (!targetRow) return
    const rowHeight = targetRow.offsetHeight || 24
    container.scrollTop = Math.max(0, (firstIdx - 3) * rowHeight)
  }, [visibleTrackIds, tracks])

  const deleteTrack = (id: number) => {
    setDeletedIds(prev => new Set([...prev, id]))
    setDeleteHistory(prev => [...prev, id])
    setDirty(true)
  }

  const undoDelete = () => {
    setDeleteHistory(prev => {
      if (prev.length === 0) return prev
      const last = prev[prev.length - 1]
      setDeletedIds(ids => { const next = new Set(ids); next.delete(last); return next })
      return prev.slice(0, -1)
    })
  }

  const confirmRectDelete = () => {
    if (!pendingRectIds) return
    pendingRectIds.forEach(id => deleteTrack(id))
    setPendingRectIds(null)
  }

  const toSvgCoords = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    if (!svg) return { x: 0, y: 0 }
    const r = svg.getBoundingClientRect()
    return {
      x: (e.clientX - r.left) * bgWidth / r.width,
      y: (e.clientY - r.top) * bgHeight / r.height,
    }
  }

  const handleSvgMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (pendingRectIds !== null) return
    isDraggingRef.current = false
    const pt = toSvgCoords(e)
    setDragStart(pt)
    setDragCurrent(pt)
  }

  const handleSvgMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!dragStart) return
    isDraggingRef.current = true
    setDragCurrent(toSvgCoords(e))
  }

  const handleSvgMouseUp = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!dragStart || !dragCurrent) return
    const dx = dragCurrent.x - dragStart.x
    const dy = dragCurrent.y - dragStart.y
    const isRealDrag = isDraggingRef.current && Math.abs(dx) > 5 && Math.abs(dy) > 5
    setDragStart(null)
    setDragCurrent(null)
    if (!isRealDrag) return
    wasDraggingRef.current = true
    setTimeout(() => { wasDraggingRef.current = false }, 0)
    const x1 = Math.min(dragStart.x, dragCurrent.x)
    const x2 = Math.max(dragStart.x, dragCurrent.x)
    const y1 = Math.min(dragStart.y, dragCurrent.y)
    const y2 = Math.max(dragStart.y, dragCurrent.y)
    // Hit-test against smooth points
    const ids = tracks
      .filter(track => track.smooth.some(([x, y]) => x >= x1 && x <= x2 && y >= y1 && y <= y2))
      .map(track => track.id)
    if (ids.length > 0) setPendingRectIds(ids)
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setPendingRectIds(null); setDragStart(null); setDragCurrent(null) }
      if (e.ctrlKey && e.key === 'z') { e.preventDefault(); undoDelete() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Pass 5 — Track Building</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {trackData && (
            <span style={{ fontSize: 13, color: '#555' }}>
              {tracks.length} track{tracks.length !== 1 ? 's' : ''}
              {deletedIds.size > 0 && <span style={{ color: '#c00' }}> ({deletedIds.size} deleted)</span>}
              {' · '}{trackData.fps} fps
            </span>
          )}
          {isInFlight && (
            <button
              onClick={() => cancelPass5.mutate()}
              disabled={cancelPass5.isPending}
              style={{ padding: '6px 18px', background: '#c00', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
            >
              {cancelPass5.isPending ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {!isInFlight && (
            <button
              onClick={() => rerunPass5.mutate()}
              disabled={rerunPass5.isPending}
              style={{ padding: '6px 18px', cursor: 'pointer' }}
            >
              {rerunPass5.isPending ? 'Queuing…' : 'Re-run Pass 5'}
            </button>
          )}
          <button
            onClick={() => savePass5.mutate()}
            disabled={!dirty || savePass5.isPending || accepting || isInFlight}
            style={{ padding: '6px 18px', cursor: 'pointer' }}
          >
            {savePass5.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={() => { setAccepting(true); acceptPass5.mutate() }}
            disabled={accepting || acceptPass5.isPending || !trackData || isInFlight}
            style={{ padding: '6px 18px', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
          >
            {accepting ? 'Accepting…' : 'Accept Pass 5 →'}
          </button>
        </div>
      </div>

      <p style={{ color: '#666', fontSize: 13, margin: '0 0 12px' }}>
        Magenta circle: ball detection in current frame.
        Cyan polyline: tracks containing the current frame or with an endpoint within 8 frames (full track shown).
        Click a track path to delete it. Drag a rectangle on the plot above to select and delete multiple tracks.
      </p>

      {bgMedianUrl && bgWidth && bgHeight && tracks.length > 0 && (
        <div style={{ position: 'relative', marginBottom: 12, border: '1px solid #ccc', borderRadius: 4, overflow: 'hidden' }}>
          <img
            src={bgMedianUrl}
            alt="Background"
            draggable={false}
            style={{ display: 'block', width: '100%', filter: 'grayscale(1) brightness(2.2)', userSelect: 'none' }}
          />
          <svg
            ref={svgRef}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', cursor: pendingRectIds ? 'default' : 'crosshair', userSelect: 'none' }}
            viewBox={`0 0 ${bgWidth} ${bgHeight}`}
            preserveAspectRatio="xMidYMid meet"
            onMouseDown={handleSvgMouseDown}
            onMouseMove={handleSvgMouseMove}
            onMouseUp={handleSvgMouseUp}
            onMouseLeave={() => { setDragStart(null); setDragCurrent(null) }}
          >
            <rect x={0} y={0} width={bgWidth} height={bgHeight} fill="transparent" />
            {tracks.map((track) => {
              const pts = track.smooth.map(([x, y]) => `${x},${y}`).join(' ')
              const isPending = pendingRectSet.has(track.id)
              const hovered = track.id === hoveredTrackId && !pendingRectIds
              const stroke = isPending ? '#ffff00' : hovered ? '#cc9900' : '#ff3300'
              return (
                <polyline
                  key={track.id}
                  points={pts}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={6}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  style={{ cursor: pendingRectIds ? 'default' : 'pointer' }}
                  onClick={() => { if (!wasDraggingRef.current && !pendingRectIds) deleteTrack(track.id) }}
                  onMouseEnter={() => { if (!pendingRectIds) setHoveredTrackId(track.id) }}
                  onMouseLeave={() => setHoveredTrackId(null)}
                >
                  <title>Track {track.id + 1} — click to delete</title>
                </polyline>
              )
            })}
            {dragStart && dragCurrent && (() => {
              const x = Math.min(dragStart.x, dragCurrent.x)
              const y = Math.min(dragStart.y, dragCurrent.y)
              const w = Math.abs(dragCurrent.x - dragStart.x)
              const h = Math.abs(dragCurrent.y - dragStart.y)
              return (
                <rect x={x} y={y} width={w} height={h}
                  fill="rgba(255,255,0,0.12)" stroke="#ffff00" strokeWidth={2}
                  strokeDasharray="10 5" pointerEvents="none" />
              )
            })()}
          </svg>
          {pendingRectIds && (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(0,0,0,0.45)',
            }}>
              <div style={{
                background: '#fff', borderRadius: 8, padding: '24px 32px', boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
                textAlign: 'center', minWidth: 260,
              }}>
                <div style={{ fontSize: 15, marginBottom: 20 }}>
                  Delete <strong>{pendingRectIds.length}</strong> track{pendingRectIds.length !== 1 ? 's' : ''} in selection?
                </div>
                <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                  <button
                    onClick={confirmRectDelete}
                    style={{ padding: '6px 20px', background: '#c00', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
                  >
                    Delete {pendingRectIds.length}
                  </button>
                  <button
                    onClick={() => setPendingRectIds(null)}
                    style={{ padding: '6px 20px', cursor: 'pointer' }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <VideoPlayer
        ref={playerRef}
        videoUrl={api.videoUrl(projectId!)}
        fps={fps}
        bgWidth={bgWidth}
        bgHeight={bgHeight}
        totalFrames={0}
        ballDetections={ballDetections}
        segmentPaths={trackPaths}
        onFrameChange={setCurrentFrame}
        storageKey={`pass5-pos-${projectId}`}
        rallyTimeline={{
          events: rallies.map(r => ({ startFrame: r.start_frame, stopFrame: r.stop_frame, score: r.score })),
          onMarkerClick: (frame) => playerRef.current?.seekToFrame(frame),
        }}
      />

      {isLoading ? (
        <div style={{ color: '#888', marginTop: 16 }}>Loading tracks…</div>
      ) : tracks.length === 0 ? (
        <div style={{ color: '#888', marginTop: 16 }}>No tracks found.</div>
      ) : (
        <div ref={tableRef} style={{ maxHeight: 280, overflowY: 'auto', marginTop: 16 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
            <thead style={{ position: 'sticky', top: 0, background: '#f0f0f0', zIndex: 1 }}>
              <tr>
                <th style={th}>#</th>
                <th style={{ ...th, textAlign: 'center' }} />
                <th style={th}>Rally</th>
                <th style={th}>Overlaps</th>
                <th style={th}>First frame</th>
                <th style={th}>Last frame</th>
                <th style={th}>Detections</th>
                <th style={th}>Segments</th>
                <th style={th}>Bounces</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((track) => {
                const currentFi = currentFrame - 1
                const highlighted = currentFi >= track.first_frame && currentFi <= track.last_frame
                const selected = track.id === selectedTrackId
                return (
                  <tr
                    key={track.id}
                    style={{ borderBottom: '1px solid #eee', cursor: 'pointer', background: highlighted ? '#def' : undefined, fontWeight: selected ? 700 : undefined }}
                    onClick={() => { setSelectedTrackId(track.id); playerRef.current?.seekToFrame(track.first_frame + 1) }}
                    title="Click to seek to track start"
                  >
                    <td style={td}>{track.id + 1}</td>
                    <td style={{ ...td, textAlign: 'center' }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteTrack(track.id) }}
                        title="Delete track"
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#c00', fontSize: 14, lineHeight: 1, padding: '0 4px' }}
                      >✕</button>
                    </td>
                    <td style={td}>{track.rally_id + 1}</td>
                    <td style={{ ...td, color: overlapCounts[track.id] > 0 ? '#c60' : '#444' }}>{overlapCounts[track.id]}</td>
                    <td style={td}>{track.first_frame}</td>
                    <td style={td}>{track.last_frame}</td>
                    <td style={td}>{track.n_detections}</td>
                    <td style={td}>{track.n_segments}</td>
                    <td style={td}>{track.intersections.length}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '4px 12px', textAlign: 'right', fontWeight: 600, color: '#333' }
const td: React.CSSProperties = { padding: '3px 12px', textAlign: 'right', color: '#444' }
