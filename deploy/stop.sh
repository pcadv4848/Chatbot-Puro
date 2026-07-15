#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f chatbot.pid ]; then
    PID=$(cat chatbot.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "  Parando chatbot (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            echo "  Forçando parada..."
            kill -9 "$PID" 2>/dev/null || true
        fi
    else
        echo "  PID $PID nao esta rodando"
    fi
    rm -f chatbot.pid
else
    echo "  Nenhum PID encontrado, buscando por processo..."
fi

pkill -f "uvicorn src.main:app" 2>/dev/null || true
sleep 1
echo "  Chatbot parado"
