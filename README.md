# ChatBot Puro

Chatbot juridico com inteligencia artificial para atendimento via WhatsApp. Utiliza DeepSeek (primario) e Verboo (fallback) como provedores de LLM, e OpenWA como gateway para enviar/receber mensagens do WhatsApp.

---

## Sumario

- [Arquitetura](#arquitetura)
- [Requisitos de Hardware](#requisitos-de-hardware)
- [Primeiro Setup (VPS Linux)](#primeiro-setup-vps-linux)
- [Comandos Diarios](#comandos-diarios)
- [Scripts de Deploy](#scripts-de-deploy)
- [Atualizacao do Projeto (git pull)](#atualizacao-do-projeto-git-pull)
- [Variaveis de Ambiente](#variaveis-de-ambiente)
- [Estrutura do Projeto](#estrutura-do-projeto)

---

## Arquitetura

```
[WhatsApp] <---> [OpenWA (porta 2785)] <---> [ChatBot (porta 8000)]
                                                |
                                          [SQLite (data/chatbot.db)]
                                                |
                                          [DeepSeek API (LLM)]
```

- **OpenWA**: Gateway WhatsApp-web.js. Escuta webhooks do WhatsApp e encaminha para o ChatBot.
- **ChatBot**: FastAPI com 1 worker uvicorn. Processa mensagens com IA, gerencia sessoes.
- **SQLite**: Banco local (unico worker obrigatorio). Fallback para JSON em data/sessoes/.
- **DeepSeek**: Provedor LLM principal. Verboo como fallback.

---

## Requisitos de Hardware

| Componente | Minimo | Recomendado |
|------------|--------|-------------|
| RAM | 2 GB | 4 GB |
| CPU | 1 core | 2 cores |
| Disco | 5 GB | 10 GB |
| SO | Ubuntu 22.04+ / Debian 11+ | - |

> **NOTA**: OpenWA (Chromium) consome 800MB-1.5GB de RAM. Em VPS de 4GB, nao execute outros processos pesados no mesmo servidor.

> **WARNING**: Whisper (transcricao de audio) requer ~1GB RAM adicional e NAO esta disponivel neste setup. A transcricao de audio esta desabilitada por padrao.

---

## Primeiro Setup (VPS Linux)

### 1. Acessar o servidor

```bash
ssh usuario@SEU_IP
```

### 2. Instalar dependencias do sistema

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev \
    git tesseract-ocr tesseract-ocr-por \
    curl gnupg ca-certificates
```

### 3. Clonar o repositorio

```bash
git clone https://github.com/pcadv4848/Chatbot-Puro.git ~/chatbot-puro
cd ~/chatbot-puro
```

### 4. Executar setup automatico

```bash
bash deploy/setup.sh
```

Este script:
- Cria diretorios `data/sessoes/` e `data/output/`
- Gera uma `ENCRYPT_KEY` aleatoria de 32 bytes
- Cria `.env` a partir de `deploy/.env.production`
- Cria virtualenv Python e instala dependencias

> **WARNING**: Apos o setup, EDITE o arquivo `.env` com suas credenciais reais:
> `nano ~/chatbot-puro/.env`

### 5. Configurar variaveis essenciais no .env

```ini
DEEPSEEK_API_KEY=sk-sua-chave-aqui
OPENWA_API_KEY=PCADV48484848
APP_URL=http://SEU_IP_AQUI
ADMIN_WHATSAPP=5571912345678
```

### 6. Instalar e configurar OpenWA

> NOTA: OpenWA e um fork do whatsapp-web.js. Veja o repositorio oficial para instrucoes de instalacao.

```bash
# Exemplo (depende do fork usado):
git clone <fork-do-openwa> ~/openwa
cd ~/openwa
npm install
cp .env.example .env
# Edite .env: API_MASTER_KEY, PORT=2785
```

### 7. Iniciar OpenWA

```bash
cd ~/openwa
nohup npm start > openwa.log 2>&1 &
echo $! > openwa.pid
```

### 8. Iniciar ChatBot

```bash
cd ~/chatbot-puro
bash deploy/start.sh
```

### 9. Escanear QR Code

Abra no navegador: `http://SEU_IP:8000/webhook/qr` e escaneie com o WhatsApp.

> **NOTE**: Se a pagina mostrar "Aguardando QR Code", aguarde alguns segundos e recarregue. O OpenWA pode levar ate 30s para gerar o QR.

---

## Comandos Diarios

### Gerenciamento do ChatBot

```bash
# Iniciar
cd ~/chatbot-puro && bash deploy/start.sh

# Parar
cd ~/chatbot-puro && bash deploy/stop.sh

# Reiniciar
cd ~/chatbot-puro && bash deploy/restart.sh

# Ver status (PID, RAM, portas, health check)
cd ~/chatbot-puro && bash deploy/status.sh
```

### Logs

```bash
# Log do chatbot
tail -f ~/chatbot-puro/chatbot.log

# Ultimas 50 linhas
tail -50 ~/chatbot-puro/chatbot.log

# Log do OpenWA
tail -f ~/openwa/openwa.log

# Log com filtro (erros)
grep -i error ~/chatbot-puro/chatbot.log
```

### Gerenciamento de Processos

```bash
# Verificar se o chatbot esta rodando
pgrep -f "uvicorn src.main:app"

# Verificar se o OpenWA esta rodando
pgrep -f "openwa"

# Matar processo manualmente (se deploy/stop.sh nao funcionar)
pkill -f "uvicorn src.main:app"

# Matar por PID especifico
kill -9 12345

# Ver processos com consumo de recursos
ps aux --sort=-%mem | head -10
```

### Monitoramento de Memoria (VPS 4GB)

```bash
# Monitoramento continuo (atualiza a cada 30s)
watch -n 30 bash ~/chatbot-puro/deploy/memory.sh

# Comando rapido
free -h
```

### Health Check

```bash
# Verificar se o chatbot esta saudavel
curl http://127.0.0.1:8000/health

# Resposta esperada:
# {"status":"ok","app":"ChatBot Puro","openwa":"ready"}
```

### Forcar reconexao do WhatsApp

Acesse no navegador:
```
http://SEU_IP:8000/webhook/qr/desconectar?token=SUA_ADMIN_PASSWORD
```

---

## Scripts de Deploy

Todos os scripts estao em `deploy/` e devem ser executados da raiz do projeto.

| Script | Descricao |
|--------|-----------|
| `deploy/setup.sh` | **Primeira instalacao.** Cria diretorios, gera ENCRYPT_KEY, cria .env, instala dependencias Python. Executar UMA vez apos o clone. |
| `deploy/start.sh` | **Inicia o chatbot.** Aplica `ulimit` de memoria (padrao 2500MB), inicia uvicorn com nohup, salva PID em `chatbot.pid`. |
| `deploy/stop.sh` | **Para o chatbot.** Envia SIGTERM para o PID salvo, aguarda 2s, envia SIGKILL se necessario, faz `pkill` de fallback. |
| `deploy/restart.sh` | **Reinicia o chatbot.** Executa `stop.sh` + `start.sh`. |
| `deploy/status.sh` | **Diagnostico.** Mostra PID, memoria/CPU, portas escutando, memoria do sistema, e resultado do `/health`. |
| `deploy/memory.sh` | **Monitoramento RAM.** Mostra uso de memoria do sistema, top processos por RAM, e consumo do Chromium. |

### Explicacao Detalhada

**deploy/start.sh:**
- Verifica se `.env` existe (aborta se nao)
- Ativa a virtualenv Python
- Aplica `ulimit -v` para limitar memoria virtual a 2500MB (previne OOM)
- Inicia uvicorn com `nohup` em background, 1 worker obrigatorio
- Salva PID em `chatbot.pid`

> **WARNING**: `ulimit` pode nao funcionar em alguns ambientes (containers, some VPS). O script ignora falha do ulimit e continua.

**deploy/stop.sh:**
- Le o PID de `chatbot.pid`
- Verifica se o processo existe com `kill -0`
- Envia SIGTERM, aguarda 2s
- Se processo ainda vivo, envia SIGKILL
- Remove `chatbot.pid`
- Fallback: `pkill -f "uvicorn src.main:app"`

**deploy/status.sh:**
- Verifica `chatbot.pid` e se o processo esta rodando
- Mostra tempo de execucao (etime), %mem, %cpu, RSS
- Verifica se OpenWA esta rodando
- Verifica portas 8000 (chatbot) e 2785 (OpenWA) com `ss`
- Mostra `free -h` para memoria do sistema
- Executa `curl /health` para health check completo

**deploy/memory.sh:**
- Projetado para ser usado com `watch -n 30`
- Mostra `free -h` (memoria total/usada/disponivel)
- Top 8 processos por consumo de RAM
- Consumo especifico do processo do chatbot
- Consumo especifico de processos Chrome/Chromium (OpenWA)

---

## Atualizacao do Projeto (git pull)

### Atualizacao rapida via SSH

```bash
# Conecte e execute:
cd ~/chatbot-puro
git pull origin master
source .venv/bin/activate
pip install -r requirements.txt -q
bash deploy/restart.sh
```

### Atualizacao com deploy.sh (automatizado)

```bash
ssh usuario@SEU_IP 'SERVER_IP=SEU_IP bash -s' < deploy.sh
```

O script `deploy.sh` faz automaticamente:
1. `git pull`
2. `pip install -r requirements.txt`
3. Cria tabelas do banco se necessario
4. Para o bot antigo (via `deploy/stop.sh`)
5. Inicia o bot novo (via `deploy/start.sh`)
6. Verifica se o bot subiu

### Verificar o que mudou antes de atualizar

```bash
cd ~/chatbot-puro
git fetch origin
git log --oneline master..origin/master
```

### Reverter para versao anterior

```bash
cd ~/chatbot-puro
git log --oneline -10
git checkout HASH_DO_COMMIT
bash deploy/restart.sh

# Para voltar ao ultimo commit:
git checkout master
```

---

## Variaveis de Ambiente

Variaveis principais no `.env`:

| Variavel | Obrigatoria | Descricao |
|----------|-------------|-----------|
| `DEEPSEEK_API_KEY` | Sim | Chave da API DeepSeek |
| `OPENWA_API_KEY` | Sim | Chave de autenticacao do OpenWA |
| `APP_URL` | Sim | URL publica do bot (ex: http://187.77.235.249) |
| `ADMIN_WHATSAPP` | Sim | Numero do administrador (comandos BOT, HUMANO, STATUS) |
| `ENCRYPT_KEY` | Sim | Chave AES-256 (32 bytes hex). Gere com `bash deploy/setup.sh` |
| `OPENWA_API_URL` | Sim | URL do OpenWA (padrao: http://127.0.0.1:2785/api) |
| `DATABASE_URL` | Nao | URL do banco (padrao: sqlite para single-server) |
| `PORT` | Nao | Porta do chatbot (padrao: 8000) |

> **WARNING**: `UVICORN_WORKERS` deve ser **1** (padrao) quando usando SQLite. Multiplos workers corrompem o banco SQLite.

> **NOTE**: Para usar PostgreSQL, mude `DATABASE_URL` para `postgresql+asyncpg://...` e adicione `asyncpg` e `psycopg2-binary` ao `requirements.txt`.

---

## Estrutura do Projeto

```
chatbot-puro/
├── .env.example          # Exemplo de variaveis de ambiente
├── .gitignore
├── Dockerfile             # Build Docker (nao usado em VPS sem sudo)
├── README.md
├── deploy.sh              # Deploy rapido via SSH pipe
├── requirements.txt       # Dependencias Python
├── data/                  # Dados gerados em runtime (gitignored)
│   ├── sessoes/           # Fallback JSON das sessoes
│   ├── output/            # Documentos gerados
│   └── chatbot.db         # SQLite database
├── deploy/                # Scripts de deploy e configuracao
│   ├── setup.sh           # Setup inicial (env, diretorios, venv)
│   ├── start.sh           # Iniciar chatbot
│   ├── stop.sh            # Parar chatbot
│   ├── restart.sh         # Reiniciar chatbot
│   ├── status.sh          # Diagnostico completo
│   ├── memory.sh          # Monitoramento RAM
│   ├── .env.production    # Template .env para producao
│   ├── install_nosudo.sh  # Instalacao completa (clone + setup)
│   ├── nginx/             # Configuracao Nginx (requer sudo)
│   │   └── chatbot-puro.conf
│   └── systemd/           # Systemd service (requer sudo)
│       └── chatbot-puro.service
└── src/
    ├── main.py            # Entrypoint FastAPI
    ├── config.py          # Config (pydantic-settings)
    ├── agents/            # Logica do agente IA
    │   ├── supervisor.py  # Orquestrador principal
    │   ├── constants.py   # Constantes e prompts
    │   ├── extraction.py  # Extracao de dados
    │   ├── text_utils.py  # Utilitarios de texto
    │   └── tools/         # Ferramentas (classificar, validar, OCR)
    ├── conversation/      # Gerenciamento de conversas
    │   ├── router.py      # Webhook endpoint
    │   ├── state.py       # SessionState dataclass
    │   ├── storage.py     # Persistencia (DB + JSON fallback)
    │   ├── chat_local.py  # Interface web de teste
    │   ├── prompts.py     # System prompts
    │   ├── admin_commands.py # Comandos administrativos
    │   └── jid_utils.py   # Utilitarios JID
    ├── db/                # Banco de dados
    │   ├── session.py     # Engine SQLAlchemy (SQLite/PostgreSQL)
    │   └── models.py      # Modelos ORM
    ├── engine/            # Motor interno
    │   ├── crypto.py      # Criptografia Fernet
    │   ├── retry.py       # Retry com exponential backoff
    │   ├── rate_limit.py  # Rate limiting (slowapi)
    │   ├── logging_filter.py # Filtro de dados sensiveis em logs
    │   ├── idempotency.py # Cache de idempotencia
    │   └── reminder.py    # Lembretes automaticos
    └── services/          # Servicos externos
        ├── whatsapp.py          # Dispatcher (Meta / OpenWA)
        ├── whatsapp_openwa.py   # Integracao OpenWA
        ├── whatsapp_meta.py     # Integracao Meta (futuro)
        ├── attended_clients.py  # Clientes ja atendidos
        ├── transcricao.py       # Transcricao de audio (desabilitado)
        └── signing.py           # Verificacao de webhook Meta
```

---

## Troubleshooting

### OpenWA nao inicia (porta ocupada)

```bash
# Verificar o que esta usando a porta
ss -tlnp | grep 2785

# Se for processo root (sem sudo), mude a porta do OpenWA:
# No ~/openwa/.env: OPENWA_PORT=2786
# No ~/chatbot-puro/.env: OPENWA_API_URL=http://127.0.0.1:2786/api
```

### Chatbot nao inicia

```bash
# Verificar logs
cat ~/chatbot-puro/chatbot.log

# Verificar se porta 8000 esta ocupada
ss -tlnp | grep 8000

# Verificar .env
cat ~/chatbot-puro/.env | grep -v "KEY\|SECRET\|PASSWORD"
```

### Webhook nao recebe mensagens

```bash
# Verificar health check
curl http://127.0.0.1:8000/health

# Verificar se o webhook esta registrado
curl http://127.0.0.1:8000/webhook/diag

# Verificar log do OpenWA
tail -50 ~/openwa/openwa.log
```

### Mensagens nao sao enviadas (send-text 500)

```bash
# Verificar se API_MASTER_KEY do OpenWA bate com OPENWA_API_KEY do .env
grep API_KEY ~/chatbot-puro/.env
grep API_MASTER_KEY ~/openwa/.env

# Verificar UUID da sessao
curl http://127.0.0.1:8000/webhook/diag | python3 -m json.tool
```

---

## Seguranca

- Dados sensiveis (CPF, RG, endereco) sao criptografados com AES-256 (Fernet) usando `ENCRYPT_KEY`
- Chaves de API sao redacionadas em logs pelo `DadosSensiveisFilter`
- Webhooks do OpenWA sao validados por HMAC-SHA256
- O arquivo `.env` contem todas as credenciais e NAO deve ser versionado (esta no `.gitignore`)
- A chave `ENCRYPT_KEY` deve ser gerada com `python -c "import secrets; print(secrets.token_hex(32))"` ou automaticamente pelo `deploy/setup.sh`

> **WARNING**: Se `ENCRYPT_KEY` for alterada apos ja haver dados criptografados, os dados existentes serao ilegiveis.
