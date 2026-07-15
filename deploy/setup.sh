#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Setup do ChatBot Puro ==="

echo "=== 1. Criando diretorios de dados ==="
mkdir -p data/sessoes data/output
echo "  data/sessoes/  (sessoes JSON fallback)"
echo "  data/output/   (documentos gerados)"

echo "=== 2. Gerando ENCRYPT_KEY ==="
if [ ! -f .env ]; then
    cp deploy/.env.production .env
    ENCRYPT_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/ENCRYPT_KEY=/ENCRYPT_KEY=$ENCRYPT_KEY/" .env
    else
        sed -i "s/ENCRYPT_KEY=/ENCRYPT_KEY=$ENCRYPT_KEY/" .env
    fi
    echo "  .env criado a partir de deploy/.env.production"
    echo "  ENCRYPT_KEY gerada automaticamente"
    echo ""
    echo "  >>> EDITE .env agora com suas credenciais: <<<"
    echo "      DEEPSEEK_API_KEY, OPENWA_API_KEY, APP_URL, ADMIN_WHATSAPP"
else
    echo "  .env ja existe — pulando geracao"
fi

echo ""
echo "=== 3. Verificando virtualenv ==="
if [ ! -d .venv ]; then
    echo "  Criando .venv..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip setuptools wheel -q
    pip install -r requirements.txt -q
    echo "  Virtualenv criada e dependencias instaladas"
else
    echo "  .venv ja existe"
fi

echo ""
echo "=== 4. Permissoes dos scripts ==="
chmod +x deploy/*.sh 2>/dev/null || true

echo ""
echo "=== Setup concluido! ==="
echo "  Para iniciar: bash deploy/start.sh"
echo "  Para parar:   bash deploy/stop.sh"
echo "  Status:       bash deploy/status.sh"
echo ""
echo "  Nao esqueca de editar .env com suas credenciais!"
