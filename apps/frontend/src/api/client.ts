import type {
  ArtifactRef,
  BallAnnotation,
  JobSummary,
  Pass1CorrectionPayload,
  Pass2Corrections,
  ProjectDetail,
  ProjectSummary,
} from '../types/api'

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${method} ${path} → ${res.status}: ${text}`)
  }
  return res.json()
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body)
const put = <T>(path: string, body?: unknown) => request<T>('PUT', path, body)

export const api = {
  listProjects: () => get<ProjectSummary[]>('/api/projects'),

  createProject: (name: string) =>
    post<ProjectDetail>('/api/projects', { name }),

  getProject: (id: string) => get<ProjectDetail>(`/api/projects/${id}`),

  deleteProject: (id: string) =>
    fetch(`/api/projects/${id}`, { method: 'DELETE' }).then((r) => {
      if (!r.ok) throw new Error(`Delete failed: ${r.status}`)
    }),

  uploadVideo: async (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`/api/projects/${projectId}/video`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
    return res.json()
  },

  runPass1: (projectId: string) =>
    post<{ ok: boolean; data: JobSummary }>(`/api/projects/${projectId}/passes/pass1/run`),

  getPass: (projectId: string, passName: string) =>
    get<{ ok: boolean; data: { pass_name: string; state: string; current_job_id: string | null } }>(
      `/api/projects/${projectId}/passes/${passName}`
    ),

  getPass1Artifacts: (projectId: string) =>
    get<{ ok: boolean; data: ArtifactRef[] }>(`/api/projects/${projectId}/passes/pass1/artifacts`),

  getPass1Corrections: (projectId: string) =>
    get<{ ok: boolean; data: Pass1CorrectionPayload | null }>(
      `/api/projects/${projectId}/passes/pass1/corrections`
    ),

  submitPass1Corrections: (projectId: string, corrections: Pass1CorrectionPayload) =>
    put<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass1/corrections`, corrections),

  acceptPass1: (projectId: string) =>
    post<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass1/accept`),

  runPass2: (projectId: string) =>
    post<{ ok: boolean; data: JobSummary }>(`/api/projects/${projectId}/passes/pass2/run`),

  runPass3: (projectId: string) =>
    post<{ ok: boolean; data: JobSummary }>(`/api/projects/${projectId}/passes/pass3/run`),

  getPass3PlotMapping: (projectId: string, stem: string) =>
    get<Record<string, number>>(`/api/projects/${projectId}/passes/pass3/raw/${stem}.json`),

  getPass3Corrections: (projectId: string) =>
    get<{ ok: boolean; data: { hue_saturation: [number, number][]; value_saturation: [number, number][] } | null }>(
      `/api/projects/${projectId}/passes/pass3/corrections`
    ),

  savePass3Corrections: (projectId: string, body: { hue_saturation: [number, number][]; value_saturation: [number, number][] }) =>
    put<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass3/corrections`, body),

  acceptPass3: (projectId: string) =>
    post<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass3/accept`),

  getPass2Artifacts: (projectId: string) =>
    get<{ ok: boolean; data: ArtifactRef[] }>(`/api/projects/${projectId}/passes/pass2/artifacts`),

  getPass2Corrections: (projectId: string) =>
    get<{ ok: boolean; data: Pass2Corrections }>(`/api/projects/${projectId}/passes/pass2/corrections`),

  savePass2Annotations: (projectId: string, annotations: Record<string, BallAnnotation>, patches: Record<string, string>, minBallRadius: number, maxBallRadius: number) =>
    put<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass2/corrections`, { annotations, patches, min_ball_radius: minBallRadius, max_ball_radius: maxBallRadius }),

  acceptPass2: (projectId: string) =>
    post<{ ok: boolean }>(`/api/projects/${projectId}/passes/pass2/accept`),

  videoUrl: (projectId: string) => `/api/projects/${projectId}/video`,

  artifactUrl: (artifactId: string) => `/api/artifacts/${artifactId}`,
}
