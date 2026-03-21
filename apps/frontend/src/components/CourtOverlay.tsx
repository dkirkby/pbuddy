/**
 * SVG overlay showing draggable court corners and net line over the
 * median background image.
 */
import { useRef, useState } from 'react'
import type { CourtCorner, CourtGeometry } from '../types/api'

interface Props {
  geometry: CourtGeometry
  imageWidth: number
  imageHeight: number
  onChange: (g: CourtGeometry) => void
}

type CornerKey = keyof CourtGeometry

const CORNER_KEYS: CornerKey[] = [
  'top_left', 'top_right', 'bottom_left', 'bottom_right', 'net_left', 'net_right',
]

const CORNER_COLORS: Record<CornerKey, string> = {
  top_left: '#4af', top_right: '#4af',
  bottom_left: '#4af', bottom_right: '#4af',
  net_left: '#fa4', net_right: '#fa4',
}

export function CourtOverlay({ geometry, imageWidth, imageHeight, onChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragging, setDragging] = useState<CornerKey | null>(null)

  function toSvgCoords(clientX: number, clientY: number): CourtCorner {
    const svg = svgRef.current!
    const rect = svg.getBoundingClientRect()
    const scaleX = imageWidth / rect.width
    const scaleY = imageHeight / rect.height
    return {
      x: Math.max(0, Math.min(imageWidth, (clientX - rect.left) * scaleX)),
      y: Math.max(0, Math.min(imageHeight, (clientY - rect.top) * scaleY)),
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragging) return
    const pt = toSvgCoords(e.clientX, e.clientY)
    onChange({ ...geometry, [dragging]: pt })
  }

  function onMouseUp() {
    setDragging(null)
  }

  const g = geometry
  // Court polygon: top_left → top_right → bottom_right → bottom_left
  const courtPoints = [g.top_left, g.top_right, g.bottom_right, g.bottom_left]
  const polyStr = courtPoints.map((c) => `${c.x},${c.y}`).join(' ')

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${imageWidth} ${imageHeight}`}
      style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', cursor: dragging ? 'grabbing' : 'default' }}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
    >
      {/* Court rectangle */}
      <polygon points={polyStr} fill="none" stroke="#4af" strokeWidth={2} strokeOpacity={0.8} />

      {/* Net line */}
      <line
        x1={g.net_left.x} y1={g.net_left.y}
        x2={g.net_right.x} y2={g.net_right.y}
        stroke="#fa4" strokeWidth={2} strokeOpacity={0.9}
      />

      {/* Draggable handles */}
      {CORNER_KEYS.map((key) => {
        const c = g[key] as CourtCorner
        return (
          <circle
            key={key}
            cx={c.x} cy={c.y} r={10}
            fill={CORNER_COLORS[key]}
            fillOpacity={dragging === key ? 0.9 : 0.6}
            stroke="#fff" strokeWidth={2}
            style={{ cursor: 'grab' }}
            onMouseDown={(e) => { e.stopPropagation(); setDragging(key) }}
          />
        )
      })}
    </svg>
  )
}
