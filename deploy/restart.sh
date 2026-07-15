#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Reiniciando ChatBot ==="
bash deploy/stop.sh
sleep 2
bash deploy/start.sh

echo ""
echo "=== Logs (tail -f chatbot.log) ==="
