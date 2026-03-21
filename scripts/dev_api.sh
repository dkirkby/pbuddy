#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
python3 -m uvicorn pbva_api.main:app --reload --host 127.0.0.1 --port 8000
