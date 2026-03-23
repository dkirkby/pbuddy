/**
 * Re-exports typed constants from the repo-root dimensions.json.
 * This is the single authoritative source for all pickleball physical dimensions.
 * Import from here rather than hardcoding values anywhere in the frontend.
 */
import raw from '../../../../dimensions.json'

export const COURT_TOTAL_LENGTH        = raw.court_dimensions.total_length         // 13.41 m
export const COURT_TOTAL_WIDTH         = raw.court_dimensions.total_width          // 6.10 m
export const COURT_NON_VOLLEY_DEPTH    = raw.court_dimensions.non_volley_zone_depth // 2.13 m
export const COURT_SERVICE_AREA_LENGTH = raw.court_dimensions.service_area_length  // 4.57 m
export const COURT_SERVICE_AREA_WIDTH  = raw.court_dimensions.service_area_width   // 3.05 m
export const COURT_LINE_THICKNESS      = raw.court_dimensions.line_thickness       // 0.05 m

export const NET_POST_HEIGHT    = raw.net_specifications.post_height        // 0.91 m
export const NET_CENTER_HEIGHT  = raw.net_specifications.center_height_dip  // 0.86 m
export const NET_POST_TO_POST   = raw.net_specifications.post_to_post_width // 6.71 m

export const BALL_DIAMETER_MIN  = raw.ball_specifications.diameter_range.min // 73 mm
export const BALL_DIAMETER_MAX  = raw.ball_specifications.diameter_range.max // 75 mm
export const BALL_WEIGHT_MIN    = raw.ball_specifications.weight_range.min    // 22.1 g
export const BALL_WEIGHT_MAX    = raw.ball_specifications.weight_range.max    // 26.5 g
export const BALL_PATCH_RADIUS  = raw.ball_specifications.patch_radius_px    // 32 px

export const VOLUME_BOUNDARY_EXTENSION = raw.valid_ball_volume.boundary_extension // 0.5 m
export const VOLUME_CORNER_HEIGHT      = raw.valid_ball_volume.corner_height      // 1.0 m
export const VOLUME_NET_HEIGHT         = raw.valid_ball_volume.net_height         // 3.0 m

/**
 * Normalised v-coordinate of the kitchen (non-volley zone) line.
 * Derived from total_length and non_volley_zone_depth:
 *   the kitchen line is non_volley_zone_depth from the net (at v=0.5),
 *   so it sits at (half_length − non_volley_zone_depth) / total_length from each baseline.
 */
export const COURT_KV =
  (COURT_TOTAL_LENGTH / 2 - COURT_NON_VOLLEY_DEPTH) / COURT_TOTAL_LENGTH
