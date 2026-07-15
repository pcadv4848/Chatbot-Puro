#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "  .env nao encontrado! Execute: bash deploy/setup.sh"
    exit 1
fi

source .venv/bin/activate

PORT="${PORT:-8000}"
LOG_FILE="${LOG_FILE:-chatbot.log}"
MEMORY_LIMIT="${MEMORY_LIMIT:-2500}"  # MB

ulimit -v $((MEMORY_LIMIT * 1024)) 2>/dev/null || echo "  ulimit nao disponivel (ignorado)"

exec nohup uvicorn src.main:app --host 0.0.0.0 --port "$PORT" --workers 1 > "$LOG_FILE" 2>&1 &
echo $! > chatbot.pid
echo "  Chatbot iniciado (PID: $(cat chatbot.pid), porta: $PORT, RAM limit: ${MEMORY_LIMIT}MB)"
