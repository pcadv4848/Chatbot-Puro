#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
#  ChatBot Puro — Instalação em Servidor Próprio (Ubuntu 22.04+)
#  Uso: ssh penido-castro-bot@187.77.235.249 'bash -s' < install.sh
# ═══════════════════════════════════════════════════════════

REPO_URL="https://github.com/pcadv4848/Chatbot-Puro.git"
BRANCH="master"
INSTALL_DIR="$HOME/chatbot-puro"
OPENWA_DIR="$HOME/openwa"

echo "=== 1. Instalando dependências do sistema ==="
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3.13 python3.13-venv python3.13-dev \
    git nginx certbot python3-certbot-nginx \
    tesseract-ocr tesseract-ocr-por \
    curl gnupg \
    ca-certificates

echo "=== 2. Clonando repositório ==="
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull origin "$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

echo "=== 3. Criando virtualenv e instalando dependências Python ==="
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo "=== 4. Configurando .env ==="
if [ ! -f .env ]; then
    cp deploy/.env.production .env
    echo ">>> EDITE .env com suas credenciais (DeepSeek, OpenWA, etc.) <<<"
fi

echo "=== 5. Criando diretórios de dados ==="
mkdir -p data/sessoes data/output

echo "=== 6. Instalando systemd service ==="
sudo cp deploy/systemd/chatbot-puro.service /etc/systemd/system/chatbot-puro.service
sudo systemctl daemon-reload
sudo systemctl enable chatbot-puro

echo "=== 7. Instalando nginx ==="
sudo cp deploy/nginx/chatbot-puro.conf /etc/nginx/sites-available/chatbot-puro
if [ ! -f /etc/nginx/sites-enabled/chatbot-puro ]; then
    sudo ln -s /etc/nginx/sites-available/chatbot-puro /etc/nginx/sites-enabled/
fi
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Instalação concluída!                                   ║"
echo "║                                                          ║"
echo "║  PRÓXIMOS PASSOS:                                        ║"
echo "║  1. Edite .env com suas credenciais:                     ║"
echo "║     nano $INSTALL_DIR/.env                              ║"
echo "║                                                          ║"
echo "║  2. Inicie o chatbot:                                    ║"
echo "║     sudo systemctl start chatbot-puro                    ║"
echo "║                                                          ║"
echo "║  3. Veja os logs:                                        ║"
echo "║     journalctl -u chatbot-puro -f                        ║"
echo "║                                                          ║"
echo "║  4. (Opcional) SSL com Let's Encrypt:                    ║"
echo "║     sudo certbot --nginx -d bot.seudominio.com.br        ║"
echo "║                                                          ║"
echo "║  5. Configure e inicie o OpenWA (fork separado)          ║"
echo "╚══════════════════════════════════════════════════════════╝"
