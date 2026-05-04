"""Schema-driven pipeline dependency graph.

Loads pipeline_schema.json once at module import and derives two mappings:

  RUN_DEPENDENTS[pass_Q]      — passes that become dirty when pass Q is re-run
  SETTINGS_DEPENDENTS[pass_Q] — passes that become dirty when pass Q's settings change

Used by the API to mark downstream passes dirty instead of resetting them.
"""

from __future__ import annotations

import json
from pathlib import Path

# Locate pipeline_schema.json: walk up from this file to find the repo root.
_HERE = Path(__file__).resolve()
_SCHEMA_PATH: Path | None = None
for _parent in _HERE.parents:
    _candidate = _parent / "pipeline_schema.json"
    if _candidate.exists():
        _SCHEMA_PATH = _candidate
        break

if _SCHEMA_PATH is None:
    raise FileNotFoundError(
        "pipeline_schema.json not found in any ancestor directory of "
        f"{_HERE}"
    )

_schema = json.loads(_SCHEMA_PATH.read_text())

# Build the two dependency maps by inspecting every raw artifact's depends_on list.
# We also check accepted_artifacts for any accepted-only artifacts with cross-pass deps.

_run_dependents: dict[str, set[str]] = {}
_settings_dependents: dict[str, set[str]] = {}

for pass_name, pass_def in _schema["passes"].items():
    artifact_groups = []
    if "raw_artifacts" in pass_def:
        artifact_groups.append(pass_def["raw_artifacts"])
    if "accepted_artifacts" in pass_def:
        artifact_groups.append(pass_def["accepted_artifacts"])

    for artifact_group in artifact_groups:
        for _artifact_name, artifact_def in artifact_group.items():
            for dep in artifact_def.get("depends_on", []):
                dep_type = dep["type"]
                if dep_type == "artifact":
                    upstream_pass = dep["pass"]
                    # Skip self-references (same-pass artifact dependencies are
                    # intra-pass ordering, not cross-pass dirty propagation).
                    if upstream_pass != pass_name:
                        _run_dependents.setdefault(upstream_pass, set()).add(pass_name)
                elif dep_type == "setting":
                    upstream_pass = dep["pass"]
                    # Skip self-references: a pass's accepted artifacts depending on
                    # its own settings is handled by the accept endpoint, not by
                    # cross-pass dirty marking.
                    if upstream_pass != pass_name:
                        _settings_dependents.setdefault(upstream_pass, set()).add(pass_name)

# Convert sets to sorted lists for deterministic iteration.
RUN_DEPENDENTS: dict[str, list[str]] = {
    k: sorted(v) for k, v in _run_dependents.items()
}
SETTINGS_DEPENDENTS: dict[str, list[str]] = {
    k: sorted(v) for k, v in _settings_dependents.items()
}
