#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  deploy.sh — ChatBot Puro (deploy rapido via SSH)
#  Uso: ssh user@server 'bash -s' < deploy.sh
#  Variáveis de ambiente:
#    SERVER_IP=187.77.235.249  (para links de retorno)
#    PORT=8000                 (porta do chatbot)
# ═══════════════════════════════════════════════════════════

SERVER_IP="${SERVER_IP:-}"
BRANCH="${BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/chatbot/chatbot-puro}"
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
bash deploy/stop.sh 2>/dev/null || true

echo "=== 5. Iniciando bot ==="
bash deploy/start.sh

echo "=== 6. Verificando ==="
sleep 3
if pgrep -f "uvicorn src.main:app" > /dev/null; then
    echo "  ✓ Bot rodando na porta $PORT_CHATBOT"
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
