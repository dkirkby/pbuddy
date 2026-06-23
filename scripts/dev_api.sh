#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
pkill -f "pbva_worker.main" 2>/dev/null && echo "Killed stale worker(s)" || true
uv run uvicorn pbva_api.main:app --reload --host 127.0.0.1 --port 8000
