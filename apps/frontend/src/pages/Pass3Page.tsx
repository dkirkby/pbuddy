import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PlotMapping {
  image_width: number
  image_height: number
  axes_left: number
  axes_right: number
  axes_top: number
  axes_bottom: number
  [key: string]: number
}

interface Point { x: number; y: number }

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

const VERTEX_RADIUS = 7    // SVG viewBox units
const EDGE_HIT_DIST = 20   // SVG viewBox units — click within this distance of an edge to insert
const POLY_STROKE   = 3

function clampToFrame(pt: Point, m: PlotMapping): Point {
  return {
    x: Math.max(m.axes_left,  Math.min(m.axes_right,  pt.x)),
    y: Math.max(m.axes_top,   Math.min(m.axes_bottom, pt.y)),
  }
}

function distToSegment(p: Point, a: Point, b: Point): { dist: number; proj: Point } {
  const dx = b.x - a.x, dy = b.y - a.y
  const lenSq = dx * dx + dy * dy
  if (lenSq === 0) return { dist: Math.hypot(p.x - a.x, p.y - a.y), proj: a }
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq))
  const proj = { x: a.x + t * dx, y: a.y + t * dy }
  return { dist: Math.hypot(p.x - proj.x, p.y - proj.y), proj }
}

/** Convert PNG-pixel coords → data coords using the plot mapping. */
function pixelToData(pt: Point, m: PlotMapping, xKey: string, yKey: string): [number, number] {
  const xMin = m[`${xKey}_min`], xMax = m[`${xKey}_max`]
  const yMax = m[`${yKey}_max`], yMin = m[`${yKey}_min`]
  const x = xMin + (pt.x - m.axes_left) / (m.axes_right - m.axes_left) * (xMax - xMin)
  const y = yMax - (pt.y - m.axes_top)  / (m.axes_bottom - m.axes_top)  * (yMax - yMin)
  return [Math.round(x), Math.round(y)]
}

/** Convert data coords → PNG-pixel coords (inverse of pixelToData). */
function dataToPixel(coord: [number, number], m: PlotMapping, xKey: string, yKey: string): Point {
  const xMin = m[`${xKey}_min`], xMax = m[`${xKey}_max`]
  const yMax = m[`${yKey}_max`], yMin = m[`${yKey}_min`]
  return {
    x: m.axes_left + (coord[0] - xMin) / (xMax - xMin) * (m.axes_right  - m.axes_left),
    y: m.axes_top  + (yMax - coord[1]) / (yMax - yMin) * (m.axes_bottom - m.axes_top),
  }
}

// ---------------------------------------------------------------------------
// PlotEditor
// ---------------------------------------------------------------------------

interface PlotEditorProps {
  imageUrl: string
  mapping: PlotMapping
  vertices: Point[]
  onChange: (verts: Point[]) => void
}

function PlotEditor({ imageUrl, mapping, vertices, onChange }: PlotEditorProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const divRef = useRef<HTMLDivElement>(null)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const dragIdx = useRef<number | null>(null)

  const toSVGPoint = useCallback((e: React.PointerEvent): Point => {
    const svg = svgRef.current!
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const sp = pt.matrixTransform(svg.getScreenCTM()!.inverse())
    return { x: sp.x, y: sp.y }
  }, [])

  // Background click: insert on edge if close enough, otherwise append vertex.
  const onSVGDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (e.target !== e.currentTarget) return  // vertex circle handled separately
    e.preventDefault()
    const raw = toSVGPoint(e)
    const pt  = clampToFrame(raw, mapping)

    if (vertices.length >= 2) {
      const n = vertices.length
      for (let i = 0; i < n; i++) {
        const { dist, proj } = distToSegment(pt, vertices[i], vertices[(i + 1) % n])
        if (dist < EDGE_HIT_DIST) {
          const clamped = clampToFrame(proj, mapping)
          const next = [...vertices]
          next.splice(i + 1, 0, clamped)
          onChange(next)
          dragIdx.current = i + 1
          setSelectedIdx(i + 1)
          svgRef.current!.setPointerCapture(e.pointerId)
          divRef.current?.focus()
          return
        }
      }
    }

    onChange([...vertices, pt])
    dragIdx.current = vertices.length
    setSelectedIdx(vertices.length)
    svgRef.current!.setPointerCapture(e.pointerId)
    divRef.current?.focus()
  }, [vertices, mapping, onChange, toSVGPoint])

  const onVertexDown = useCallback((e: React.PointerEvent<SVGCircleElement>, idx: number) => {
    e.stopPropagation()
    e.preventDefault()
    dragIdx.current = idx
    setSelectedIdx(idx)
    svgRef.current!.setPointerCapture(e.pointerId)
    divRef.current?.focus()
  }, [])

  const onSVGMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (dragIdx.current === null) return
    const pt = clampToFrame(toSVGPoint(e), mapping)
    onChange(vertices.map((v, i) => i === dragIdx.current ? pt : v))
  }, [vertices, mapping, onChange, toSVGPoint])

  const onSVGUp = useCallback(() => { dragIdx.current = null }, [])

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIdx !== null) {
      if (vertices.length > 1) {
        onChange(vertices.filter((_, i) => i !== selectedIdx))
        setSelectedIdx(null)
      }
    }
  }, [vertices, selectedIdx, onChange])

  const pts = vertices.map(v => `${v.x},${v.y}`).join(' ')

  return (
    <div
      ref={divRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      style={{ position: 'relative', display: 'inline-block', outline: 'none' }}
    >
      <img src={imageUrl} style={{ display: 'block', width: 540 }} draggable={false} />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${mapping.image_width} ${mapping.image_height}`}
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', cursor: 'crosshair' }}
        onPointerDown={onSVGDown}
        onPointerMove={onSVGMove}
        onPointerUp={onSVGUp}
      >
        {vertices.length >= 2 && (
          <polygon
            points={pts}
            fill="rgba(255,220,0,0.12)"
            stroke="rgba(255,220,0,0.9)"
            strokeWidth={POLY_STROKE}
            style={{ pointerEvents: 'none' }}
          />
        )}
        {vertices.map((v, i) => (
          <circle
            key={i}
            cx={v.x} cy={v.y} r={VERTEX_RADIUS}
            fill={i === selectedIdx ? '#444' : 'rgba(0,0,0,0.25)'}
            stroke="#333" strokeWidth={2}
            style={{ cursor: 'grab' }}
            onPointerDown={(e) => onVertexDown(e, i)}
          />
        ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pass3Page
// ---------------------------------------------------------------------------

export default function Pass3Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: hsRaw } = useQuery({
    queryKey: ['pass3-mapping-hs', projectId],
    queryFn: () => api.getPass3PlotMapping(projectId!, 'hue_saturation'),
  })
  const { data: vsRaw } = useQuery({
    queryKey: ['pass3-mapping-vs', projectId],
    queryFn: () => api.getPass3PlotMapping(projectId!, 'value_saturation'),
  })
  const { data: corrResp } = useQuery({
    queryKey: ['pass3-corrections', projectId],
    queryFn: () => api.getPass3Corrections(projectId!),
  })

  const hsMapping = hsRaw as PlotMapping | undefined
  const vsMapping = vsRaw as PlotMapping | undefined

  const [hsVerts, setHsVerts] = useState<Point[]>([])
  const [vsVerts, setVsVerts] = useState<Point[]>([])
  const [loaded, setLoaded] = useState(false)
  const [bgOnly, setBgOnly] = useState(false)

  // Populate polygon editors from saved corrections once mappings are ready.
  useEffect(() => {
    if (loaded || !hsMapping || !vsMapping || corrResp === undefined) return
    const corr = corrResp.data
    if (corr) {
      setHsVerts(corr.hue_saturation.map(c => dataToPixel(c, hsMapping, 'h', 's')))
      setVsVerts(corr.value_saturation.map(c => dataToPixel(c, vsMapping, 'v', 's')))
    }
    setLoaded(true)
  }, [loaded, hsMapping, vsMapping, corrResp])

  const buildPayload = useCallback(() => {
    if (!hsMapping || !vsMapping) throw new Error('Mappings not loaded')
    return {
      hue_saturation:   hsVerts.map(v => pixelToData(v, hsMapping, 'h', 's')),
      value_saturation: vsVerts.map(v => pixelToData(v, vsMapping, 'v', 's')),
    }
  }, [hsVerts, vsVerts, hsMapping, vsMapping])

  const save = useMutation({
    mutationFn: () => api.savePass3Corrections(projectId!, buildPayload()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pass3-corrections', projectId] }),
  })

  const accept = useMutation({
    mutationFn: async () => {
      await api.savePass3Corrections(projectId!, buildPayload())
      return api.acceptPass3(projectId!)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      navigate(`/projects/${projectId}`)
    },
  })

  if (!hsMapping || !vsMapping) return <div style={{ padding: 24 }}>Loading…</div>

  const hsStem = bgOnly ? 'hue_saturation_bg' : 'hue_saturation'
  const vsStem = bgOnly ? 'value_saturation_bg' : 'value_saturation'
  const hsUrl = `/api/projects/${projectId}/passes/pass3/raw/${hsStem}.png`
  const vsUrl = `/api/projects/${projectId}/passes/pass3/raw/${vsStem}.png`

  return (
    <div style={{ padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Project
      </button>
      <h1 style={{ marginTop: 0 }}>Pass 3 — Ball Color Tagging</h1>
      <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginBottom: 20, flexWrap: 'wrap' }}>
        <p style={{ color: '#555', margin: 0, maxWidth: 620 }}>
          Draw a polygon around the ball color cluster in each plot.
          <strong> Click</strong> to place a vertex ·
          <strong> Drag</strong> a vertex to reposition ·
          <strong> Click an edge</strong> to insert a vertex ·
          <strong> Delete/Backspace</strong> to remove the selected vertex.
        </p>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', whiteSpace: 'nowrap', fontSize: 14 }}>
          <input type="checkbox" checked={bgOnly} onChange={e => setBgOnly(e.target.checked)} />
          Background only
        </label>
      </div>

      <div style={{ display: 'flex', gap: 32, alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontWeight: 'bold', marginBottom: 8 }}>Hue vs Saturation</div>
          <PlotEditor
            imageUrl={hsUrl}
            mapping={hsMapping}
            vertices={hsVerts}
            onChange={setHsVerts}
          />
        </div>
        <div>
          <div style={{ fontWeight: 'bold', marginBottom: 8 }}>Value vs Saturation</div>
          <PlotEditor
            imageUrl={vsUrl}
            mapping={vsMapping}
            vertices={vsVerts}
            onChange={setVsVerts}
          />
        </div>
      </div>

      <div style={{ marginTop: 24, display: 'flex', gap: 12, alignItems: 'center' }}>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending || accept.isPending}
          style={{ padding: '8px 20px', cursor: 'pointer' }}
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={() => accept.mutate()}
          disabled={save.isPending || accept.isPending}
          style={{ padding: '8px 20px', cursor: 'pointer', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4 }}
        >
          {accept.isPending ? 'Accepting…' : 'Accept'}
        </button>
        {save.isSuccess && !save.isPending && (
          <span style={{ color: '#0a0', fontSize: 14 }}>Saved.</span>
        )}
        {(save.isError || accept.isError) && (
          <span style={{ color: '#c00', fontSize: 14 }}>
            {((save.error || accept.error) as Error)?.message}
          </span>
        )}
      </div>
    </div>
  )
}
