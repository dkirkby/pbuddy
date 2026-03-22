/**
 * VideoPlayer — HTML5 video element with a canvas overlay for detection ellipses
 * and an optional court outline. Supports forward/backward playback at 4 speeds.
 *
 * State machine:
 *   stopped → playing-forward | playing-backward
 *   playing-forward → stopped (pause or end of video)
 *   playing-backward → stopped (pause or start of video)
 * Speed and direction are independent; switching direction always passes through stopped.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CourtGeometry, Detection } from '../types/api'

const SPEEDS = [0.25, 0.5, 1.0, 3.0] as const
type Speed = (typeof SPEEDS)[number]
type PlaybackState = 'stopped' | 'playing-forward' | 'playing-backward'

// Court proportions (normalized 0–1): net at 0.5, kitchen lines at ±7/44 from net.
const KV = (22 - 7) / 44
const COURT_LINES = [
  // Outer boundary
  [0, 0, 1, 0], [1, 0, 1, 1], [1, 1, 0, 1], [0, 1, 0, 0],
  // Net
  [0, 0.5, 1, 0.5],
  // Kitchen lines
  [0, KV, 1, KV], [0, 1 - KV, 1, 1 - KV],
  // Center lines (between kitchen and baseline)
  [0.5, 0, 0.5, KV], [0.5, 1 - KV, 0.5, 1],
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

interface Props {
  videoUrl: string
  fps: number
  bgWidth: number
  bgHeight: number
  detections: Record<number, Detection[]>
  courtGeometry?: CourtGeometry
  totalFrames: number
}

export function VideoPlayer({
  videoUrl, fps, bgWidth, bgHeight, detections, courtGeometry, totalFrames,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [playbackState, setPlaybackState] = useState<PlaybackState>('stopped')
  const [speed, setSpeed] = useState<Speed>(1.0)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [showCourt, setShowCourt] = useState(false)
  const [detCount, setDetCount] = useState(0)

  // Refs for reverse playback loop to avoid stale closures.
  const playbackStateRef = useRef(playbackState)
  const speedRef = useRef(speed)
  const isSeekingRef = useRef(false)
  const rafRef = useRef<number | null>(null)

  useEffect(() => { playbackStateRef.current = playbackState }, [playbackState])
  useEffect(() => { speedRef.current = speed }, [speed])

  // Draw detections + court outline on the canvas for the current video frame.
  const drawOverlay = useCallback(() => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Sync canvas size to displayed video size.
    const dw = video.clientWidth
    const dh = video.clientHeight
    if (canvas.width !== dw || canvas.height !== dh) {
      canvas.width = dw
      canvas.height = dh
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const frameIndex = Math.round(video.currentTime * fps)
    const dets = detections[frameIndex] ?? []

    setCurrentTime(video.currentTime)
    setDetCount(dets.length)

    const scaleX = canvas.width / bgWidth
    const scaleY = canvas.height / bgHeight

    // Draw detection ellipses.
    ctx.strokeStyle = 'rgba(255, 120, 0, 0.85)'
    ctx.lineWidth = 2
    for (const det of dets) {
      const angleRad = (det.angle * Math.PI) / 180
      ctx.beginPath()
      ctx.ellipse(
        det.cx * scaleX,
        det.cy * scaleY,
        Math.max(det.a * scaleX, 1),
        Math.max(det.b * scaleY, 1),
        angleRad,
        0,
        2 * Math.PI,
      )
      ctx.stroke()
    }

    // Draw court outline.
    if (showCourt && courtGeometry) {
      const H = buildH(courtGeometry)
      ctx.lineWidth = 1.5
      for (const [u0, v0, u1, v1] of COURT_LINES) {
        const isNet = u0 === 0 && v0 === 0.5
        ctx.strokeStyle = isNet ? 'rgba(255, 180, 50, 0.85)' : 'rgba(80, 200, 255, 0.75)'
        const [x0, y0] = applyH(H, u0, v0)
        const [x1, y1] = applyH(H, u1, v1)
        ctx.beginPath()
        ctx.moveTo(x0 * scaleX, y0 * scaleY)
        ctx.lineTo(x1 * scaleX, y1 * scaleY)
        ctx.stroke()
      }
    }
  }, [fps, bgWidth, bgHeight, detections, showCourt, courtGeometry])

  // Redraw on timeupdate (forward playback) or after seeking.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    function onTimeUpdate() {
      if (playbackStateRef.current === 'playing-forward') drawOverlay()
    }
    function onDurationChange() {
      setDuration(video!.duration || 0)
    }
    function onEnded() {
      setPlaybackState('stopped')
      drawOverlay()
    }

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('durationchange', onDurationChange)
    video.addEventListener('ended', onEnded)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('durationchange', onDurationChange)
      video.removeEventListener('ended', onEnded)
    }
  }, [drawOverlay])

  // Reverse playback: step backward one frame per rAF tick, wait for seeked event.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    function onSeeked() {
      drawOverlay()
      isSeekingRef.current = false
      if (playbackStateRef.current !== 'playing-backward') return
      if (video!.currentTime <= 0) {
        setPlaybackState('stopped')
        return
      }
      rafRef.current = requestAnimationFrame(stepBack)
    }

    function stepBack() {
      if (!video || isSeekingRef.current) return
      isSeekingRef.current = true
      const newTime = Math.max(0, video.currentTime - (1 / fps) * speedRef.current)
      video.currentTime = newTime
    }

    video.addEventListener('seeked', onSeeked)
    return () => video.removeEventListener('seeked', onSeeked)
  }, [fps, drawOverlay])

  // Start/stop playback based on state changes.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    if (playbackState === 'playing-forward') {
      video.playbackRate = speed
      video.play().catch(() => setPlaybackState('stopped'))
    } else if (playbackState === 'playing-backward') {
      video.pause()
      isSeekingRef.current = false
      // Trigger the first step; subsequent steps happen in onSeeked.
      rafRef.current = requestAnimationFrame(() => {
        if (!video || isSeekingRef.current) return
        isSeekingRef.current = true
        video.currentTime = Math.max(0, video.currentTime - (1 / fps) * speed)
      })
    } else {
      video.pause()
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [playbackState, speed, fps])

  // Update speed on forward playback without toggling state.
  useEffect(() => {
    const video = videoRef.current
    if (video && playbackState === 'playing-forward') {
      video.playbackRate = speed
    }
  }, [speed, playbackState])

  function handlePlayPause() {
    if (playbackState === 'stopped') {
      setPlaybackState('playing-forward')
    } else {
      setPlaybackState('stopped')
    }
  }

  function handleBackward3x() {
    setPlaybackState('stopped')
    setTimeout(() => {
      speedRef.current = 3.0
      setSpeed(3.0)
      setPlaybackState('playing-backward')
    }, 0)
  }

  function handleBackward1x() {
    setPlaybackState('stopped')
    setTimeout(() => {
      speedRef.current = 1.0
      setSpeed(1.0)
      setPlaybackState('playing-backward')
    }, 0)
  }

  function handleForward3x() {
    setPlaybackState('stopped')
    setTimeout(() => {
      speedRef.current = 3.0
      setSpeed(3.0)
      setPlaybackState('playing-forward')
    }, 0)
  }

  const frameIndex = Math.round(currentTime * fps)

  return (
    <div>
      {/* Video + overlay */}
      <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
        <video
          ref={videoRef}
          src={videoUrl}
          style={{ display: 'block', maxWidth: '100%', maxHeight: 540 }}
          onLoadedMetadata={() => {
            setDuration(videoRef.current?.duration ?? 0)
            drawOverlay()
          }}
        />
        <canvas
          ref={canvasRef}
          style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
        />
      </div>

      {/* Controls */}
      <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <button onClick={handleBackward3x} title="3× backward">◀◀</button>
        <button onClick={handleBackward1x} title="1× backward">◀</button>
        <button onClick={handlePlayPause} style={{ minWidth: 48 }}>
          {playbackState !== 'stopped' ? '⏸' : '▶'}
        </button>
        <button onClick={handleForward3x} title="3× forward">▶▶</button>

        <span style={{ marginLeft: 8, fontSize: 13, color: '#555' }}>Speed:</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => setSpeed(s)}
            style={{
              padding: '2px 8px', fontSize: 12,
              background: speed === s ? '#09f' : '#eee',
              color: speed === s ? '#fff' : '#333',
              border: '1px solid #ccc', borderRadius: 3,
            }}
          >
            {s}×
          </button>
        ))}
      </div>

      {/* Info row */}
      <div style={{ marginTop: 8, fontSize: 13, color: '#555', display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        <span>Time: {fmtTime(currentTime)} / {fmtTime(duration)}</span>
        <span>Frame: {frameIndex} / {totalFrames}</span>
        <span>Detections this frame: <strong>{detCount}</strong></span>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showCourt}
            onChange={(e) => setShowCourt(e.target.checked)}
          />
          Show court outline
        </label>
      </div>
    </div>
  )
}
