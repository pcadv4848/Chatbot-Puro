#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ChatBot Puro — Instalação SEM sudo (usuário comum)
#  Uso: bash install_nosudo.sh
# ═══════════════════════════════════════════════════════════

REPO_URL="https://github.com/pcadv4848/Chatbot-Puro.git"
INSTALL_DIR="$HOME/chatbot-puro"
OPENWA_DIR="$HOME/openwa"

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
    git pull origin master
else
    git clone --branch master "$REPO_URL" "$INSTALL_DIR"
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
fi

echo "=== 5. Diretórios de dados ==="
mkdir -p data/sessoes data/output
mkdir -p "$INSTALL_DIR/run"

echo "=== 6. Script de inicialização (start.sh) ==="
cat > start.sh << 'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${UVICORN_WORKERS:-1}"
SCRIPT
chmod +x start.sh

echo "=== 7. Script de parada (stop.sh) ==="
cat > stop.sh << 'SCRIPT'
#!/usr/bin/env bash
pkill -f "uvicorn src.main:app" 2>/dev/null && echo "Parou" || echo "Nao estava rodando"
SCRIPT
chmod +x stop.sh

echo "=== 8. Script de inicialização do OpenWA (start_openwa.sh) ==="
cat > start_openwa.sh << 'SCRIPT'
#!/usr/bin/env bash
cd "$HOME/openwa"

# Se o banco ainda não existe (primeira execução), API_MASTER_KEY define a chave
if [ ! -f "$HOME/openwa/data/openwa-main.sqlite" ]; then
  export API_MASTER_KEY=PCADV48484848
fi

exec npm start
SCRIPT
chmod +x start_openwa.sh

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Instalação concluída!                                   ║"
echo "║                                                          ║"
echo "║  INICIAR O BOT:                                          ║"
echo "║    screen -S chatbot                                     ║"
echo "║    cd ~/chatbot-puro                                     ║"
echo "║    ./start.sh                                            ║"
echo "║    (Ctrl+A, D para desanexar)                            ║"
echo "║                                                          ║"
echo "║  VER LOGS:                                               ║"
echo "║    screen -r chatbot                                     ║"
echo "║                                                          ║"
echo "║  INICIAR OPENWA (fork):                                  ║"
echo "║    screen -S openwa                                      ║"
echo "║    cd ~/openwa                                           ║"
echo "║    npm start                                             ║"
echo "║                                                          ║"
echo "║  PARAR O BOT:                                            ║"
echo "║    cd ~/chatbot-puro && ./stop.sh                        ║"
echo "║                                                          ║"
echo "║  QR CODE:                                                ║"
echo "║    http://187.77.235.249:8000/webhook/qr                 ║"
echo "╚══════════════════════════════════════════════════════════╝"
