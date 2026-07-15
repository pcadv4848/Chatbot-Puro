#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ChatBot Puro — Instalação SEM sudo (usuário comum)
#  Uso: bash install_nosudo.sh
#  Variáveis de ambiente:
#    SERVER_IP=187.77.235.249  (para links de retorno no final)
# ═══════════════════════════════════════════════════════════

REPO_URL="${REPO_URL:-https://github.com/pcadv4848/Chatbot-Puro.git}"
BRANCH="${BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/chatbot-puro}"
SERVER_IP="${SERVER_IP:-}"
PORT_CHATBOT="${PORT:-8000}"

echo "=== 1. Verificando ferramentas disponíveis ==="
for cmd in python3 git node npm; do
    if command -v "$cmd" &>/dev/null; then
        echo "  ✓ $cmd encontrado: $(command -v "$cmd")"
    else
        echo "  ✗ $cmd NÃO encontrado"
    fi
done

PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10 python3.13; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        echo "  Usando: $($PYTHON --version)"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo ">>> Python 3 não encontrado. Instale com pyenv:"
    echo "    curl https://pyenv.run | bash"
    echo "    pyenv install 3.12"
    exit 1
fi

echo "=== 2. Clonando repositório ==="
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull origin "$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

echo "=== 3. Virtualenv e dependências ==="
$PYTHON -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo "=== 4. .env ==="
if [ ! -f .env ]; then
    cp deploy/.env.production .env
    echo ">>> EDITE .env com: nano $INSTALL_DIR/.env"
    echo "    Preencha DEEPSEEK_API_KEY, ENCRYPT_KEY e ajuste APP_URL"
fi

echo "=== 5. Diretórios de dados ==="
mkdir -p data/sessoes data/output
mkdir -p "$INSTALL_DIR/run"

echo "=== 6. Script de inicialização (start.sh) ==="
cat > start.sh << 'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
nohup uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 > chatbot.log 2>&1 &
echo $! > chatbot.pid
echo "Bot iniciado (PID: $(cat chatbot.pid))"
SCRIPT
chmod +x start.sh

echo "=== 7. Script de parada (stop.sh) ==="
cat > stop.sh << 'SCRIPT'
#!/usr/bin/env bash
if [ -f chatbot.pid ]; then
    kill $(cat chatbot.pid) 2>/dev/null || true
    rm -f chatbot.pid
fi
pkill -f "uvicorn src.main:app" 2>/dev/null || true
echo "Bot parado"
SCRIPT
chmod +x stop.sh

echo "=== 8. Script de inicialização do OpenWA (start_openwa.sh) ==="
cat > start_openwa.sh << 'SCRIPT'
#!/usr/bin/env bash
cd "$HOME/openwa"
export API_MASTER_KEY=PCADV48484848
nohup npm start > openwa.log 2>&1 &
echo $! > openwa.pid
echo "OpenWA iniciado (PID: $(cat openwa.pid))"
SCRIPT
chmod +x start_openwa.sh

echo ""
QR_URL="http://${SERVER_IP}:${PORT_CHATBOT}/webhook/qr"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Instalação concluída!                                   ║"
echo "║                                                          ║"
echo "║  INICIAR:  cd ~/chatbot-puro && ./start.sh               ║"
echo "║  PARAR:    cd ~/chatbot-puro && ./stop.sh                ║"
echo "║  LOGS:     tail -f ~/chatbot-puro/chatbot.log            ║"
echo "║                                                          ║"
echo "║  INICIAR OPENWA:                                         ║"
echo "║    cd ~/openwa && ./start_openwa.sh                      ║"
echo "║                                                          ║"
if [ -n "$SERVER_IP" ]; then
echo "║  QR CODE:  $QR_URL                                       ║"
fi
echo "╚══════════════════════════════════════════════════════════╝"
