import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { CourtOverlay } from '../components/CourtOverlay'
import { useEditorStore } from '../state/editorStore'
import type { ArtifactRef, Pass1RawResult } from '../types/api'

// Working resolution of median background image.
const BG_W = 960
const BG_H = 540

/** Convert HSV (OpenCV range H∈[0,180], S∈[0,255], V∈[0,255]) to CSS hex. */
function hsvToHex([h, s, v]: number[]): string {
  const H = (h / 180) * 360
  const S = s / 255
  const V = v / 255
  const c = V * S
  const x = c * (1 - Math.abs(((H / 60) % 2) - 1))
  const m = V - c
  let r = 0, g = 0, b = 0
  if (H < 60) { r = c; g = x }
  else if (H < 120) { r = x; g = c }
  else if (H < 180) { g = c; b = x }
  else if (H < 240) { g = x; b = c }
  else if (H < 300) { r = x; b = c }
  else { r = c; b = x }
  const toHex = (n: number) => Math.round((n + m) * 255).toString(16).padStart(2, '0')
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

export default function Pass1Page() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const editor = useEditorStore()
  const [saving, setSaving] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  // Load artifacts for pass1.
  const { data: artResp } = useQuery({
    queryKey: ['pass1-artifacts', projectId],
    queryFn: () => api.getPass1Artifacts(projectId!),
  })
  const artifacts: ArtifactRef[] = artResp?.data ?? []
  const rawJsonArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'json'
  )
  const bgArtifact = artifacts.find(
    (a) => a.artifact_role === 'raw' && a.artifact_type === 'png' && a.path.includes('median_background')
  )
  // Load raw result JSON and init editor.
  const { data: rawResult } = useQuery<Pass1RawResult>({
    queryKey: ['pass1-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

  useEffect(() => {
    if (rawResult && !editor.courtGeometry) {
      editor.initFromRaw(rawResult.stable_bounds, rawResult.court_geometry, rawResult.ball_color_model)
    }
  }, [rawResult])

  async function handleSave() {
    if (!editor.stableBounds || !editor.courtGeometry || !editor.ballColorModel) return
    setSaving(true)
    setStatusMsg(null)
    try {
      await api.submitPass1Corrections(projectId!, {
        stable_bounds: editor.stableBounds,
        court_geometry: editor.courtGeometry,
        ball_color_model: editor.ballColorModel,
      })
      editor.markClean()
      setStatusMsg('Corrections saved.')
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
      if (editor.isDirty) await handleSave()
      await api.acceptPass1(projectId!)
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      editor.reset()
      navigate(`/projects/${projectId}`)
    } catch (e: any) {
      setStatusMsg('Error: ' + e.message)
    } finally {
      setAccepting(false)
    }
  }

  const bgUrl = bgArtifact ? api.artifactUrl(bgArtifact.id) : null

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <button onClick={() => navigate(`/projects/${projectId}`)} style={{ marginBottom: 16 }}>
        ← Back to Project
      </button>
      <h1>Pass 1 Review — Scene Calibration</h1>

      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {/* ── Left column: controls ── */}
        <div style={{ flex: '0 0 260px' }}>

          {/* Stable bounds */}
          <section style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>Stable Video Bounds</h3>
            {editor.stableBounds ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label>
                  In (s):&nbsp;
                  <input
                    type="number"
                    value={editor.stableBounds.in_time_s}
                    step={0.5} min={0}
                    onChange={(e) =>
                      editor.setStableBounds({ ...editor.stableBounds!, in_time_s: +e.target.value })
                    }
                    style={{ width: 80 }}
                  />
                </label>
                <label>
                  Out (s):&nbsp;
                  <input
                    type="number"
                    value={editor.stableBounds.out_time_s}
                    step={0.5}
                    onChange={(e) =>
                      editor.setStableBounds({ ...editor.stableBounds!, out_time_s: +e.target.value })
                    }
                    style={{ width: 80 }}
                  />
                </label>
              </div>
            ) : <p style={{ color: '#aaa' }}>Loading…</p>}
          </section>

          {/* Ball colour */}
          <section style={{ marginBottom: 24 }}>
            <h3 style={{ marginTop: 0 }}>Ball Color (HSV)</h3>
            {editor.ballColorModel ? (
              <div style={{ fontSize: 13 }}>
                <div>Lower: <span style={{ fontFamily: 'monospace' }}>
                  H={editor.ballColorModel.hsv_lower[0].toFixed(0)}{' '}
                  S={editor.ballColorModel.hsv_lower[1].toFixed(0)}{' '}
                  V={editor.ballColorModel.hsv_lower[2].toFixed(0)}
                </span></div>
                <div>Upper: <span style={{ fontFamily: 'monospace' }}>
                  H={editor.ballColorModel.hsv_upper[0].toFixed(0)}{' '}
                  S={editor.ballColorModel.hsv_upper[1].toFixed(0)}{' '}
                  V={editor.ballColorModel.hsv_upper[2].toFixed(0)}
                </span></div>
                <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                  <div title="Lower" style={{ width: 32, height: 32, borderRadius: 4, border: '1px solid #ccc', background: hsvToHex(editor.ballColorModel.hsv_lower) }} />
                  <div title="Upper" style={{ width: 32, height: 32, borderRadius: 4, border: '1px solid #ccc', background: hsvToHex(editor.ballColorModel.hsv_upper) }} />
                </div>
              </div>
            ) : <p style={{ color: '#aaa' }}>Loading…</p>}
          </section>

          {/* Confidence */}
          {rawResult && (
            <section style={{ marginBottom: 24 }}>
              <h3 style={{ marginTop: 0 }}>Confidence</h3>
              {Object.entries(rawResult.confidence).map(([k, v]) => (
                <div key={k} style={{ fontSize: 13, marginBottom: 6 }}>
                  {k}: <strong>{(v * 100).toFixed(0)}%</strong>
                  <div style={{ background: '#eee', borderRadius: 4, height: 6, marginTop: 2 }}>
                    <div style={{
                      width: `${Math.min(v * 100, 100)}%`,
                      background: v > 0.5 ? '#0a0' : '#f90',
                      borderRadius: 4, height: 6,
                    }} />
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {editor.isDirty && (
              <div style={{ color: '#f90', fontSize: 12 }}>⚠ Unsaved changes</div>
            )}
            <button onClick={handleSave} disabled={saving || !editor.isDirty} style={{ padding: '8px 0' }}>
              {saving ? 'Saving…' : 'Save Corrections'}
            </button>
            <button
              onClick={handleAccept}
              disabled={accepting}
              style={{ padding: '8px 0', background: '#0a0', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            >
              {accepting ? 'Accepting…' : 'Accept Pass 1 →'}
            </button>
            {statusMsg && (
              <p style={{ fontSize: 12, color: statusMsg.startsWith('Error') ? 'red' : 'green', margin: 0 }}>
                {statusMsg}
              </p>
            )}
          </div>
        </div>

        {/* ── Right column: image ── */}
        <div style={{ flex: '1 1 600px' }}>
          <h3 style={{ marginTop: 0 }}>Median Background</h3>
          <p style={{ fontSize: 12, color: '#666', marginTop: 0 }}>
            Drag the blue handles to adjust court corners; orange handles to adjust the net line.
          </p>
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
            {bgUrl ? (
              <img src={bgUrl} alt="Median background" style={{ maxWidth: '100%', display: 'block' }} />
            ) : (
              <div style={{
                width: 600, height: 338, background: '#222',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555',
              }}>
                {rawJsonArtifact ? 'Loading image…' : 'No artifacts yet — pass 1 may still be running.'}
              </div>
            )}
            {bgUrl && editor.courtGeometry && (
              <CourtOverlay
                geometry={editor.courtGeometry}
                imageWidth={BG_W}
                imageHeight={BG_H}
                onChange={editor.setCourtGeometry}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
