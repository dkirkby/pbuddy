# 📄 Pickleball Video Analysis System — Problem Statement & Requirements

## 1. Problem Statement

Competitive pickleball players (DUPR 3.5–4.5) lack accessible tools to quantitatively analyze match play using standard video recordings. Existing approaches either require specialized hardware, extensive manual annotation, or fail under real-world conditions such as multiple visible courts, occlusions, and variable lighting.

The goal of this software package is to analyze video from a **single, mostly fixed camera** and reconstruct:

* The **3D trajectory of the ball**
* **Key gameplay events** (hits, bounces, net contacts)
* **Player positions and identities**

The system must operate robustly in **messy, real-world environments**, optionally incorporating **audio cues and limited user input** to improve accuracy.

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
* Optional **audio-assisted event detection**
* Optional **user-in-the-loop refinement**

### Excluded (initial version)

* Multi-camera fusion
* Fully automated, zero-intervention workflows
* Spin estimation (future)

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

   * User corrects court geometry, ball color, or mis-tracked events

---

## 5. Functional Requirements

### 5.1 Video & Audio Ingestion

* Accept standard smartphone video (varied resolution, frame rate)
* Extract synchronized audio track
* Handle:

  * Slight camera motion (wind)
  * Initial and final camera repositioning
  * Partial court visibility

---

### 5.2 Court Detection & Selection

* Detect multiple candidate courts in frame
* Identify **primary court of interest**

#### Methods:

* Geometric detection of court lines
* Spatial clustering of player activity
* Optional user selection (click/select correct court)

#### User-in-the-loop:

* Allow user to:

  * Confirm or select the correct court
  * Adjust detected court boundaries

---

### 5.3 Camera Calibration & 3D Mapping

* Estimate camera parameters:

  * Intrinsics + extrinsics
* Compute mapping:

  * **2D image → 3D court coordinates**

#### Constraints:

* Use known court dimensions
* Use net height and position

#### Enhancements:

* Use **ball size as a scale constraint** for depth estimation
* Refine calibration via trajectory consistency

---

### 5.4 Ball Detection & Tracking

#### Requirements:

* Detect and track ball in presence of:

  * Other balls (adjacent courts)
  * Visual clutter
  * Motion blur
  * Variable lighting, e.g. due to nearby structures casting shadows and changing clouds

#### Disambiguation strategies:

* Spatial filtering to primary court region
* Temporal continuity constraints
* Physics-based trajectory filtering

#### Adaptive appearance modeling:

* Learn ball color dynamically per video
* Allow user to:

  * Confirm or override detected ball color

---

### 5.5 3D Trajectory Reconstruction (Core)

* Reconstruct ball trajectory in 3D over time
* Use:

  * Camera calibration
  * Known ball size
  * Physics-constrained motion while in flight, incorporating gravity, spin-dependent drag and lift
  * Physics-constrained change of trajectory after hitting a paddle or the ground

#### Robustness:

* Handle occlusions via interpolation and physics constraints

---

### 5.6 Event Detection

#### Events:

* Paddle hits
* Ground bounces
* Net contacts, where the ball might become dead, ending the point, or else change direction but continue over the net, continuing the point

#### Multi-modal detection:

* **Visual cues**:

  * Trajectory discontinuities
* **Audio cues**:

  * Impact sounds (paddle/ground/net)

#### Audio considerations:

* Multiple simultaneous games may produce interfering sounds
* Use:

  * Temporal alignment with visual events
  * Confidence scoring

#### Strategies:

* Allow user to make corrections

---

### 5.7 Player Detection & Tracking

* Detect and track 4 players on primary court
* Maintain identity over time

#### Challenges:

* Nearby players on adjacent courts
* Occlusions and crossings

#### Strategies:

* Spatial restriction to selected court
* Motion continuity
* Team assignment via court side
* Allow user to make corrections

---

### 5.8 Hit Attribution

* Assign each hit to a specific player

#### Inputs:

* Player positions
* Ball trajectory
* Audio timing (optional)

#### Requirements:

* Resolve ambiguity in doubles play
* Provide confidence score per attribution

#### Strategies:

* Track the score to know who is serving and receiving at the start of each point

---

### 5.9 Derived Analytics

#### 1. Player Movement

* Position relative to:

  * Kitchen line
  * Baseline
* Movement patterns across rally phases

---

#### 2. Shot Selection

* Ball placement (3D landing position)
* Shot direction relative to opponent positions
* Ball speed at contact

---

#### 3. Early Rally Outcomes

* Analyze:

  * Serve
  * Return
  * 3rd shot
  * 4th shot

For each:

* Placement
* Speed
* Outcome classification

---

#### 4. Unforced Errors

* Detect:

  * Ball into net
  * Ball out of bounds
* Attribute error to player

---

### 5.10 User-in-the-Loop Refinement

The system must support **interactive correction workflows**:

#### Editable elements:

* Court geometry (drag corners/lines)
* Ball color / detection parameters
* Event corrections (add/remove hits, bounces)
* Player identity corrections

#### Design goal:

> Minimal user effort → maximal improvement in analysis accuracy

---

### 5.11 Output Generation

#### Annotated Video

* Overlay:

  * 3D ball trajectory (projected)
  * Bounce markers
  * Hit markers
  * Player positions

---

#### Data Export

* JSON/CSV:

  * Event timeline
  * Ball trajectory (3D samples)
  * Player positions
  * Shot metrics

---

## 6. Non-Functional Requirements

### 6.1 Robustness

* Must operate with:

  * Multiple courts and balls visible
  * Partial occlusions
  * Variable lighting and color conditions

---

### 6.2 Accuracy Targets

* Ball position error: ≤ 20–30 cm
* Hit timing error: ≤ 1–2 frames
* Player position error: ≤ 50 cm

---

### 6.3 Performance

* Offline processing:

  * GPU: ≤ 1–5× real-time
  * CPU: ≤ 5–20× real-time

---

### 6.4 Usability

* Interactive corrections must be:

  * Fast (< a few seconds per adjustment)
  * Visually intuitive
* System should guide user when confidence is low

---

## 7. Assumptions & Constraints

* Single camera, mostly fixed
* Smartphone-quality video + audio
* No markers on players or ball
* Known:

  * Court dimensions
  * Approximate ball size

---

## 8. Key Technical Challenges

### Primary

**3D Ball Trajectory Reconstruction from Monocular Video in Multi-Court Scenes**

---

### Secondary

* Disambiguating target ball from others
* Fusing audio + visual event detection
* Robust court calibration with partial visibility
* Player tracking with nearby distractors

---

## 9. Success Metrics

### Quantitative

* % of rallies successfully reconstructed
* Ball tracking continuity (frames tracked / total)
* Hit detection precision/recall
* Correct player attribution rate

---

### Qualitative

* Does user trust the analysis?
* Does minimal correction significantly improve results?
* Are insights actionable for competitive players?

---

## 10. Future Extensions

* Shot classification (drive, dink, lob)
* Spin estimation
* Real-time feedback
* Cross-match player comparison
* AI coaching suggestions

---

## 11. Design Philosophy

* **Physics-informed + data-driven hybrid**
* **Robust to real-world, multi-court environments**
* **Human-in-the-loop for critical ambiguity resolution**
* **Focus on actionable insights, not just detection**
