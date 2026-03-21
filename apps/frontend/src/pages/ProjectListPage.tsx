import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

export default function ProjectListPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [newName, setNewName] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: api.listProjects,
  })

  async function handleCreate() {
    if (!newName.trim() || !uploadFile) return
    setCreating(true)
    setError(null)
    try {
      const project = await api.createProject(newName.trim())
      await api.uploadVideo(project.id, uploadFile)
      qc.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <h1>PBuddy — Projects</h1>

      {/* Create new project */}
      <section style={{ border: '1px solid #ccc', borderRadius: 8, padding: 16, marginBottom: 24 }}>
        <h2 style={{ marginTop: 0 }}>New Project</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Project name"
            style={{ flex: '1 1 200px', padding: '6px 10px', fontSize: 14 }}
          />
          <input
            type="file"
            accept="video/*"
            onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            style={{ flex: '2 1 300px' }}
          />
          <button
            onClick={handleCreate}
            disabled={creating || !newName.trim() || !uploadFile}
            style={{ padding: '6px 16px', cursor: 'pointer' }}
          >
            {creating ? 'Creating…' : 'Create & Upload'}
          </button>
        </div>
        {error && <p style={{ color: 'red', marginTop: 8 }}>{error}</p>}
      </section>

      {/* Project list */}
      {isLoading && <p>Loading…</p>}
      {projects && projects.length === 0 && <p>No projects yet. Create one above.</p>}
      {projects && projects.map((p) => (
        <div
          key={p.id}
          onClick={() => navigate(`/projects/${p.id}`)}
          style={{
            border: '1px solid #ddd', borderRadius: 8, padding: 16, marginBottom: 12,
            cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
          }}
        >
          <div>
            <strong>{p.name}</strong>
            <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
              {p.video_duration_s ? `${Math.round(p.video_duration_s / 60)} min` : 'no video'}
              {p.video_width ? ` · ${p.video_width}×${p.video_height}` : ''}
            </div>
          </div>
          <div style={{ fontSize: 12, color: '#888', textAlign: 'right' }}>
            <div>{p.status}</div>
            <div>{new Date(p.created_at).toLocaleDateString()}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
