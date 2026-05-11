import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { VideoPlayer } from '../components/VideoPlayer'
import type { VideoPlayerHandle } from '../components/VideoPlayer'
import type { Pass5Segment } from '../types/api'

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
  const [selectedSegId, setSelectedSegId] = useState<number | null>(null)
  const [hoveredSegId, setHoveredSegId] = useState<number | null>(null)
  const hasEnteredSelectedRef = useRef(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null)
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null)
  const [pendingRectIds, setPendingRectIds] = useState<number[] | null>(null)
  const isDraggingRef = useRef(false)
  const wasDraggingRef = useRef(false)

  const { data: segData, isLoading } = useQuery({
    queryKey: ['pass5-segments', projectId],
    queryFn: () => api.getPass5Segments(projectId!),
  })

  const { data: savedCorrections } = useQuery({
    queryKey: ['pass5-corrections', projectId],
    queryFn: () => api.getPass5Corrections(projectId!),
  })

  const correctionsLoadedRef = useRef(false)
  useEffect(() => {
    if (!savedCorrections || correctionsLoadedRef.current) return
    correctionsLoadedRef.current = true
    const ids = savedCorrections.deleted_segment_ids ?? []
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

  const segments: Pass5Segment[] = (segData?.segments ?? []).filter(s => !deletedIds.has(s.id))

  // Per-frame ball detection index (OpenCV frame numbers), restricted to detections used in a segment.
  const ballDetections = useMemo(() => {
    if (!detectionsData?.detections) return {}
    const segmentKeys = new Set<string>()
    for (const seg of segments) {
      for (const d of seg.detections) segmentKeys.add(`${d.frame}:${d.cx}:${d.cy}`)
    }
    const out: Record<number, { cx: number; cy: number; radius: number }[]> = {}
    for (const d of detectionsData.detections) {
      if (!segmentKeys.has(`${d.frame}:${d.cx}:${d.cy}`)) continue
      if (!out[d.frame]) out[d.frame] = []
      out[d.frame].push({ cx: d.cx, cy: d.cy, radius: d.radius })
    }
    return out
  }, [detectionsData, segments])

  // Clear selection when playback moves outside the selected segment's range,
  // but only after the frame has first entered the segment (avoids clearing on the
  // initial seek when clicking a row while the video is outside that segment).
  useEffect(() => {
    hasEnteredSelectedRef.current = false
  }, [selectedSegId])

  useEffect(() => {
    if (selectedSegId === null) return
    const sel = segments.find(s => s.id === selectedSegId)
    if (!sel) { setSelectedSegId(null); return }
    const currentFi = currentFrame - 1
    const inRange = currentFi >= sel.first_frame && currentFi <= sel.last_frame
    if (inRange) {
      hasEnteredSelectedRef.current = true
    } else if (hasEnteredSelectedRef.current) {
      setSelectedSegId(null)
    }
  }, [currentFrame, selectedSegId, segments])

  const fps = pass2Meta?.fps ?? 30
  const bgWidth = pass2Meta?.bg_width ?? 960
  const bgHeight = pass2Meta?.bg_height ?? 540

  const pendingRectSet = useMemo(() => new Set(pendingRectIds ?? []), [pendingRectIds])

  // Segment paths for polyline rendering.
  const segmentPaths = useMemo(() =>
    segments.map(seg => ({ id: seg.id, highlighted: seg.id === selectedSegId, detections: seg.detections })),
  [segments, selectedSegId])

  // Per-segment count of other segments that share at least one frame.
  const overlapCounts = useMemo(() => {
    const counts: Record<number, number> = {}
    for (const a of segments) {
      let count = 0
      for (const b of segments) {
        if (b.id !== a.id && a.first_frame <= b.last_frame && b.first_frame <= a.last_frame) count++
      }
      counts[a.id] = count
    }
    return counts
  }, [segments])

  // Segments visible at the current frame: those that contain it or have an endpoint within ~0.25 s.
  const visibleSegmentIds = useMemo(() => {
    const currentFi = currentFrame - 1  // convert browser→OpenCV numbering
    const nearWindow = Math.round(0.25 * fps)
    const ids = new Set<number>()
    for (const seg of segments) {
      const inSeg = currentFi >= seg.first_frame && currentFi <= seg.last_frame
      const nearStart = Math.abs(seg.first_frame - currentFi) <= nearWindow
      const nearEnd = Math.abs(seg.last_frame - currentFi) <= nearWindow
      if (inSeg || nearStart || nearEnd) ids.add(seg.id)
    }
    return ids
  }, [segments, currentFrame, fps])

  // Auto-scroll the table so the first highlighted row has at most 3 rows above it visible.
  useEffect(() => {
    const container = tableRef.current
    if (!container) return
    const firstIdx = segments.findIndex(seg => visibleSegmentIds.has(seg.id))
    if (firstIdx < 0) return
    const rows = container.querySelectorAll('tbody tr')
    const targetRow = rows[firstIdx] as HTMLElement | undefined
    if (!targetRow) return
    const rowHeight = targetRow.offsetHeight || 24
    container.scrollTop = Math.max(0, (firstIdx - 3) * rowHeight)
  }, [visibleSegmentIds, segments])

  const deleteSegment = (id: number) => {
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
    pendingRectIds.forEach(id => deleteSegment(id))
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
    const ids = segments
      .filter(seg => seg.detections.some(d => d.cx >= x1 && d.cx <= x2 && d.cy >= y1 && d.cy <= y2))
      .map(seg => seg.id)
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
        <h1 style={{ margin: 0 }}>Pass 5 — Segment Building</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {segData && (
            <span style={{ fontSize: 13, color: '#555' }}>
              {segments.length} segment{segments.length !== 1 ? 's' : ''}
              {deletedIds.size > 0 && <span style={{ color: '#c00' }}> ({deletedIds.size} deleted)</span>}
              {' · '}gap ≤ {segData.max_gap_frames} frames
              {' · '}gate {segData.large_gate_px}px → {segData.small_gate_px}px
              {' · '}min length {segData.min_segment_length}
            </span>
          )}
          <button
            onClick={() => savePass5.mutate()}
            disabled={!dirty || savePass5.isPending || accepting}
            style={{ padding: '6px 18px', cursor: 'pointer' }}
          >
            {savePass5.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={() => { setAccepting(true); acceptPass5.mutate() }}
            disabled={accepting || acceptPass5.isPending || !segData}
            style={{ padding: '6px 18px', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', fontWeight: 600 }}
          >
            {accepting ? 'Accepting…' : 'Accept Pass 5 →'}
          </button>
        </div>
      </div>

      <p style={{ color: '#666', fontSize: 13, margin: '0 0 12px' }}>
        Magenta circle: ball detection in current frame.
        Cyan polyline: segments containing the current frame or with an endpoint within 8 frames (full segment shown).
        Click a segment path to delete it. Drag a rectangle on the plot above to select and delete multiple segments.
      </p>

      {bgMedianUrl && bgWidth && bgHeight && segments.length > 0 && (
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
            {segments.map((seg) => {
              const pts = seg.detections.map(d => `${d.cx},${d.cy}`).join(' ')
              const isPending = pendingRectSet.has(seg.id)
              const hovered = seg.id === hoveredSegId && !pendingRectIds
              const stroke = isPending ? '#ffff00' : hovered ? '#cc9900' : '#ff3300'
              return (
                <polyline
                  key={seg.id}
                  points={pts}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={6}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  style={{ cursor: pendingRectIds ? 'default' : 'pointer' }}
                  onClick={() => { if (!wasDraggingRef.current && !pendingRectIds) deleteSegment(seg.id) }}
                  onMouseEnter={() => { if (!pendingRectIds) setHoveredSegId(seg.id) }}
                  onMouseLeave={() => setHoveredSegId(null)}
                >
                  <title>Segment {seg.id + 1} — click to delete</title>
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
                  Delete <strong>{pendingRectIds.length}</strong> segment{pendingRectIds.length !== 1 ? 's' : ''} in selection?
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
        segmentPaths={segmentPaths}
        onFrameChange={setCurrentFrame}
        storageKey={`pass5-pos-${projectId}`}
        rallyTimeline={{
          events: rallies.map(r => ({ startFrame: r.start_frame, stopFrame: r.stop_frame, score: r.score })),
          onMarkerClick: (frame) => playerRef.current?.seekToFrame(frame),
        }}
      />

      {isLoading ? (
        <div style={{ color: '#888', marginTop: 16 }}>Loading segments…</div>
      ) : segments.length === 0 ? (
        <div style={{ color: '#888', marginTop: 16 }}>No segments found.</div>
      ) : (
        <div ref={tableRef} style={{ maxHeight: 280, overflowY: 'auto', marginTop: 16 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
            <thead style={{ position: 'sticky', top: 0, background: '#f0f0f0', zIndex: 1 }}>
              <tr>
                <th style={th}>#</th>
                <th style={{ ...th, textAlign: 'center' }} />
                <th style={th}>Overlaps</th>
                <th style={th}>First frame</th>
                <th style={th}>Last frame</th>
                <th style={th}>Detections</th>
                <th style={th}>Span (frames)</th>
                <th style={th}>Speed (px/fr)</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((seg) => {
                const currentFi = currentFrame - 1
                const highlighted = currentFi >= seg.first_frame && currentFi <= seg.last_frame
                const selected = seg.id === selectedSegId
                return (
                  <tr
                    key={seg.id}
                    style={{ borderBottom: '1px solid #eee', cursor: 'pointer', background: highlighted ? '#def' : undefined, fontWeight: selected ? 700 : undefined }}
                    onClick={() => { setSelectedSegId(seg.id); playerRef.current?.seekToFrame(seg.first_frame + 1) }}
                    title="Click to seek to segment start"
                  >
                    <td style={td}>{seg.id + 1}</td>
                    <td style={{ ...td, textAlign: 'center' }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteSegment(seg.id) }}
                        title="Delete segment"
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#c00', fontSize: 14, lineHeight: 1, padding: '0 4px' }}
                      >✕</button>
                    </td>
                    <td style={{ ...td, color: overlapCounts[seg.id] > 0 ? '#c60' : '#444' }}>{overlapCounts[seg.id]}</td>
                    <td style={td}>{seg.first_frame}</td>
                    <td style={td}>{seg.last_frame}</td>
                    <td style={td}>{seg.length}</td>
                    <td style={td}>{seg.last_frame - seg.first_frame + 1}</td>
                    <td style={td}>{seg.mean_speed_px_per_frame?.toFixed(1) ?? '—'}</td>
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
