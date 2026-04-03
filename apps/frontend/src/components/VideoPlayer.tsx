/**
 * VideoPlayer — HTML5 video with canvas overlay for detections and court lines.
 *
 * Buttons:  ⏮  ◀◀  ◀|  ▶/⏸  |▶  ▶▶  ?
 * Keys:     —   ⇧←  ←   Space   →   ⇧→  —
 *
 * ◀◀ / ▶▶  play at 2× in that direction while held (button or keyboard shortcut)
 * ◀| / |▶  step one frame per press; hold for continuous stepping
 * ⏮        rewind to the beginning
 */
import { Fragment, forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { CourtGeometry } from '../types/api'
import { computeVolumeOverlay } from '../lib/courtCamera'
import { BALL_PATCH_RADIUS, COURT_KV } from '../lib/dimensions'

interface Detection {
  cx: number; cy: number; a: number; b: number; angle: number
  area: number; bbox_x: number; bbox_y: number; bbox_w: number; bbox_h: number
}

interface BallDetection {
  cx: number; cy: number; radius: number
}

// ─── Court geometry helpers ──────────────────────────────────────────────────

const KV = COURT_KV
const COURT_LINES = [
  [0, 0, 1, 0], [1, 0, 1, 1], [1, 1, 0, 1], [0, 1, 0, 0], // boundary
  [0, 0.5, 1, 0.5],                                          // net
  [0, KV, 1, KV], [0, 1 - KV, 1, 1 - KV],                  // kitchen lines
  [0.5, 0, 0.5, KV], [0.5, 1 - KV, 0.5, 1],                // centre lines
] as const

function buildH(g: CourtGeometry): number[] {
  const { top_left: TL, top_right: TR, bottom_left: BL, bottom_right: BR } = g
  const A = TR.x - BR.x, B = BL.x - BR.x, C = TL.x - TR.x - BL.x + BR.x
  const D = TR.y - BR.y, E = BL.y - BR.y, F = TL.y - TR.y - BL.y + BR.y
  const det = A * E - B * D
  const gh = (C * E - B * F) / det
  const hh = (A * F - C * D) / det
  return [
    TR.x * (gh + 1) - TL.x, BL.x * (hh + 1) - TL.x, TL.x,
    TR.y * (gh + 1) - TL.y, BL.y * (hh + 1) - TL.y, TL.y,
    gh, hh, 1,
  ]
}

function applyH(H: number[], u: number, v: number): [number, number] {
  const w = H[6] * u + H[7] * v + 1
  return [(H[0] * u + H[1] * v + H[2]) / w, (H[3] * u + H[4] * v + H[5]) / w]
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}m ${sec.toString().padStart(2, '0')}s`
}

// ─── Types ───────────────────────────────────────────────────────────────────

const PATCH_RADIUS = BALL_PATCH_RADIUS  // bg-plate pixels; from dimensions.json

// Hollow-circle cursor that mirrors the canvas annotation marker.
const _cursorSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="21" height="21">'
  + '<circle cx="10.5" cy="10.5" r="8.5" fill="none" stroke="rgba(0,0,0,0.5)" stroke-width="2.5"/>'
  + '<circle cx="10.5" cy="10.5" r="8.5" fill="none" stroke="white" stroke-width="1.5"/>'
  + '</svg>'
const CIRCLE_CURSOR = `url("data:image/svg+xml,${encodeURIComponent(_cursorSvg)}") 10 10, crosshair`

export interface VideoPlayerHandle {
  seekToFrame: (frameIndex: number) => void
}

type PlaybackState = 'stopped' | 'playing' | 'fast-forward' | 'fast-reverse'

interface BallAnnotation {
  x: number
  y: number
  radius?: number
}

interface Props {
  videoUrl: string
  fps: number
  bgWidth: number
  bgHeight: number
  detections?: Record<number, Detection[]>
  ballDetections?: Record<number, BallDetection[]>
  courtGeometry?: CourtGeometry
  totalFrames: number
  annotations?: Record<number, BallAnnotation>
  onVideoClick?: (frameIndex: number, bgX: number, bgY: number, patchDataUrl: string | null, radius: number) => void
  onFrameChange?: (frameIndex: number) => void
  ballCount?: number
  storageKey?: string
  previewCanvasRef?: React.RefObject<HTMLCanvasElement>
  bgPlateUrl?: string
  staticOverlay?: HTMLCanvasElement | null
  segmentPaths?: Array<{ id: number; detections: { frame: number; cx: number; cy: number }[] }>
  rallyTimeline?: { events: Array<{ startFrame: number; stopFrame?: number; score?: string }>; onMarkerClick: (frame: number) => void }
}

// ─── Rally timeline bar ──────────────────────────────────────────────────────

const BAR_H = 20          // px — colored bar area
const TICK_LABEL_H = 12   // px — time-label area below bar
const TIMELINE_H = BAR_H + TICK_LABEL_H

const WINDOW_MINUTES = 3  // total span shown in the timeline bar

function RallyTimelineBar({
  events, currentFrame, fps, onMarkerClick,
}: {
  events: Array<{ startFrame: number; stopFrame?: number; score?: string }>
  currentFrame: number
  fps: number
  onMarkerClick: (frame: number) => void
}) {
  const halfWindow = (WINDOW_MINUTES / 2) * 60 * fps
  const windowFrames = WINDOW_MINUTES * 60 * fps
  const winStart = currentFrame - halfWindow
  const winEnd   = currentFrame + halfWindow

  // Convert a frame number to a percentage across the bar [0..100].
  const toPct = (f: number) => (f - winStart) / windowFrames * 100

  // Time-axis ticks every 15 s.
  const tickFrames = Math.round(15 * fps)
  const ticks: number[] = []
  for (let f = Math.ceil(winStart / tickFrames) * tickFrames; f <= winEnd; f += tickFrames) {
    ticks.push(f)
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: TIMELINE_H, marginBottom: 4, overflow: 'hidden' }}>
      {/* Gray background bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: BAR_H, background: '#bbb' }} />

      {events.map((ev, i) => {
        const servePct = toPct(ev.startFrame)
        const endPct   = ev.stopFrame != null ? toPct(ev.stopFrame) : null

        // Green fill clipped to [0, 100].
        const fillLeft  = endPct != null ? Math.max(0, Math.min(100, servePct)) : null
        const fillRight = endPct != null ? Math.max(0, Math.min(100, endPct))   : null
        const showFill       = fillLeft != null && fillRight != null && fillRight > fillLeft && fillRight > 0 && fillLeft < 100
        const showServeMarker = servePct >= 0 && servePct <= 100
        const showEndMarker   = endPct != null && endPct >= 0 && endPct <= 100

        if (!showFill && !showServeMarker && !showEndMarker) return null
        return (
          <Fragment key={i}>
            {/* Green rally fill with score overlay */}
            {showFill && (
              <div style={{
                position: 'absolute', top: 0, height: BAR_H,
                left: `${fillLeft}%`, width: `${fillRight! - fillLeft!}%`,
                background: '#1c7a1c', pointerEvents: 'none',
                overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {ev.score && (
                  <span style={{ fontSize: 7, color: '#fff', fontFamily: 'monospace', whiteSpace: 'nowrap', userSelect: 'none' }}>
                    {ev.score}
                  </span>
                )}
              </div>
            )}
            {/* Serve marker >| — triangle tip at startFrame, extends 6 px left */}
            {showServeMarker && (
              <div
                style={{ position: 'absolute', top: 0, left: `calc(${servePct}% - 6px)`, height: BAR_H, zIndex: 2, cursor: 'pointer', display: 'flex' }}
                onClick={(e) => { e.stopPropagation(); onMarkerClick(ev.startFrame) }}
                title={`Seek to serve at frame ${ev.startFrame}`}
              >
                <div style={{ width: 0, height: 0, borderTop: `${BAR_H / 2}px solid transparent`, borderBottom: `${BAR_H / 2}px solid transparent`, borderLeft: `6px solid #0a3a0a` }} />
                <div style={{ width: 2, height: BAR_H, background: '#0a3a0a' }} />
              </div>
            )}
            {/* End marker |< — bar at stopFrame, triangle tip extends 6 px right */}
            {showEndMarker && ev.stopFrame != null && (
              <div
                style={{ position: 'absolute', top: 0, left: `${endPct}%`, height: BAR_H, zIndex: 2, cursor: 'pointer', display: 'flex' }}
                onClick={(e) => { e.stopPropagation(); onMarkerClick(ev.stopFrame!) }}
                title={`Seek to rally end at frame ${ev.stopFrame}`}
              >
                <div style={{ width: 2, height: BAR_H, background: '#3a0a0a' }} />
                <div style={{ width: 0, height: 0, borderTop: `${BAR_H / 2}px solid transparent`, borderBottom: `${BAR_H / 2}px solid transparent`, borderRight: `6px solid #3a0a0a` }} />
              </div>
            )}
          </Fragment>
        )
      })}

      {/* Time-axis tick marks and labels every 15 s */}
      {ticks.map(f => {
        const pct = toPct(f)
        if (pct < 0 || pct > 100 || f < 0) return null
        const totalSec = Math.round(f / fps)  // seconds from video start
        const m = Math.floor(totalSec / 60)
        const s = totalSec % 60
        const label = `${m}:${s.toString().padStart(2, '0')}`
        return (
          <Fragment key={f}>
            <div style={{ position: 'absolute', top: BAR_H - 4, left: `${pct}%`, width: 1, height: 4, background: '#555', pointerEvents: 'none' }} />
            <div style={{ position: 'absolute', top: BAR_H, left: `${pct}%`, transform: 'translateX(-50%)', fontSize: 9, color: '#333', whiteSpace: 'nowrap', lineHeight: `${TICK_LABEL_H}px`, userSelect: 'none' }}>
              {label}
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}

// ─── Component ───────────────────────────────────────────────────────────────

export const VideoPlayer = forwardRef<VideoPlayerHandle, Props>(function VideoPlayer({
  videoUrl, fps, bgWidth, bgHeight, detections, ballDetections, courtGeometry, totalFrames,
  annotations, onVideoClick, onFrameChange, ballCount, storageKey, previewCanvasRef, bgPlateUrl,
  staticOverlay, segmentPaths, rallyTimeline,
}, ref) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const bgSubCanvasRef = useRef<HTMLCanvasElement>(null)
  const bgPlateRef = useRef<HTMLImageElement | null>(null)
  const mouseRef = useRef<{ x: number; y: number } | null>(null)
  const dragStartRef = useRef<{ bgX: number; bgY: number; fi: number } | null>(null)
  const pendingAnnRef = useRef<{ bgX: number; bgY: number; radius: number } | null>(null)

  useImperativeHandle(ref, () => ({
    seekToFrame: (fi) => {
      if (videoRef.current) videoRef.current.currentTime = fi / fpsRef.current
    },
  }))

  const [playbackState, setPlaybackState] = useState<PlaybackState>('stopped')
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [seekBarValue, setSeekBarValue] = useState(0)
  const isDraggingSeekBar = useRef(false)
  const [showCourt, setShowCourt] = useState(false)
  const [showTent, setShowTent] = useState(false)
  const [showBgSub, setShowBgSub] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [detCount, setDetCount] = useState(0)
  const [mouseOverVideo, setMouseOverVideo] = useState(false)

  // Refs so event handlers and intervals see current values without stale closures.
  const playbackStateRef = useRef<PlaybackState>('stopped')
  const fpsRef = useRef(fps)
  const isSeekingRef = useRef(false)
  const reverseRafRef = useRef<number | null>(null)   // rAF id for fast-reverse seek loop
  const forwardRafRef = useRef<number | null>(null)   // rAF id for canvas-sync during forward play (fallback)
  const vfcHandleRef  = useRef<number | null>(null)   // requestVideoFrameCallback handle
  const stepTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stepIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastFrameIndexRef = useRef(0)   // last frameIndex computed by drawOverlay (rVFC or currentTime)

  useEffect(() => { playbackStateRef.current = playbackState }, [playbackState])
  useEffect(() => { fpsRef.current = fps }, [fps])

  // Keep seek bar in sync with video position when not dragging.
  useEffect(() => {
    if (!isDraggingSeekBar.current) setSeekBarValue(currentTime)
  }, [currentTime])

  // Load median bg plate image whenever the URL changes.
  useEffect(() => {
    if (!bgPlateUrl) return
    const img = new Image()
    img.onload = () => { bgPlateRef.current = img }
    img.src = bgPlateUrl
  }, [bgPlateUrl])

  // rAF loop that composites frame − bg into bgSubCanvasRef when bg-sub mode is on.
  useEffect(() => {
    if (!showBgSub) return
    let rafId: number
    function tick() {
      const video = videoRef.current
      const canvas = bgSubCanvasRef.current
      const plate = bgPlateRef.current
      if (video && canvas && plate) {
        const w = video.clientWidth
        const h = video.clientHeight
        if (w > 0 && h > 0) {
          if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w
            canvas.height = h
          }
          const ctx = canvas.getContext('2d')
          if (ctx) {
            ctx.drawImage(video, 0, 0, w, h)
            ctx.globalCompositeOperation = 'difference'
            ctx.drawImage(plate, 0, 0, w, h)
            ctx.globalCompositeOperation = 'source-over'
          }
        }
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [showBgSub])

  // ── Canvas drawing ──────────────────────────────────────────────────────────

  // mediaTime: exact PTS from requestVideoFrameCallback; omit to read video.currentTime.
  const drawOverlay = useCallback((mediaTime?: number, skipFrameUpdate = false) => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dw = video.clientWidth
    const dh = video.clientHeight
    if (canvas.width !== dw || canvas.height !== dh) {
      canvas.width = dw
      canvas.height = dh
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    // Three cases for frame index:
    //   rVFC (mediaTime provided):       Math.round(mediaTime * fps) — exact PTS, gives N+1
    //   onSeeked / other (no mediaTime): Math.round(video.currentTime * fps) — seek position N/fps gives N
    //   pointer-move redraw (skipFrameUpdate=true): keep existing lastFrameIndexRef unchanged
    let frameIndex: number
    if (skipFrameUpdate) {
      frameIndex = lastFrameIndexRef.current
    } else if (mediaTime !== undefined) {
      frameIndex = Math.round(mediaTime * fps)
      lastFrameIndexRef.current = frameIndex
    } else {
      frameIndex = Math.round(video.currentTime * fps)
      lastFrameIndexRef.current = frameIndex
    }
    const dets = detections ? (detections[frameIndex] ?? []) : []
    if (!skipFrameUpdate) {
      setCurrentTime(mediaTime ?? video.currentTime)
      setDetCount(dets.length)
      onFrameChange?.(frameIndex)
    }

    const sx = canvas.width / bgWidth
    const sy = canvas.height / bgHeight

    // Draw detection ellipses.
    ctx.strokeStyle = 'rgba(255, 120, 0, 0.85)'
    ctx.lineWidth = 2
    for (const det of dets) {
      ctx.beginPath()
      ctx.ellipse(
        det.cx * sx, det.cy * sy,
        Math.max(det.a * sx, 1), Math.max(det.b * sy, 1),
        (det.angle * Math.PI) / 180, 0, 2 * Math.PI,
      )
      ctx.stroke()
    }

    // Draw pass4 ball detections: magenta circle for current frame only.
    // Subtract 1 to compensate for video PTS starting at 1/fps rather than 0, which causes
    // Math.round(mediaTime * fps) to be 1 higher than OpenCV's 0-based frame counter.
    if (ballDetections) {
      const fi = frameIndex - 1
      const dets4 = ballDetections[fi]
      if (dets4) {
        ctx.strokeStyle = 'rgba(255, 0, 255, 0.9)'
        ctx.lineWidth = 2
        for (const det of dets4) {
          ctx.beginPath()
          ctx.arc(det.cx * sx, det.cy * sy, 14, 0, 2 * Math.PI)
          ctx.stroke()
        }
      }
    }

    // Draw complete segment polyline for segments that include the current frame
    // or have an endpoint within 8 frames of the current frame.
    if (segmentPaths) {
      const currentFi = frameIndex - 1  // convert browser→OpenCV numbering
      ctx.lineWidth = 2
      ctx.strokeStyle = 'rgba(100, 210, 255, 0.85)'
      for (const seg of segmentPaths) {
        if (seg.detections.length < 2) continue
        const firstFrame = seg.detections[0].frame
        const lastFrame = seg.detections[seg.detections.length - 1].frame
        const inSegment = currentFi >= firstFrame && currentFi <= lastFrame
        const nearStart = Math.abs(firstFrame - currentFi) <= 8
        const nearEnd = Math.abs(lastFrame - currentFi) <= 8
        if (!inSegment && !nearStart && !nearEnd) continue
        ctx.beginPath()
        ctx.moveTo(seg.detections[0].cx * sx, seg.detections[0].cy * sy)
        for (let i = 1; i < seg.detections.length; i++) {
          ctx.lineTo(seg.detections[i].cx * sx, seg.detections[i].cy * sy)
        }
        ctx.stroke()
      }
    }

    // Draw ball annotation circles (current frame full opacity, ±5 frames faded).
    // Red circle at annotated radius if radius > 0, otherwise fallback cyan circle.
    if (annotations) {
      ctx.lineWidth = 1.5
      for (const [fi, ann] of Object.entries(annotations)) {
        const dist = Math.abs(parseInt(fi, 10) - frameIndex)
        if (dist > 5) continue
        const opacity = dist === 0 ? 1.0 : 0.3
        const cx = ann.x * sx
        const cy = ann.y * sy
        if (ann.radius && ann.radius > 0) {
          ctx.strokeStyle = `rgba(255, 60, 60, ${opacity})`
          ctx.beginPath()
          ctx.arc(cx, cy, ann.radius * sx, 0, 2 * Math.PI)
          ctx.stroke()
        } else {
          ctx.strokeStyle = `rgba(0, 220, 255, ${opacity})`
          ctx.beginPath()
          ctx.arc(cx, cy, 10, 0, 2 * Math.PI)
          ctx.stroke()
        }
      }
    }


    if (showCourt && courtGeometry) {
      const H = buildH(courtGeometry)
      ctx.lineWidth = 1.5
      for (const [u0, v0, u1, v1] of COURT_LINES) {
        const isNet = u0 === 0 && v0 === 0.5
        ctx.strokeStyle = isNet ? 'rgba(255, 180, 50, 0.85)' : 'rgba(80, 200, 255, 0.75)'
        const [x0, y0] = applyH(H, u0, v0)
        const [x1, y1] = applyH(H, u1, v1)
        ctx.beginPath()
        ctx.moveTo(x0 * sx, y0 * sy)
        ctx.lineTo(x1 * sx, y1 * sy)
        ctx.stroke()
      }

    }

    if (showTent && courtGeometry) {
      const vol = computeVolumeOverlay(courtGeometry, bgWidth, bgHeight)

      // Volume edges — magenta, full opacity for debugging
      ctx.strokeStyle = 'rgba(255, 0, 255, 1.0)'
      ctx.lineWidth = 1.5
      for (const [x0, y0, x1, y1] of vol.edges) {
        ctx.beginPath()
        ctx.moveTo(x0 * sx, y0 * sy)
        ctx.lineTo(x1 * sx, y1 * sy)
        ctx.stroke()
      }

      // Silhouette polygon — red outline
      if (vol.silhouette.length >= 2) {
        ctx.strokeStyle = 'rgba(255, 50, 50, 0.85)'
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.moveTo(vol.silhouette[0][0] * sx, vol.silhouette[0][1] * sy)
        for (let i = 1; i < vol.silhouette.length; i++) {
          ctx.lineTo(vol.silhouette[i][0] * sx, vol.silhouette[i][1] * sy)
        }
        ctx.closePath()
        ctx.stroke()
      }
    }
    // Draw static overlay (e.g. full-video detections map): white pixels → yellow, black → transparent.
    if (staticOverlay) {
      ctx.drawImage(staticOverlay, 0, 0, canvas.width, canvas.height)
    }
  }, [fps, bgWidth, bgHeight, detections, ballDetections, showCourt, showTent, courtGeometry, annotations, onFrameChange, staticOverlay, segmentPaths])

  // Redraw whenever annotations or other overlay state changes (e.g. right after a click).
  useEffect(() => { drawOverlay() }, [drawOverlay])

  // Save position to sessionStorage on every seek so StrictMode's effect cleanup
  // (which runs with currentTime=0) never overwrites a valid stored position.
  useEffect(() => {
    const video = videoRef.current
    if (!video || !storageKey) return
    const save = () => sessionStorage.setItem(storageKey, String(video.currentTime))
    video.addEventListener('seeked', save)
    return () => video.removeEventListener('seeked', save)
  }, [storageKey])

  // ── seeked: redraw canvas; also drives the fast-reverse seek loop ───────────

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    function stepBack() {
      if (!video || isSeekingRef.current) return
      isSeekingRef.current = true
      const targetFrame = Math.round(video.currentTime * fpsRef.current) - 2
      video.currentTime = Math.max(0, targetFrame / fpsRef.current)
    }

    function onSeeked() {
      drawOverlay()
      if (playbackStateRef.current !== 'fast-reverse') return
      isSeekingRef.current = false
      if (video!.currentTime <= 0) {
        setPlaybackState('stopped')
        return
      }
      reverseRafRef.current = requestAnimationFrame(stepBack)
    }

    function onDurationChange() { setDuration(video!.duration || 0) }
    function onEnded() { setPlaybackState('stopped'); drawOverlay() }

    video.addEventListener('seeked', onSeeked)
    video.addEventListener('durationchange', onDurationChange)
    video.addEventListener('ended', onEnded)
    return () => {
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('durationchange', onDurationChange)
      video.removeEventListener('ended', onEnded)
    }
  }, [drawOverlay])

  // ── Canvas-sync loop during forward playback ────────────────────────────────
  // Prefer requestVideoFrameCallback (fires once per displayed frame with exact
  // PTS) over rAF (fires at screen refresh rate, currentTime between frames).

  useEffect(() => {
    const isForward = playbackState === 'playing' || playbackState === 'fast-forward'
    const video = videoRef.current

    const cancelAll = () => {
      if (vfcHandleRef.current !== null && video) {
        ;(video as any).cancelVideoFrameCallback(vfcHandleRef.current)
        vfcHandleRef.current = null
      }
      if (forwardRafRef.current !== null) {
        cancelAnimationFrame(forwardRafRef.current)
        forwardRafRef.current = null
      }
    }

    if (!isForward || !video) { cancelAll(); return }

    if ('requestVideoFrameCallback' in video) {
      // rVFC path: metadata.mediaTime is the exact PTS of the frame being painted.
      const vfcTick = (_now: number, metadata: { mediaTime: number }) => {
        drawOverlay(metadata.mediaTime)
        if (playbackStateRef.current === 'playing' || playbackStateRef.current === 'fast-forward') {
          vfcHandleRef.current = (videoRef.current as any).requestVideoFrameCallback(vfcTick)
        } else {
          vfcHandleRef.current = null
        }
      }
      vfcHandleRef.current = (video as any).requestVideoFrameCallback(vfcTick)
    } else {
      // rAF fallback for browsers without rVFC.
      const tick = () => {
        drawOverlay()
        if (playbackStateRef.current === 'playing' || playbackStateRef.current === 'fast-forward') {
          forwardRafRef.current = requestAnimationFrame(tick)
        }
      }
      forwardRafRef.current = requestAnimationFrame(tick)
    }

    return cancelAll
  }, [playbackState, drawOverlay])

  // ── Drive the video element when playback state changes ────────────────────

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const cancelReverse = () => {
      if (reverseRafRef.current !== null) {
        cancelAnimationFrame(reverseRafRef.current)
        reverseRafRef.current = null
      }
    }

    if (playbackState === 'playing') {
      cancelReverse()
      video.playbackRate = 1
      video.play().catch(() => setPlaybackState('stopped'))
    } else if (playbackState === 'fast-forward') {
      cancelReverse()
      video.playbackRate = 2
      video.play().catch(() => setPlaybackState('stopped'))
    } else if (playbackState === 'fast-reverse') {
      video.pause()
      isSeekingRef.current = false
      reverseRafRef.current = requestAnimationFrame(() => {
        if (!video || isSeekingRef.current) return
        isSeekingRef.current = true
        const targetFrame = Math.round(video.currentTime * fpsRef.current) - 2
        video.currentTime = Math.max(0, targetFrame / fpsRef.current)
      })
    } else {
      // stopped
      cancelReverse()
      video.pause()
    }

    return cancelReverse
  }, [playbackState])

  // ── Frame-step helpers used by both buttons and keyboard ────────────────────

  function stopStepping() {
    if (stepTimeoutRef.current !== null) { clearTimeout(stepTimeoutRef.current); stepTimeoutRef.current = null }
    if (stepIntervalRef.current !== null) { clearInterval(stepIntervalRef.current); stepIntervalRef.current = null }
  }

  function startStepping(delta: number) {
    const video = videoRef.current
    if (!video) return
    // Cancel any timers from a previous call (key-repeat fires faster than the
    // 300 ms timeout, so each repeat resets the countdown — only a button hold
    // ever lets the interval actually start).
    stopStepping()
    // Immediately stop any forward/reverse playback.
    if (playbackStateRef.current !== 'stopped') {
      video.pause()
      if (reverseRafRef.current !== null) { cancelAnimationFrame(reverseRafRef.current); reverseRafRef.current = null }
      setPlaybackState('stopped')
    }
    const step = () => {
      const v = videoRef.current
      if (!v) return
      // Compute from frame index to avoid accumulated floating-point error and
      // to guarantee we always cross a frame boundary by exactly one frame.
      const targetFrame = Math.round(v.currentTime * fpsRef.current) + delta
      v.currentTime = Math.max(0, Math.min(v.duration, targetFrame / fpsRef.current))
    }
    step() // immediate
    // After 300 ms hold, repeat at ~30 steps/s.
    stepTimeoutRef.current = setTimeout(() => {
      stepIntervalRef.current = setInterval(step, 1000 / 30)
    }, 300)
  }

  // Clean up step timers on unmount.
  useEffect(() => () => stopStepping(), [])

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Don't capture keys when typing in form fields.
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      if (e.code === 'Space') {
        e.preventDefault()
        if (!e.repeat) {
          setPlaybackState(p =>
            p === 'playing' ? 'stopped' : p === 'stopped' ? 'playing' : 'stopped'
          )
        }
        return
      }

      if (e.code === 'ArrowRight') {
        e.preventDefault()
        if (e.shiftKey) {
          if (!e.repeat && playbackStateRef.current !== 'fast-forward') setPlaybackState('fast-forward')
        } else {
          startStepping(1)
        }
        return
      }

      if (e.code === 'ArrowLeft') {
        e.preventDefault()
        if (e.shiftKey) {
          if (!e.repeat && playbackStateRef.current !== 'fast-reverse') setPlaybackState('fast-reverse')
        } else {
          startStepping(-1)
        }
        return
      }
    }

    function handleKeyUp(e: KeyboardEvent) {
      const s = playbackStateRef.current
      if (s === 'fast-forward' || s === 'fast-reverse') {
        if (
          e.code === 'ArrowRight' || e.code === 'ArrowLeft' ||
          e.code === 'ShiftLeft' || e.code === 'ShiftRight'
        ) {
          setPlaybackState('stopped')
        }
      }
      // Stop mouse-hold stepping on key release too.
      if (e.code === 'ArrowRight' || e.code === 'ArrowLeft') stopStepping()
    }

    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('keyup', handleKeyUp)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('keyup', handleKeyUp)
    }
  }, []) // uses refs — no deps needed

  // ── Button actions ──────────────────────────────────────────────────────────

  function handleRewind() {
    const video = videoRef.current
    if (!video) return
    stopStepping()
    setPlaybackState('stopped')
    video.currentTime = 0
  }

  function handlePlayPause() {
    setPlaybackState(p => p === 'playing' ? 'stopped' : p === 'stopped' ? 'playing' : 'stopped')
  }

  const frameIndex = Math.round(currentTime * fps)

  // ── Video pointer events → drag-to-set-radius annotation ──────────────────
  // Pointer down: record centre. Pointer move: update radius. Pointer up: confirm.

  function handleContainerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const video = videoRef.current
    if (!video) return
    const rect = e.currentTarget.getBoundingClientRect()
    const bgX = (e.clientX - rect.left) / video.clientWidth * bgWidth
    const bgY = (e.clientY - rect.top) / video.clientHeight * bgHeight
    dragStartRef.current = { bgX, bgY, fi: lastFrameIndexRef.current }
    pendingAnnRef.current = { bgX, bgY, radius: 0 }
    e.currentTarget.setPointerCapture(e.pointerId)
    drawOverlay(undefined, true)
  }

  function handleContainerPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    if (!dragStartRef.current) return
    const video = videoRef.current
    if (!video) return
    const bgX = (e.clientX - rect.left) / video.clientWidth * bgWidth
    const bgY = (e.clientY - rect.top) / video.clientHeight * bgHeight
    const dx = bgX - dragStartRef.current.bgX
    const dy = bgY - dragStartRef.current.bgY
    pendingAnnRef.current = {
      bgX: dragStartRef.current.bgX,
      bgY: dragStartRef.current.bgY,
      radius: Math.round(Math.hypot(dx, dy) * 10) / 10,
    }
    drawOverlay(undefined, true)
  }

  function handleContainerPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragStartRef.current || !onVideoClick) return
    const pending = pendingAnnRef.current!
    const { fi } = dragStartRef.current
    dragStartRef.current = null
    pendingAnnRef.current = null

    const video = videoRef.current!
    let patchDataUrl: string | null = null
    const size = PATCH_RADIUS * 2
    const offscreen = document.createElement('canvas')
    offscreen.width = size
    offscreen.height = size
    const pctx = offscreen.getContext('2d')
    if (pctx) {
      try {
        const scaleX = video.videoWidth / bgWidth
        const scaleY = video.videoHeight / bgHeight
        pctx.drawImage(
          video,
          (pending.bgX - PATCH_RADIUS) * scaleX, (pending.bgY - PATCH_RADIUS) * scaleY,
          size * scaleX, size * scaleY,
          0, 0, size, size,
        )
        patchDataUrl = offscreen.toDataURL('image/png')
      } catch {
        // canvas tainted (cross-origin) — proceed without patch
      }
    }

    onVideoClick(fi, pending.bgX, pending.bgY, patchDataUrl, pending.radius)
  }

  // ── Live patch preview (drawn into an external canvas supplied by the parent) ─
  // During drag: patch is frozen at the drag-start centre; red radius circle is drawn.
  // Otherwise: patch follows mouse cursor with a crosshair.

  useEffect(() => {
    if (!mouseOverVideo || !previewCanvasRef?.current) return
    let rafId: number
    function tick() {
      const video = videoRef.current
      const canvas = previewCanvasRef!.current
      const drag = dragStartRef.current
      const bgX = drag ? drag.bgX : (mouseRef.current ? mouseRef.current.x / video!.clientWidth * bgWidth : null)
      const bgY = drag ? drag.bgY : (mouseRef.current ? mouseRef.current.y / video!.clientHeight * bgHeight : null)
      if (video && canvas && bgX !== null && bgY !== null) {
        const ctx = canvas.getContext('2d')
        if (ctx) {
          const cw = canvas.width
          const ch = canvas.height
          const scaleX = video.videoWidth / bgWidth
          const scaleY = video.videoHeight / bgHeight
          try {
            ctx.drawImage(
              video,
              (bgX - PATCH_RADIUS) * scaleX, (bgY - PATCH_RADIUS) * scaleY,
              PATCH_RADIUS * 2 * scaleX, PATCH_RADIUS * 2 * scaleY,
              0, 0, cw, ch,
            )
          } catch {
            ctx.clearRect(0, 0, cw, ch)
          }
          if (drag && pendingAnnRef.current) {
            // Red radius circle — radius is in bg-plate pixels = canvas pixels (1:1 mapping).
            const r = pendingAnnRef.current.radius
            ctx.strokeStyle = 'rgba(255, 60, 60, 0.85)'
            ctx.lineWidth = 1.5
            ctx.beginPath()
            ctx.arc(cw / 2, ch / 2, Math.max(r, 1), 0, 2 * Math.PI)
            ctx.stroke()
          } else {
            // Crosshair while hovering
            ctx.strokeStyle = 'rgba(0, 220, 255, 0.6)'
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(cw / 2, 0); ctx.lineTo(cw / 2, ch)
            ctx.moveTo(0, ch / 2); ctx.lineTo(cw, ch / 2)
            ctx.stroke()
          }
        }
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [mouseOverVideo, previewCanvasRef, bgWidth, bgHeight])

  // ── Render ──────────────────────────────────────────────────────────────────

  const btnStyle: React.CSSProperties = { padding: '4px 10px', fontSize: 15, cursor: 'pointer', userSelect: 'none' }

  return (
    <div>
      {/* Rally timeline — scrolls with current frame, spans 4 minutes */}
      {rallyTimeline && duration > 0 && (
        <RallyTimelineBar
          events={rallyTimeline.events}
          currentFrame={Math.round(currentTime * fps)}
          fps={fps}
          onMarkerClick={rallyTimeline.onMarkerClick}
        />
      )}

      {/* Video + canvas overlay */}
      <div
        style={{
          position: 'relative', display: 'inline-block', maxWidth: '100%',
          cursor: onVideoClick ? CIRCLE_CURSOR : 'default',
        }}
        onPointerDown={onVideoClick ? handleContainerPointerDown : undefined}
        onPointerMove={onVideoClick ? handleContainerPointerMove : undefined}
        onPointerUp={onVideoClick ? handleContainerPointerUp : undefined}
        onMouseEnter={onVideoClick ? () => setMouseOverVideo(true) : undefined}
        onMouseLeave={onVideoClick ? () => {
          // Don't stop the preview loop while a drag is in progress (pointer capture keeps events firing).
          if (!dragStartRef.current) { setMouseOverVideo(false); mouseRef.current = null }
        } : undefined}
      >
        <video
          ref={videoRef}
          src={videoUrl}
          style={{ display: 'block', maxWidth: '100%', maxHeight: 540, visibility: showBgSub ? 'hidden' : 'visible' }}
          onLoadedMetadata={() => {
            const v = videoRef.current
            if (!v) return
            setDuration(v.duration || 0)
            if (storageKey) {
              const saved = parseFloat(sessionStorage.getItem(storageKey) ?? '0')
              if (saved > 0 && saved < v.duration) v.currentTime = saved
            }
            drawOverlay()
          }}
        />
        <canvas
          ref={bgSubCanvasRef}
          style={{
            position: 'absolute', top: 0, left: 0, pointerEvents: 'none',
            display: showBgSub ? 'block' : 'none',
          }}
        />
        <canvas
          ref={canvasRef}
          style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
        />
      </div>

      {/* Seek bar */}
      {duration > 0 && (
        <input
          type="range"
          min={0}
          max={duration}
          step="any"
          value={seekBarValue}
          style={{ width: '100%', marginTop: 6, cursor: 'pointer', display: 'block' }}
          onPointerDown={() => { isDraggingSeekBar.current = true }}
          onPointerUp={() => { isDraggingSeekBar.current = false }}
          onChange={(e) => {
            const video = videoRef.current
            if (!video) return
            if (playbackStateRef.current !== 'stopped') {
              video.pause()
              setPlaybackState('stopped')
            }
            const v = parseFloat(e.target.value)
            // Update slider position immediately for real-time visual feedback.
            setSeekBarValue(v)
            // Snap to the nearest frame boundary to avoid off-by-one errors.
            const targetFrame = Math.round(v * fpsRef.current)
            video.currentTime = Math.max(0, Math.min(video.duration, targetFrame / fpsRef.current))
          }}
        />
      )}

      {/* Playback controls */}
      <div style={{ marginTop: 6, display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Rewind */}
        <button onClick={handleRewind} title="Rewind to beginning" style={btnStyle}>⏮</button>

        {/* 2× reverse — active while held */}
        <button
          onPointerDown={() => setPlaybackState('fast-reverse')}
          onPointerUp={() => setPlaybackState(s => s === 'fast-reverse' ? 'stopped' : s)}
          onPointerLeave={() => setPlaybackState(s => s === 'fast-reverse' ? 'stopped' : s)}
          title="2× fast reverse — hold (⇧←)"
          style={btnStyle}
        >◀◀</button>

        {/* Frame reverse — continuous while held */}
        <button
          onPointerDown={() => startStepping(-1)}
          onPointerUp={stopStepping}
          onPointerLeave={stopStepping}
          title="One frame back — hold to continue (←)"
          style={btnStyle}
        >◀|</button>

        {/* Play / Pause */}
        <button
          onClick={handlePlayPause}
          title="Play / Pause (Space)"
          style={{ ...btnStyle, minWidth: 44 }}
        >
          {playbackState === 'playing' ? '⏸' : '▶'}
        </button>

        {/* Frame advance — continuous while held */}
        <button
          onPointerDown={() => startStepping(1)}
          onPointerUp={stopStepping}
          onPointerLeave={stopStepping}
          title="One frame forward — hold to continue (→)"
          style={btnStyle}
        >|▶</button>

        {/* 2× forward — active while held */}
        <button
          onPointerDown={() => setPlaybackState('fast-forward')}
          onPointerUp={() => setPlaybackState(s => s === 'fast-forward' ? 'stopped' : s)}
          onPointerLeave={() => setPlaybackState(s => s === 'fast-forward' ? 'stopped' : s)}
          title="2× fast forward — hold (⇧→)"
          style={btnStyle}
        >▶▶</button>

        <div style={{ width: 1, height: 20, background: '#ccc', margin: '0 4px' }} />

        {/* Help toggle */}
        <button
          onClick={() => setShowHelp(h => !h)}
          title="Keyboard shortcuts"
          style={{ ...btnStyle, fontWeight: 'bold', background: showHelp ? '#ddf' : undefined }}
        >?</button>

        {/* Court and Tent overlays — only shown when court geometry is available */}
        {courtGeometry && (<>
          <label style={{ marginLeft: 4, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 13 }}>
            <input type="checkbox" checked={showCourt} onChange={(e) => setShowCourt(e.target.checked)} />
            Court
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 13 }}>
            <input type="checkbox" checked={showTent} onChange={(e) => setShowTent(e.target.checked)} />
            Tent
          </label>
        </>)}

        {/* Bg-sub toggle — only shown when a bg plate URL is available */}
        {bgPlateUrl && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontSize: 13 }}>
            <input type="checkbox" checked={showBgSub} onChange={(e) => setShowBgSub(e.target.checked)} />
            Bg sub
          </label>
        )}

        {/* Ball annotation count */}
        {ballCount !== undefined && (
          <span style={{ marginLeft: 8, fontSize: 13, color: '#555' }}>
            Balls marked: <strong>{ballCount}</strong>
          </span>
        )}
      </div>

      {/* Status row */}
      <div style={{ marginTop: 6, fontSize: 13, color: '#555', display: 'flex', gap: 16 }}>
        <span>{fmtTime(currentTime)} / {fmtTime(duration)}</span>
        <span>Frame {frameIndex} / {totalFrames || Math.round(duration * fps)}</span>
        {detections && <span>Detections: <strong>{detCount}</strong></span>}
      </div>

      {/* Help panel */}
      {showHelp && (
        <div style={{
          marginTop: 8, padding: '10px 14px',
          background: '#f8f8f8', border: '1px solid #ddd', borderRadius: 4,
          fontSize: 13, lineHeight: 2,
        }}>
          <strong>Keyboard shortcuts</strong>
          <table style={{ borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              {([
                ['Space', 'Play / Pause'],
                ['→', 'Advance one frame (hold to continue)'],
                ['←', 'Reverse one frame (hold to continue)'],
                ['⇧→', '2× fast forward (hold)'],
                ['⇧←', '2× fast reverse (hold)'],
              ] as const).map(([key, desc]) => (
                <tr key={key}>
                  <td style={{ paddingRight: 20, fontFamily: 'monospace', fontWeight: 'bold', color: '#333' }}>{key}</td>
                  <td style={{ color: '#555' }}>{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
})
