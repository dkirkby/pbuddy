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

export interface Pass0RawResult {
  bg_width: number
  bg_height: number
  median_count: number
  midpoint_chunk: number
  video_fps: number
}

export interface Pass0CorrectionPayload {
  court_geometry?: CourtGeometry | null
  k1?: number | null
}

export interface Pass1Sample {
  s: number    // -1 to +1 along segment
  val: number  // V - S/2 value
}

export interface Pass1SamplePoint {
  sx: number; sy: number
  px1: number; py1: number
  px2: number; py2: number
  samples?: Pass1Sample[]  // not stored in JSON; reconstructed on the frontend
}

export interface Pass1CourtLine {
  name: string
  color: string
  points: Pass1SamplePoint[]
}

export interface Pass1ChunkProfiles {
  chunk_index: number
  vals: number[][][]  // [line_idx][point_idx][sample_idx]
}

export interface Pass1SegmentAnalysis {
  reference: number[]              // gradient reference curve, length = perp_seg_points
  lags: (number | null)[]         // per-chunk lag in sample units (null = not clean)
  similarities: (number | null)[] // per-chunk ZNCC similarity
}

export interface Pass1RawResult {
  bg_width: number
  bg_height: number
  midpoint_chunk_index: number
  perp_seg_length_px: number
  perp_seg_points: number
  court_lines: Pass1CourtLine[]    // geometry only, chunk-independent
  chunks: Pass1ChunkProfiles[]     // per-chunk sampled values
  segment_analyses?: Pass1SegmentAnalysis[][]  // [line_idx][point_idx]
}

export interface Pass1CorrectionPayload {
  [key: string]: never
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

export interface PlayerNames {
  far_team_right: string
  far_team_left: string
  near_team_right: string
  near_team_left: string
}

export interface RallyRecord {
  score: string
  start_frame: number
  stop_frame: number
  serverName: string
  receiverName: string
  servingTeamWinsRally: boolean
}

export interface Pass2Corrections {
  annotations: Record<string, BallAnnotation>
  patches: Record<string, string>  // frameIndex → PNG data URL
  player_names?: PlayerNames
  far_team_serves_first?: boolean | null
  rally?: RallyRecord[]
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
  mean_speed_px_per_frame: number
  detections: { frame: number; cx: number; cy: number; radius: number }[]
}

export interface Pass5Segments {
  segment_count: number
  max_gap_frames: number
  large_gate_px: number
  small_gate_px: number
  min_segment_length: number
  segments: Pass5Segment[]
}

export interface Pass6RawResult {
  rally_count: number
  output_duration_s: number
  chapter_timestamps?: string
}
