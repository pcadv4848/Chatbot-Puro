#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== ChatBot Status ==="

if [ -f chatbot.pid ]; then
    PID=$(cat chatbot.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "  PID: $PID (rodando)"
        ps -p "$PID" -o pid,etime,%mem,%cpu,rss --no-headers 2>/dev/null || true
    else
        echo "  PID: $PID (morto — PID file stale)"
    fi
else
    echo "  PID file nao encontrado"
fi

BOT_PIDS=$(pgrep -f "uvicorn src.main:app" 2>/dev/null || true)
if [ -n "$BOT_PIDS" ]; then
    echo "  Processos uvicorn ativos: $BOT_PIDS"
    ps -p $BOT_PIDS -o pid,etime,%mem,%cpu,rss --no-headers 2>/dev/null || true
else
    echo "  Nenhum processo uvicorn encontrado"
fi

echo ""
echo "=== OpenWA Status ==="
OPENWA_PIDS=$(pgrep -f "openwa" 2>/dev/null || true)
if [ -n "$OPENWA_PIDS" ]; then
    echo "  Processos OpenWA: $OPENWA_PIDS"
else
    echo "  Nenhum processo OpenWA encontrado"
fi

echo ""
echo "=== Portas ==="
PORT_CHATBOT="${PORT:-8000}"
echo "  Chatbot: $(ss -tlnp 2>/dev/null | grep ":$PORT_CHATBOT " || echo 'porta $PORT_CHATBOT nao escutando')"
echo "  OpenWA:  $(ss -tlnp 2>/dev/null | grep ':2785 ' || echo 'porta 2785 nao escutando')"

echo ""
echo "=== Memoria do Sistema ==="
free -h 2>/dev/null || cat /proc/meminfo 2>/dev/null | head -5 || echo "  N/A"

echo ""
echo "=== Health Check ==="
PORT="${PORT:-8000}"
curl -sf "http://127.0.0.1:$PORT/health" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  Health check falhou"
