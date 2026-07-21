#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ChatBot Puro — Instalação SEM sudo (usuário comum)
#  Uso: bash deploy/install_nosudo.sh
#  Variáveis de ambiente:
#    SERVER_IP=187.77.235.249  (para links de retorno no final)
#    PORT=8000                 (porta do chatbot)
# ═══════════════════════════════════════════════════════════

REPO_URL="${REPO_URL:-https://github.com/pcadv4848/Chatbot-Puro.git}"
BRANCH="${BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/chatbot/chatbot-puro}"
SERVER_IP="${SERVER_IP:-}"
PORT_CHATBOT="${PORT:-8000}"

echo "=== 1. Verificando ferramentas disponíveis ==="
MISSING=""
for cmd in python3 git node npm; do
    if command -v "$cmd" &>/dev/null; then
        echo "  ✓ $cmd: $(command -v "$cmd")"
    else
        echo "  ✗ $cmd NÃO encontrado"
        MISSING="$MISSING $cmd"
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
pip install --upgrade pip setuptools wheel -q
pip install -r requirements.txt -q
echo "  Dependências instaladas"

echo "=== 4. Setup (.env + diretórios + chave) ==="
bash deploy/setup.sh

echo "=== 5. Permissões ==="
chmod +x deploy/*.sh

echo ""
QR_URL="http://${SERVER_IP}:${PORT_CHATBOT}/webhook/qr"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Instalação concluída!                                   ║"
echo "║                                                          ║"
echo "║  INICIAR:  cd ~/chatbot-puro && bash deploy/start.sh     ║"
echo "║  PARAR:    cd ~/chatbot-puro && bash deploy/stop.sh      ║"
echo "║  STATUS:   cd ~/chatbot-puro && bash deploy/status.sh    ║"
echo "║  LOGS:     tail -f ~/chatbot-puro/chatbot.log            ║"
echo "║                                                          ║"
echo "║  EDITAR .env: nano ~/chatbot-puro/.env                   ║"
echo "║                                                          ║"
if [ -n "$SERVER_IP" ]; then
echo "║  QR CODE:  $QR_URL                                       ║"
fi
echo "╚══════════════════════════════════════════════════════════╝"
