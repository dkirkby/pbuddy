/**
 * SVG overlay showing the full pickleball court geometry over the median
 * background image. Outer boundary, kitchen lines, center lines, and net
 * are all derived from 4 draggable corner handles via a perspective transform.
 *
 * Standard court proportions (feet): 44 long × 20 wide, net at 22, kitchen at ±7 from net.
 */
import { useRef, useState } from 'react'
import type { CourtCorner, CourtGeometry } from '../types/api'
import { COURT_KV } from '../lib/dimensions'

interface Props {
  geometry: CourtGeometry
  imageWidth: number
  imageHeight: number
  onChange: (g: CourtGeometry) => void
}

type CornerKey = keyof CourtGeometry

const CORNER_KEYS: CornerKey[] = ['top_left', 'top_right', 'bottom_left', 'bottom_right']

// Normalised v-coordinate of the kitchen line — derived from dimensions.json.
const KV = COURT_KV

// [u0, v0, u1, v1] for each line segment to draw.
const OUTER_LINES = [
  [0, 0, 1, 0], [1, 0, 1, 1], [1, 1, 0, 1], [0, 1, 0, 0],
]
const INNER_LINES = [
  [0, KV, 1, KV],          // top kitchen line
  [0, 1 - KV, 1, 1 - KV],  // bottom kitchen line
  [0.5, 0, 0.5, KV],        // top center line
  [0.5, 1 - KV, 0.5, 1],   // bottom center line
]
const NET_LINE = [0, 0.5, 1, 0.5]

/**
 * Build a 3×3 homography (flat row-major array of 9) mapping court (u,v) → image (x,y).
 * Mapping: (0,0)→TL, (1,0)→TR, (1,1)→BR, (0,1)→BL.
 */
function buildH(g: CourtGeometry): number[] {
  const { top_left: TL, top_right: TR, bottom_left: BL, bottom_right: BR } = g
  const A = TR.x - BR.x, B = BL.x - BR.x, C = TL.x - TR.x - BL.x + BR.x
  const D = TR.y - BR.y, E = BL.y - BR.y, F = TL.y - TR.y - BL.y + BR.y
  const det = A * E - B * D
  const gh = (C * E - B * F) / det
  const hh = (A * F - C * D) / det
  return [
    TR.x * (gh + 1) - TL.x,  BL.x * (hh + 1) - TL.x,  TL.x,
    TR.y * (gh + 1) - TL.y,  BL.y * (hh + 1) - TL.y,  TL.y,
    gh, hh, 1,
  ]
}

function applyH(H: number[], u: number, v: number): CourtCorner {
  const w = H[6] * u + H[7] * v + 1
  return { x: (H[0] * u + H[1] * v + H[2]) / w, y: (H[3] * u + H[4] * v + H[5]) / w }
}

function CourtLine({ H, seg, stroke, strokeWidth = 1.5, opacity = 0.85 }: {
  H: number[], seg: number[], stroke: string, strokeWidth?: number, opacity?: number
}) {
  const p0 = applyH(H, seg[0], seg[1])
  const p1 = applyH(H, seg[2], seg[3])
  return (
    <line
      x1={p0.x} y1={p0.y} x2={p1.x} y2={p1.y}
      stroke={stroke} strokeWidth={strokeWidth} strokeOpacity={opacity}
    />
  )
}

export function CourtOverlay({ geometry, imageWidth, imageHeight, onChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragging, setDragging] = useState<CornerKey | null>(null)
  const H = buildH(geometry)

  function toImageCoords(clientX: number, clientY: number): CourtCorner {
    const rect = svgRef.current!.getBoundingClientRect()
    return {
      x: (clientX - rect.left) * (imageWidth / rect.width),
      y: (clientY - rect.top) * (imageHeight / rect.height),
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragging) return
    onChange({ ...geometry, [dragging]: toImageCoords(e.clientX, e.clientY) })
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${imageWidth} ${imageHeight}`}
      style={{
        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
        overflow: 'visible', cursor: dragging ? 'grabbing' : 'default',
      }}
      onMouseMove={onMouseMove}
      onMouseUp={() => setDragging(null)}
      onMouseLeave={() => setDragging(null)}
    >
      {/* Outer boundary */}
      {OUTER_LINES.map((seg, i) => (
        <CourtLine key={`out-${i}`} H={H} seg={seg} stroke="#4af" strokeWidth={2} />
      ))}

      {/* Kitchen lines and center lines */}
      {INNER_LINES.map((seg, i) => (
        <CourtLine key={`inn-${i}`} H={H} seg={seg} stroke="#4af" strokeWidth={1.5} opacity={0.7} />
      ))}

      {/* Net */}
      <CourtLine H={H} seg={NET_LINE} stroke="#fa4" strokeWidth={2} />

      {/* Draggable corner handles */}
      {CORNER_KEYS.map((key) => {
        const c = geometry[key] as CourtCorner
        return (
          <circle
            key={key}
            cx={c.x} cy={c.y} r={10}
            fill="#4af" fillOpacity={dragging === key ? 0.9 : 0.6}
            stroke="#fff" strokeWidth={2}
            style={{ cursor: 'grab' }}
            onMouseDown={(e) => { e.stopPropagation(); setDragging(key) }}
          />
        )
      })}
    </svg>
  )
}
