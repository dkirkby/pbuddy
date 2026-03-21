#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
uv run uvicorn pbva_api.main:app --reload --host 127.0.0.1 --port 8000
