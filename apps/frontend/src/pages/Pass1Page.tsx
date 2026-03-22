import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { CourtOverlay } from '../components/CourtOverlay'
import { useEditorStore } from '../state/editorStore'
import type { ArtifactRef, CourtGeometry, Pass1RawResult } from '../types/api'

// Working resolution of median background image.
const BG_W = 960
const BG_H = 540

// Default court corners (normalized screen coords → pixel coords).
// Matches the initial overlay shown before the user refines by dragging.
const DEFAULT_COURT: CourtGeometry = {
  top_left:     { x: 0.35 * BG_W, y: 0.30 * BG_H },
  top_right:    { x: 0.65 * BG_W, y: 0.30 * BG_H },
  bottom_left:  { x: 0.05 * BG_W, y: 0.90 * BG_H },
  bottom_right: { x: 0.95 * BG_W, y: 0.90 * BG_H },
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

  // Load raw result JSON (needed for stable_bounds).
  const { data: rawResult } = useQuery<Pass1RawResult>({
    queryKey: ['pass1-raw', projectId],
    queryFn: async () => {
      const resp = await fetch(api.artifactUrl(rawJsonArtifact!.id))
      return resp.json()
    },
    enabled: !!rawJsonArtifact,
  })

  // Load saved corrections (may be null if none submitted yet).
  const { data: corrResp } = useQuery({
    queryKey: ['pass1-corrections', projectId],
    queryFn: () => api.getPass1Corrections(projectId!),
    enabled: !!rawJsonArtifact,
  })

  useEffect(() => {
    editor.reset()
  }, [projectId])

  useEffect(() => {
    if (!rawResult || corrResp === undefined) return
    editor.initFromRaw(
      corrResp.data?.stable_bounds ?? rawResult.stable_bounds,
      corrResp.data?.court_geometry ?? DEFAULT_COURT,
    )
  }, [rawResult, corrResp])

  async function handleSave() {
    if (!editor.stableBounds || !editor.courtGeometry) return
    setSaving(true)
    setStatusMsg(null)
    try {
      await api.submitPass1Corrections(projectId!, {
        stable_bounds: editor.stableBounds,
        court_geometry: editor.courtGeometry,
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
            Drag the blue handles to align the four court corners. Interior lines and net are computed automatically.
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
