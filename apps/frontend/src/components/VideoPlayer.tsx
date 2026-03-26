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
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
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
  onVideoClick?: (frameIndex: number, bgX: number, bgY: number, patchDataUrl: string | null) => void
  onFrameChange?: (frameIndex: number) => void
  ballCount?: number
  storageKey?: string
  previewCanvasRef?: React.RefObject<HTMLCanvasElement>
  bgPlateUrl?: string
}

// ─── Component ───────────────────────────────────────────────────────────────

export const VideoPlayer = forwardRef<VideoPlayerHandle, Props>(function VideoPlayer({
  videoUrl, fps, bgWidth, bgHeight, detections, ballDetections, courtGeometry, totalFrames,
  annotations, onVideoClick, onFrameChange, ballCount, storageKey, previewCanvasRef, bgPlateUrl,
}, ref) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const bgSubCanvasRef = useRef<HTMLCanvasElement>(null)
  const bgPlateRef = useRef<HTMLImageElement | null>(null)
  const mouseRef = useRef<{ x: number; y: number } | null>(null)

  useImperativeHandle(ref, () => ({
    seekToFrame: (fi) => {
      if (videoRef.current) videoRef.current.currentTime = fi / fpsRef.current
    },
  }))

  const [playbackState, setPlaybackState] = useState<PlaybackState>('stopped')
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
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

  useEffect(() => { playbackStateRef.current = playbackState }, [playbackState])
  useEffect(() => { fpsRef.current = fps }, [fps])

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
  const drawOverlay = useCallback((mediaTime?: number) => {
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

    const t = mediaTime ?? video.currentTime
    const frameIndex = Math.round(t * fps)
    const dets = detections ? (detections[frameIndex] ?? []) : []
    setCurrentTime(t)
    setDetCount(dets.length)
    onFrameChange?.(frameIndex)

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

    // Draw pass4 ball detections: cyan for previous 8 frames, magenta for current frame.
    // Subtract 1 to compensate for video PTS starting at 1/fps rather than 0, which causes
    // Math.round(mediaTime * fps) to be 1 higher than OpenCV's 0-based frame counter.
    if (ballDetections) {
      for (let offset = 8; offset >= 0; offset--) {
        const fi = frameIndex - offset - 1
        const dets4 = ballDetections[fi]
        if (!dets4) continue
        ctx.strokeStyle = offset === 0 ? 'rgba(255, 0, 255, 0.9)' : 'rgba(0, 220, 255, 0.5)'
        ctx.lineWidth = offset === 0 ? 2 : 1.5
        for (const det of dets4) {
          ctx.beginPath()
          ctx.arc(det.cx * sx, det.cy * sy, 14, 0, 2 * Math.PI)
          ctx.stroke()
        }
      }
    }

    // Draw ball annotation circles (current frame full opacity, ±5 frames faded).
    if (annotations) {
      const R = 10
      ctx.lineWidth = 1.5
      for (const [fi, ann] of Object.entries(annotations)) {
        const dist = Math.abs(parseInt(fi, 10) - frameIndex)
        if (dist > 5) continue
        const opacity = dist === 0 ? 1.0 : 0.3
        ctx.strokeStyle = `rgba(0, 220, 255, ${opacity})`
        const cx = ann.x * sx
        const cy = ann.y * sy
        ctx.beginPath()
        ctx.arc(cx, cy, R, 0, 2 * Math.PI)
        ctx.stroke()
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
  }, [fps, bgWidth, bgHeight, detections, ballDetections, showCourt, showTent, courtGeometry, annotations, onFrameChange])

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

  // ── Video click → annotation callback ──────────────────────────────────────

  function handleContainerClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!onVideoClick) return
    const video = videoRef.current
    if (!video) return
    const rect = e.currentTarget.getBoundingClientRect()
    const bgX = (e.clientX - rect.left) / video.clientWidth * bgWidth
    const bgY = (e.clientY - rect.top) / video.clientHeight * bgHeight
    const fi = Math.round(video.currentTime * fpsRef.current)

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
          (bgX - PATCH_RADIUS) * scaleX, (bgY - PATCH_RADIUS) * scaleY,
          size * scaleX, size * scaleY,
          0, 0, size, size,
        )
        patchDataUrl = offscreen.toDataURL('image/png')
      } catch {
        // canvas tainted (cross-origin) — proceed without patch
      }
    }

    onVideoClick(fi, bgX, bgY, patchDataUrl)
  }

  // ── Live patch preview (drawn into an external canvas supplied by the parent) ─

  useEffect(() => {
    if (!mouseOverVideo || !previewCanvasRef?.current) return
    let rafId: number
    function tick() {
      const video = videoRef.current
      const canvas = previewCanvasRef!.current
      const pos = mouseRef.current
      if (video && canvas && pos) {
        const ctx = canvas.getContext('2d')
        if (ctx) {
          const cw = canvas.width
          const ch = canvas.height
          const bgX = pos.x / video.clientWidth * bgWidth
          const bgY = pos.y / video.clientHeight * bgHeight
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
          // Crosshair at centre
          ctx.strokeStyle = 'rgba(0, 220, 255, 0.6)'
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.moveTo(cw / 2, 0); ctx.lineTo(cw / 2, ch)
          ctx.moveTo(0, ch / 2); ctx.lineTo(cw, ch / 2)
          ctx.stroke()
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
      {/* Video + canvas overlay */}
      <div
        style={{
          position: 'relative', display: 'inline-block', maxWidth: '100%',
          cursor: onVideoClick ? CIRCLE_CURSOR : 'default',
        }}
        onClick={handleContainerClick}
        onMouseMove={onVideoClick ? (e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top }
        } : undefined}
        onMouseEnter={onVideoClick ? () => setMouseOverVideo(true) : undefined}
        onMouseLeave={onVideoClick ? () => { setMouseOverVideo(false); mouseRef.current = null } : undefined}
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

      {/* Playback controls */}
      <div style={{ marginTop: 10, display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
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
