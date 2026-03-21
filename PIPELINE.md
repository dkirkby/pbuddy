# ⚙️ Pickleball Video Analysis System — Processing Pipeline

This document outlines the sequential preprocessing pipeline and user-in-the-loop workflow for the Pickleball Video Analysis System.

## 1. System Architecture Overview
The system operates as a **local web application**:
* **Backend:** A local Python-based server (e.g., FastAPI or Flask) responsible for executing heavy computer vision and physics calculations on the user's local hardware (e.g., Apple M-series or discrete GPU).
* **Frontend:** A modern JavaScript framework (e.g., React, Vue, or Svelte) served locally to the user's web browser, providing a highly interactive UI for video playback and data correction.

Because a typical game video is approximately 20 minutes long, the pipeline is strictly sequential. The backend will complete a full pass on the video file, pause to collect human-in-the-loop corrections via the frontend, and only then proceed to the next computationally expensive pass.

---

## Pass 1: Global Scene & Camera Calibration
**Goal:** Establish the physical parameters of the static scene and target object appearances before analyzing motion. The system isolates the stable video footage and generates a clean background model to understand the court geometry.

### System Actions
* **Camera Stabilization Trimming:** Automatically detects heavy global motion to clip the "initial and final camera repositioning" phases, defining the bounds of the stable video footage.
* **Background Plate Generation:** Computes a temporal median image from a sample of the stabilized frames. This effectively erases all moving objects (players, balls), leaving a pristine image of the static court environment.
* **Court Detection:** Analyzes the clean median image to detect geometric court lines and identifies the primary court of interest.
* **Generous Ball Color Profiling:** Extracts candidate ball pixels (e.g., using background subtraction to find moving blobs) and establishes a broad HSV (Hue, Saturation, Value) color profile.
  * *Note:* This profile is intentionally generous to prioritize recall; it is expected to encompass other similarly colored objects (shoes, shirts). Strict disambiguation is deferred to the size and motion constraints applied during Pass 3.

### User Input Collected
* **Stabilization Sliders:** The user is presented with a timeline showing the auto-detected stable video bounds. They can drag sliders to fine-tune the "In" and "Out" points.
* **Court Geometry Validation:** The user sees the pristine **median background image** overlaid with a digital court grid. They drag the corners and net line to perfectly align with the physical court lines.
* **Ball Color Confirmation:** The UI presents the established color profile bounds. The user can confirm, or if the system completely missed the ball hue, click on the actual ball in a raw frame to instantly reset the generous color threshold.

---

## Pass 2: Temporal Segmentation (Clipping the Points)
**Goal:** Eliminate "dead time" (e.g., picking up balls, timeouts) to save massive amounts of compute in later stages.

### System Actions
* **Activity Segmentation:** Runs a fast, low-resolution pass analyzing player movement and spatial clustering to segment the stabilized video into distinct "live point" clips.
* **Initial State Estimation:** Attempts to identify which team is serving at the start of each segmented clip based on player positioning.

### User Input Collected
* **Timeline Review:** The user reviews a timeline scrubber containing the highlighted active play segments, allowing them to trim, merge, or delete clips.
* **Match State Initialization:** For each valid rally, the user confirms or corrects the starting match state (e.g., "Team A serving from the right"). This establishes the baseline for scoring and hit attribution.

---

## Pass 3: Player & Ball Event Tracking
**Goal:** Perform the heavy computer vision tracking exclusively on the trimmed "live point" clips identified in Pass 2.

### System Actions
* **Player Tracking:** Detects and tracks the 4 players on the primary court, maintaining identity over time.
* **2D Ball Tracking:** Searches for the ball frame-by-frame utilizing the generous color profile from Pass 1, now strictly gated by size filtering and temporal motion continuity constraints to ignore distractor objects.
* **Event Detection:** Identifies paddle hits, ground bounces, and net contacts based on sharp trajectory discontinuities in the 2D tracking data.

### User Input Collected
* **Event Scrubbing (Frame Tagging):** The user scrubs the timeline to add missing events (e.g., a subtle dink) or remove false positive events (e.g., a hallucinated bounce).
* **Ball Position Correction:** The user can select specific frames and manually click/drag the ball's bounding box to correct its exact 2D pixel location, fixing tracking errors before 3D physics interpolation begins.

---

## Pass 4: 3D Physics Reconstruction & Attribution
**Goal:** Translate verified 2D tracking data and events into a physically accurate 3D model.

### System Actions
* **3D Trajectory Reconstruction:** Fits physics-constrained 3D trajectories between the verified hit and bounce events, incorporating gravity, spin-dependent drag, and lift.
* **Hit Attribution:** Assigns each verified hit to a specific player based on their tracked 3D position and the current match state.
* **Analytics Generation:** Calculates derived metrics such as shot speed, net clearance, player positioning, and unforced errors.

### User Input Collected
* **Attribution Correction:** The user resolves ambiguities (e.g., when two players swing at a middle ball) by explicitly assigning the hit to the correct player.
* **Player Identity Correction:** If players crossed paths and the tracker swapped their IDs, the user selects a frame and swaps the identities back to their correct state.

---

## Final Output: Interactive Replay
Following the completion of all passes, the user enters the final analysis interface.

### Features
* **Annotated Video:** Video playback overlaid with projected 3D ball trajectories, bounce/hit markers, and player positioning heatmaps.
* **Data Export:** The ability to export JSON/CSV files containing the event timeline, 3D ball trajectory samples, and calculated shot metrics for external analysis.