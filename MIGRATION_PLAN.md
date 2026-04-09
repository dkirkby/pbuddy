# Migration Plan: Artifact-Validity-Based Project View

## Goals
1. All pass review pages accessible at any time (not just when `waiting_for_user`)
2. Setting changes mark downstream passes **dirty** rather than deleting artifacts
3. Re-running a pass marks downstream passes dirty (not reset to `not_started`)
4. Re-Run buttons are visually activated when a pass is dirty

## Implementation Order
1. Phase 2 (dependency module) — pure logic, no side effects, easily tested
2. Phase 1 (DB column) — needed before Phase 3/4 can write to it
3. Phase 3 (run_pass change) — highest impact, stops destroying downstream work
4. Phase 4 (corrections dirty marking) — enables setting-change tracking
5. Phase 5 (API response) — exposes new field to frontend
6. Phase 6 (ProjectStatus simplification) — cleanup
7. Phase 7 + 8 (frontend) — UI changes last, once backend is solid

---

## Phase 1 — DB Schema: Add `is_dirty` flag [x]

**File: `packages/db/src/pbva_db/models.py`**

Add one column to `Pass`:
```python
is_dirty: Mapped[bool] = mapped_column(default=False)
```

Applied via an `ALTER TABLE` guard in `engine.py` executed at startup (consistent with current approach — no Alembic).

---

## Phase 2 — Schema-Driven Dependency Graph [x]

**New file: `packages/core/src/pbva_core/dependencies.py`**

Load `pipeline_schema.json` once at module import and compute two dictionaries:

```python
# pass Q re-ran → which other passes are now stale?
RUN_DEPENDENTS: dict[str, list[str]]

# pass Q settings changed → which other passes are now stale?
SETTINGS_DEPENDENTS: dict[str, list[str]]
```

These replace the hardcoded `_DOWNSTREAM_PASSES` in `passes.py`. Derivation logic:
- For each pass P's raw artifact, walk its `depends_on` list
- `type: "artifact"` with `pass: Q` (Q ≠ P) → P appears in `RUN_DEPENDENTS[Q]`
- `type: "setting"` with `pass: Q` → P appears in `SETTINGS_DEPENDENTS[Q]`

Expected result (derivable from the schema):
- `RUN_DEPENDENTS["pass1"]` → `["pass2", "pass3", "pass4", "pass6"]`
- `SETTINGS_DEPENDENTS["pass1"]` → `["pass3", "pass4"]` (court_corners)
- `SETTINGS_DEPENDENTS["pass2"]` → `["pass3", "pass4", "pass6"]`
- `SETTINGS_DEPENDENTS["pass3"]` → `["pass4"]`
- `SETTINGS_DEPENDENTS["pass5"]` → `["pass6"]`

---

## Phase 3 — Update `run_pass` Endpoint [x]

**File: `apps/api/src/pbva_api/routes/passes.py`**

Replace the current cascade logic that resets downstream passes to `not_started` with:

```python
for downstream in RUN_DEPENDENTS.get(pass_name, []):
    ds_row = ...  # fetch row
    if ds_row is not None and ds_row.state not in (not_started, queued, running):
        ds_row.is_dirty = True
        ds_row.updated_at = _utcnow()
```

- **Do not** reset `state`, artifact pointer fields
- **Do not** delete downstream raw artifact files
- Still cancel a running pass4 job when pass1/2/3 are re-run
- Still clear pass2 corrections when re-running pass2 (user starts a fresh annotation session)
- Set `is_dirty = False` on the pass being run itself
- Remove `_RUN_RESETS_STATUS` dict and `project.status` linear update (phased out in Phase 6)

---

## Phase 4 — Mark Dirty When Corrections Saved [x]

**File: `apps/api/src/pbva_api/routes/passes.py`** — corrections PUT handlers

After each `save_passN_corrections` writes the file:

```python
for downstream in SETTINGS_DEPENDENTS.get(pass_name, []):
    ds_row = ...  # fetch row
    if ds_row is not None and ds_row.state not in (not_started, queued, running):
        ds_row.is_dirty = True
        ds_row.updated_at = _utcnow()
db.commit()
```

Covers: court_corners → pass3, pass4 dirty; ball_annotations → pass3, pass4, pass6 dirty; color_polygons → pass4 dirty; deleted_segments → pass6 dirty.

---

## Phase 5 — Expose `is_dirty` in API Response [x]

**File: `packages/core/src/pbva_core/types.py`**
Add `is_dirty: bool = False` to the pass summary type used in project responses.

**File: `apps/api/src/pbva_api/routes/projects.py`**
Populate `is_dirty` from `pass_row.is_dirty` when building the project response.

---

## Phase 6 — Simplify `ProjectStatus` [x]

**File: `packages/core/src/pbva_core/enums.py`**

Replace the 14-value linear `ProjectStatus` enum with three values:
```python
class ProjectStatus(str, Enum):
    created = "created"
    video_ready = "video_ready"
    in_progress = "in_progress"
```

Update all references. Sequential progress info now comes from individual pass states.

---

## Phase 7 — Frontend: Review Always Accessible [x]

**File: `apps/frontend/src/pages/ProjectHome.tsx`**

In `PassCard`, change the Review button condition from `state === 'waiting_for_user'` to
`state === 'waiting_for_user' || state === 'accepted'`.

Change `prereqMet` from a hard blocker to a soft warning: show an amber notice instead of disabling the card.

---

## Phase 8 — Frontend: Dirty State UI [x]

**File: `apps/frontend/src/pages/ProjectHome.tsx`**

Add `isDirty: boolean` to `PassCardProps`. When `isDirty`:
- Add amber left border on the card
- Show a "Stale" badge next to the state label
- Re-Run button uses amber styling instead of gray

---

## Out of Scope (for now)
- Artifact-level dirty tracking (pass-level granularity is sufficient)
- Timestamp-based computed dirtiness (stored `is_dirty` boolean is simpler)
- Alembic migrations
- Removing `produced_raw_output` pass state

---

## Progress Log

- [x] Phase 1: DB schema — `is_dirty: bool` column added to `Pass`; ALTER TABLE guard in `engine.py`
- [x] Phase 2: Dependency graph module — `packages/core/src/pbva_core/dependencies.py` with `RUN_DEPENDENTS` and `SETTINGS_DEPENDENTS` dicts derived from `pipeline_schema.json`
- [x] Phase 3: run_pass endpoint — cascade now marks downstream dirty instead of resetting to `not_started`; pass4 raw files no longer deleted on upstream re-run
- [x] Phase 4: Corrections dirty marking — `_mark_settings_dirty()` helper called in all corrections PUT handlers (pass1, pass2, pass3, pass5); all corrections endpoints now accept `accepted` state in addition to `waiting_for_user`
- [x] Phase 5: API response — `is_dirty` exposed via `PassStatusSummary` and populated in `projects.py`
- [x] Phase 6: ProjectStatus simplification — enum reduced to `created`, `video_ready`, `in_progress`; all legacy values removed; worker and API updated
- [x] Phase 7: Frontend review access — Review button shown for both `waiting_for_user` and `accepted` states; `prereqMet` is now a soft warning, not a hard blocker
- [x] Phase 8: Frontend dirty UI — amber left border, "Stale" badge, and amber Re-Run button styling when `is_dirty` is true

All 24 unit + integration tests pass (excluding pre-existing slow tests that require `ffprobe`/`test.mp4`).
