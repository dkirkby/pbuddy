export interface PassStatus {
  pass_name: string
  state: string
  current_job_id: string | null
  updated_at: string
}

export interface ProjectSummary {
  id: string
  name: string
  status: string
  created_at: string
  updated_at: string
  video_duration_s: number | null
  video_fps: number | null
  video_width: number | null
  video_height: number | null
}

export interface ProjectDetail extends ProjectSummary {
  passes: PassStatus[]
}

export interface JobSummary {
  id: string
  project_id: string
  pass_name: string
  job_type: string
  status: string
  queued_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
}

export interface ArtifactRef {
  id: string
  project_id: string
  pass_name: string | null
  artifact_role: string
  artifact_type: string
  path: string
  created_at: string
}

export interface CourtCorner {
  x: number
  y: number
}

export interface CourtGeometry {
  top_left: CourtCorner
  top_right: CourtCorner
  bottom_left: CourtCorner
  bottom_right: CourtCorner
}

export interface StableBounds {
  in_time_s: number
  out_time_s: number
}

export interface Pass1RawResult {
  stable_bounds: StableBounds
  median_background_paths: string[]
  median_window_times: [number, number][]
  bg_width: number
  bg_height: number
}

export interface Pass1CorrectionPayload {
  court_geometry?: CourtGeometry | null
}

export interface WsEvent {
  type: string
  project_id: string
  job_id: string | null
  payload: Record<string, unknown>
}

export interface BallAnnotation {
  x: number
  y: number
  radius: number
}

export interface Pass2Corrections {
  annotations: Record<string, BallAnnotation>
  patches: Record<string, string>  // frameIndex → PNG data URL
}

export interface Pass2RawResult {
  fps: number
  bg_width: number
  bg_height: number
}

export interface Pass5Segment {
  id: number
  first_frame: number
  last_frame: number
  length: number
  detections: { frame: number; cx: number; cy: number; radius: number }[]
}

export interface Pass5Segments {
  segment_count: number
  max_gap_frames: number
  max_pixels_per_frame: number
  segments: Pass5Segment[]
}
