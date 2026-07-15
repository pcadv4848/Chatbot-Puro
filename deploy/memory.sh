#!/usr/bin/env bash
# Monitoramento de memoria para VPS 4GB
# Uso: watch -n 30 bash deploy/memory.sh
set -euo pipefail

echo "=== Memoria $(date '+%H:%M:%S') ==="
free -h 2>/dev/null | head -3

echo ""
echo "=== Top processos por RAM ==="
ps aux --sort=-%mem 2>/dev/null | head -8 || ps aux 2>/dev/null | head -8

echo ""
echo "=== Chatbot ==="
CHATBOT_PID=$(cat chatbot.pid 2>/dev/null || echo "")
if [ -n "$CHATBOT_PID" ] && kill -0 "$CHATBOT_PID" 2>/dev/null; then
    ps -p "$CHATBOT_PID" -o pid,%mem,rss,etime --no-headers 2>/dev/null || echo "  N/A"
else
    echo "  Nao rodando"
fi

echo ""
echo "=== OpenWA (Chromium) ==="
CHROME_PIDS=$(pgrep -f "chrome|chromium" 2>/dev/null || true)
if [ -n "$CHROME_PIDS" ]; then
    ps -p $CHROME_PIDS -o pid,%mem,rss,comm --no-headers 2>/dev/null | sort -k2 -rn || true
fi
