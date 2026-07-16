#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${UVICORN_WORKERS:-1}"
