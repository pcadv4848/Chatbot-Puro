#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  deploy.sh — ChatBot Puro
#  Uso: ssh penido-castro-bot@187.77.235.249 'bash -s' < deploy.sh
#  Ou:  cat deploy.sh | ssh penido-castro-bot@187.77.235.249 bash
# ═══════════════════════════════════════════════════════════

BRANCH="master"
INSTALL_DIR="$HOME/chatbot-puro"
OPENWA_DIR="$HOME/openwa"
PORT_CHATBOT="${PORT:-8000}"
PORT_OPENWA="${OPENWA_PORT:-2785}"

echo "=== 1. Atualizando código ==="
cd "$INSTALL_DIR"
git pull origin "$BRANCH"

echo "=== 2. Atualizando dependências Python ==="
source .venv/bin/activate
pip install -r requirements.txt -q

echo "=== 3. Tabelas do banco ==="
python -c "
import asyncio
from src.db.models import Base
from src.db.session import engine
async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('  Tabelas OK')
asyncio.run(main())
"

echo "=== 4. Parando bot antigo ==="
pkill -f "uvicorn src.main:app" 2>/dev/null || true
screen -S chatbot -X quit 2>/dev/null || true
sleep 1

echo "=== 5. Iniciando bot ==="
screen -dmS chatbot bash -c "cd '$INSTALL_DIR' && source .venv/bin/activate && uvicorn src.main:app --host 0.0.0.0 --port $PORT_CHATBOT --workers 1"

echo "=== 6. Verificando ==="
sleep 2
if pgrep -f "uvicorn src.main:app" > /dev/null; then
    echo "  ✓ Bot rodando na porta $PORT_CHATBOT"
else
    echo "  ✗ Bot NAO iniciou — veja os logs:"
    echo "    screen -r chatbot"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Deploy concluído!                                       ║"
echo "║                                                          ║"
echo "║  Logs:  screen -r chatbot                                ║"
echo "║  QR:    http://187.77.235.249:$PORT_CHATBOT/webhook/qr      ║"
echo "╚══════════════════════════════════════════════════════════╝"
