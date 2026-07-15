#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  deploy.sh — ChatBot Puro
#  Uso: ssh user@server 'bash -s' < deploy.sh
#  Variáveis de ambiente:
#    SERVER_IP=187.77.235.249  (para links de retorno)
# ═══════════════════════════════════════════════════════════

SERVER_IP="${SERVER_IP:-}"
BRANCH="${BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/chatbot-puro}"
PORT_CHATBOT="${PORT:-8000}"

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
sleep 2

echo "=== 5. Iniciando bot ==="
cd "$INSTALL_DIR"
source .venv/bin/activate
nohup uvicorn src.main:app --host 0.0.0.0 --port "$PORT_CHATBOT" --workers 1 > chatbot.log 2>&1 &
echo $! > chatbot.pid

echo "=== 6. Verificando ==="
sleep 3
if pgrep -f "uvicorn src.main:app" > /dev/null; then
    echo "  ✓ Bot rodando na porta $PORT_CHATBOT (PID: $(cat chatbot.pid 2>/dev/null || echo '?'))"
    echo "  ✓ Logs: tail -f $INSTALL_DIR/chatbot.log"
else
    echo "  ✗ Bot NAO iniciou — veja os logs:"
    echo "    cat $INSTALL_DIR/chatbot.log"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Deploy concluído!                                       ║"
echo "║                                                          ║"
if [ -n "$SERVER_IP" ]; then
echo "║  QR:  http://${SERVER_IP}:${PORT_CHATBOT}/webhook/qr     ║"
fi
echo "╚══════════════════════════════════════════════════════════╝"
