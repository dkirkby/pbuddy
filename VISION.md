# 📄 Pickleball Video Analysis System — Problem Statement & Requirements

## 1. Problem Statement

Competitive pickleball players (DUPR 3.5–4.5) lack accessible tools to quantitatively analyze match play using standard video recordings. Existing approaches either require specialized hardware, extensive manual annotation, or fail under real-world conditions such as multiple visible courts, occlusions, and variable lighting.

The goal of this software package is to analyze video from a **single, mostly fixed camera** and reconstruct:

* The **3D trajectory of the ball**
* **Key gameplay events** (hits, bounces, net contacts)
* **Player positions and identities**

The system must operate robustly in **messy, real-world environments**, optionally incorporating **limited user input** and (if viable) audio cues to improve accuracy.

---

## 2. Target Users

* Primary: Competitive recreational players (DUPR 3.5–4.5)
* Secondary: Coaches analyzing match play

---

## 3. System Scope

### Included

* Single-camera video (smartphone-quality)
* Doubles play (4 players on primary court)
* Scenes with **multiple visible courts and players**
* Per-point analysis (ball tracked only during live play)
* Optional **user-in-the-loop refinement**
* **Conditional:** Audio-assisted event detection (only if proven effective with reasonable development effort)

### Excluded (initial version)

* Multi-camera fusion
* Fully automated, zero-intervention workflows
* Spin estimation (future)
* Nighttime/artificial lighting scenarios (to avoid severe motion blur)
* Cloud-based video processing (initial version targets local compute)

---

## 4. Key Use Cases

1. **Post-match review with cluttered background**
   * Identify and analyze the correct court despite adjacent play

2. **Shot selection and placement analysis**
   * Evaluate shot intent relative to opponent positioning

3. **Movement and positioning feedback**
   * Analyze spacing, kitchen positioning, and transitions

4. **Early rally effectiveness**
   * Serve → return → 3rd → 4th shot outcomes

5. **Error diagnosis**
   * Identify unforced errors (net, long, wide)

6. **Interactive refinement workflow**
   * User corrects court geometry, ball color, serving state, or mis-tracked events

---

## 5. Architecture & Platform

* **Deployment:** Local application (desktop/laptop).
* **Target Hardware:** Apple M-series Macs or Linux workstations equipped with discrete GPUs (e.g., NVIDIA RTX 3090).
* **Rationale:** Completely bypasses the severe UX friction of uploading massive, high-framerate sports video files to the cloud. Maximizes performance and privacy.

---

## 6. Functional Requirements

### 6.1 Video & Audio Ingestion

* Accept standard smartphone video (varied resolution, frame rate).
* Extract synchronized audio track (if audio processing is implemented).
* Handle:
  * Slight camera motion (wind)
  * Initial and final camera repositioning
  * Partial court visibility

---

### 6.2 Court Detection & Selection

* Detect multiple candidate courts in frame
* Identify **primary court of interest**

#### Methods:
* Geometric detection of court lines
* Spatial clustering of player activity
* Optional user selection (click/select correct court)

#### User-in-the-loop:
* Allow user to confirm or select the correct court
* Allow user to adjust detected court boundaries

---

### 6.3 Camera Calibration & 3D Mapping

* Estimate camera parameters (intrinsics + extrinsics).
* Compute mapping: **2D image → 3D court coordinates**.

#### Constraints & Enhancements:
* Use known court dimensions and net height/position.
* Use **ball size as a scale constraint** for depth estimation.
* **Shadow utilization:** Leverage the ball's shadow on the court surface (when visible in daytime lighting) as an auxiliary geometric constraint for 3D depth estimation.
* Refine calibration via trajectory consistency.

---

### 6.4 Ball Detection & Tracking

* Detect and track ball in presence of:
  * Other balls (adjacent courts)
  * Visual clutter and motion blur
  * Daytime lighting variations (sunny or overcast)

#### Disambiguation strategies:
* Spatial filtering to primary court region.
* Temporal continuity constraints (ignoring rolling balls).
* Physics-based trajectory filtering.

#### Adaptive appearance modeling:
* Learn ball color dynamically per video.
* Allow user to confirm or override detected ball color.

---

### 6.5 3D Trajectory Reconstruction (Core)

* Reconstruct ball trajectory in 3D over time using:
  * Camera calibration
  * Known ball size
  * Physics-constrained motion while in flight, incorporating gravity, spin-dependent drag, and lift.
  * Physics-constrained change of trajectory after hitting a paddle or the ground.
* Handle occlusions via interpolation and physics constraints.

---

### 6.6 Event Detection

#### Events:
* Paddle hits
* Ground bounces
* Net contacts (dead ball vs. continuation)

#### Detection Modalities:
* **Visual cues:** Trajectory discontinuities.
* **Audio cues (Conditional):** Impact sounds (paddle/ground/net). *Note: Only to be included if proven highly effective against multi-court background noise with reasonable effort.*

#### Strategies:
* Allow user to make quick corrections to missed or false events.

---

### 6.7 Player Detection & Tracking

* Detect and track 4 players on primary court.
* Maintain identity over time despite nearby players, occlusions, and crossings.

#### Strategies:
* Spatial restriction to selected court.
* Motion continuity and team assignment via court side.
* Allow user to make corrections.

---

### 6.8 Match State & Hit Attribution

* Assign each hit to a specific player and maintain the state of the rally.

#### Inputs:
* Player positions & ball trajectory.
* Audio timing (if implemented).

#### Scoring & Server Tracking:
* Predict scoring by tracking **who serves at the start of each point**.
* **User-in-the-loop:** The system will present its "best guess" of the serving team/player at the start of a rally. The user confirms or corrects this state to ensure accurate attribution for the rest of the point.

---

### 6.9 Derived Analytics

#### 1. Player Movement
* Position relative to kitchen line and baseline.
* Movement patterns across rally phases.

#### 2. Shot Selection
* Ball placement (3D landing position).
* Shot direction relative to opponent positions.
* Ball speed at contact.

#### 3. Early Rally Outcomes
* Analyze Serve, Return, 3rd shot, and 4th shot for placement, speed, and outcome classification.

#### 4. Unforced Errors
* Detect ball into net or out of bounds, and attribute error to player.

---

### 6.10 User-in-the-Loop Refinement

The system must support **interactive correction workflows**.
* **Editable elements:** Court geometry, ball color, match state/server, event corrections, player identities.
* **Design goal:** Minimal user effort → maximal improvement in analysis accuracy.

---

### 6.11 Output Generation

* **Annotated Video:** Overlay 3D ball trajectory, bounce/hit markers, and player positions.
* **Data Export:** JSON/CSV containing event timeline, ball trajectory (3D samples), player positions, and shot metrics.

---

## 7. Non-Functional Requirements

### 7.1 Robustness

* Must operate with:
  * Multiple courts and balls visible.
  * Partial occlusions.
  * Daytime lighting conditions (sunny or overcast).

---

### 7.2 Accuracy Targets

* Ball position error: ≤ 20–30 cm
* Hit timing error: ≤ 1–2 frames
* Player position error: ≤ 50 cm

---

### 7.3 Performance

* Target hardware (M-series Mac / 3090 GPU):
  * GPU processing: ≤ 1–5× real-time
  * CPU processing: ≤ 5–20× real-time

---

### 7.4 Usability

* Interactive corrections must be fast (< a few seconds per adjustment) and visually intuitive.
* System should guide user when confidence is low.

---

## 8. Assumptions & Constraints

* Single camera, mostly fixed.
* Smartphone-quality video.
* Daytime play only (sufficient lighting for high shutter speeds).
* No markers on players or ball.
* Known court dimensions and approximate ball size.

---

## 9. Key Technical Challenges

### Primary
* **3D Ball Trajectory Reconstruction from Monocular Video in Multi-Court Scenes**

### Secondary
* Leveraging ball shadow for 2D-to-3D depth constraints.
* Disambiguating target ball from others.
* Robust court calibration with partial visibility.
* Inferring match state with minimal human intervention.

---

## 10. Success Metrics

### Quantitative
* % of rallies successfully reconstructed.
* Ball tracking continuity (frames tracked / total).
* Hit detection precision/recall.
* Correct player attribution rate.

### Qualitative
* Does user trust the analysis?
* Does minimal correction significantly improve results?
* Are insights actionable for competitive players?

---

## 11. Future Extensions

* Shot classification (drive, dink, lob).
* Spin estimation.
* Real-time feedback.
* Cross-match player comparison.
* AI coaching suggestions.

---

## 12. Design Philosophy

* **Physics-informed + data-driven hybrid**
* **Robust to real-world, multi-court environments**
* **Human-in-the-loop for critical ambiguity resolution**
* **Focus on actionable insights, not just detection**