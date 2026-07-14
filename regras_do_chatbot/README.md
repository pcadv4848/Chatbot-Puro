# Regras do Chatbot — Atendimento Exclusivo

Este documento detalha como cada regra de atendimento foi implementada no
projeto original, servindo como referência para recriar o comportamento.

---

## Índice de Arquivos

| # | Arquivo | Função Principal |
|---|---|---|
| 1 | `prompts.py` | System prompt da IA — escopo, proibições, fluxo |
| 2 | `text.py` | Normalização de fala popular, gírias, sotaques |
| 3 | `classificar.py` | Dicionário de benefícios + palavras-chave |
| 4 | `supervisor.py` | Orquestrador IA + fallback + máquina de estados |
| 5 | `state.py` | Modelo SessionState — status, histórico, flags |
| 6 | `storage.py` | Persistência de sessão (disco/PostgreSQL) |
| 7 | `router.py` | Webhook + roteamento de mensagens |
| 8 | `whatsapp_openwa.py` | Conexão WhatsApp, envio/recebimento |
| 9 | `admin_commands.py` | Comandos do administrador (/human, /status) |
| 10 | `config.py` | Configurações (.env → pydantic-settings) |

---

## 1. `prompts.py` — System Prompt da IA

**Arquivo original:** `src/conversation/prompts.py`

### Regra: Escopo restrito (só identificar nome + benefício)

```python
SYSTEM_PROMPT = """
Voce e da Advocacia Penido Castro.

SEU TRABALHO (ESCOPO LIMITADO):
1. Perguntar o nome da pessoa
2. Fazer 1 pergunta para entender o caso
3. Usar classificar_beneficio para identificar o beneficio
4. Informar que o atendimento vai continuar com um humano
5. NADA MAIS
"""
```

### Regra: Não mentir / não ocultar informações

```python
VOCE NAO DEVE:
- Dar prazos, valores ou informacoes juridicas
- Fazer perguntas intimas ou sobre medico especifico
- Afirmar algo sem ter 100% de certeza

REGRA:
- NUNCA afirme nada com menos de 100% de certeza
- Se nao tiver certeza, diga que nao sabe e que um humano vai ajudar
```

### Regra: Não falar sobre documentos

```python
VOCE NAO DEVE:
- Coletar dados pessoais (CPF, RG, endereco) — isso e feito automaticamente
- Pedir fotos de documentos ou qualquer arquivo
- Gerar documentos ou contratos
```

### Regra: Conversas curtas (máx 2 frases)

```python
REGRA:
- Seja breve (maximo 2 frases por mensagem)
- Apos classificar, avise que o atendimento tera continuidade com um humano
- NAO continue a conversa apos identificar o beneficio
```

### Regra: Handoff ao classificar

```python
FLUXO:
1. Se apresente como Advocacia Penido Castro e pergunte o nome
2. Pergunte o que a pessoa precisa (1 pergunta apenas)
3. Use classificar_beneficio para identificar
4. Informe que o atendimento vai continuar com um humano
5. ENCERRE — nao continue a conversa
```

### Implementação

O `SYSTEM_PROMPT` é passado como `SystemMessage` para o DeepSeek via LangChain
em `supervisor.py:_processar_ia()`. A IA **não conversa livremente** — ela só
pode responder dentro do escopo definido. As ferramentas disponíveis são
limitadas a `classificar_beneficio` e `extrair_dados_ocr`.

---

## 2. `text.py` — Normalização de Fala Popular

**Arquivo original:** `src/utils/text.py`

### Regra: Entender independente de sotaque/gírias

```python
# Expansão de abreviações e gírias
GIRIAS = {
    "oq": "o que", "pq": "porque", "tb": "também", "vc": "você",
    "q": "que", "td": "tudo", "ngm": "ninguém", "blz": "beleza",
    "obg": "obrigado", "pfv": "por favor", "msg": "mensagem",
    "tlgd": "tá ligado", "sla": "sei lá", "neh": "né",
}

# Recomposição de acentos (escrita sem acento → com acento)
ACENTOS = {
    "doenca": "doença", "incapacidade": "incapacidade", # já ok
    "invalidez": "invalidez", "aposentadoria": "aposentadoria",
    "revisao": "revisão", "pensao": "pensão",
    # ... centenas de mapeamentos
}
```

### Funções principais

```python
def normalizar_texto(texto: str) -> str:
    """Pipeline completo de normalização:
    1. Minúsculas
    2. Expansão de gírias
    3. Recomposição de acentos
    4. Remoção de acentos (para matching)
    5. Remoção de espaços extras
    """
    texto = texto.lower().strip()
    for giria, expansao in GIRIAS.items():
        texto = texto.replace(giria, expansao)
    for sem_acento, com_acento in ACENTOS.items():
        texto = texto.replace(sem_acento, com_acento)
    texto = remover_acentos(texto)  # "doença" → "doenca" (para matching)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def remover_acentos(texto: str) -> str:
    """Remove acentos para correspondência case-insensitive.
    Usa str.translate() com mapeamento de unicode.
    """
    substituicoes = str.maketrans(
        "ÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇáàãâäéèêëíìîïóòõôöúùûüç",
        "AAAAAEEEEIIIIOOOOOUUUUCaaaaaeeeeiiiiooooouuuuc"
    )
    return texto.translate(substituicoes)
```

### Por que isso funciona

O classificador em `classificar.py` usa **correspondência exata por substring**
depois de normalizar AMBOS os lados (texto do usuário + palavras-chave). Isso
significa que "AUXILIO DOENCA", "auxílio doença", "auxilio-doenca" e
"auxílio-doença" **todas viram "auxilio doenca"** e encontram o mesmo match.

---

## 3. `classificar.py` — Classificação de Benefício

**Arquivo original:** `src/agents/tools/classificar.py`

### Regra: Mapear fala do cliente → tipo de benefício

O coração são duas estruturas de dados:

#### BENEFICIOS — Mapeamento benefício → documentos necessários

```python
BENEFICIOS = {
    "incapacidade": {
        "nome": "Benefício por Incapacidade",
        "subtipos": {
            "auxilio_doenca": "Auxílio-Doença",
            "aposentadoria_invalidez": "Aposentadoria por Invalidez",
        },
        # "documentos" ignorado no chatbot-only
    },
    "idade_rural": {
        "nome": "Aposentadoria por Idade Rural",
    },
    "revisao": {
        "nome": "Revisão de Benefício",
    },
    "pensao": {
        "nome": "Pensão por Morte",
    },
    "outro": {
        "nome": "Outro Benefício",
    },
}
```

#### PALAVRAS_CHAVE — Variações da fala popular

```python
PALAVRAS_CHAVE = {
    "incapacidade": {
        "judicial": [
            "auxílio-doença judicial",
            "ação de auxílio-doença",
            "processo de auxílio-doença",
        ],
        "adm": [
            "auxílio-doença", "auxilio doenca", "auxílio doença",
            "incapacidade", "doente", "doença", "licença médica",
            "afastado", "cirurgia", "invalidez", "inválida", "invalida",
            "não consigo trabalhar", "nao consigo trabalhar",
            "coluna", "costa", "hérnia", "hernia",
            "derrame", "artrose", "reumatismo",
            # Gírias rurais / informais:
            "quebrei", "quebrou", "quebrado",
            "acidentado", "acidente", "atropelado",
            "de baixa", "baixa médica", "baixa medica",
            "doenca do trabalho", "insalubre", "insalubridade",
            "perícia", "pericia", "incapaz",
            # Dialeto informal:
            "tô doente", "to doente", "tou doente",
            "não aguento mais", "não dou mais conta",
            "não consigo mais trabalhar",
            "sem carteira", "sem registro", "nunca assinei carteira",
            "tendinite", "bursite",
        ],
    },
    # ... MESMA estrutura para idade_rural, revisao, pensao
}
```

### Algoritmo de classificação

```python
def classificar(texto_cliente: str) -> dict:
    texto = normalizar_texto(texto_cliente).lower()

    # 1. Correspondência exata por substring (confiança 0.85)
    for tipo, esferas in PALAVRAS_CHAVE.items():
        for esfera, palavras in esferas.items():
            for palavra in palavras:
                palavra_norm = remover_acentos(palavra.lower())
                if palavra_norm in texto:
                    return {
                        "tipo": tipo,
                        "esfera": esfera,
                        "confianca": 0.85,
                    }

    # 2. Correspondência difusa com SequenceMatcher (confiança 0.60)
    palavras_user = [w for w in texto.split() if w not in _PALAVRAS_IGNORAR]
    palavras_user = [remover_acentos(w) for w in palavras_user]

    for tipo, esferas in PALAVRAS_CHAVE.items():
        for esfera, palavras in esferas.items():
            for keyword in palavras:
                keyword_tokens = [remover_acentos(w) for w in keyword.split()
                                  if w not in _PALAVRAS_IGNORAR]
                matches = 0
                for kt in keyword_tokens:
                    for ut in palavras_user:
                        if SequenceMatcher(None, kt, ut).ratio() >= 0.70:
                            matches += 1
                            break
                score = matches / len(keyword_tokens) if keyword_tokens else 0
                if score > melhor_score and score >= 0.4:
                    melhor_score, melhor_tipo, melhor_esfera = score, tipo, esfera

    # 3. Fallback (confiança 0.40)
    return {"tipo": "outro", "esfera": "adm", "confianca": 0.4}
```

### Palavras ignoradas no matching difuso

```python
_PALAVRAS_IGNORAR = {
    "de", "da", "do", "das", "dos", "em", "para", "com", "um", "uma",
    "o", "a", "os", "as", "no", "na", "por", "que", "é", "são",
    "meu", "minha", "quero", "queria", "preciso", "gostaria",
    "pode", "como", "vou", "eu", "ele", "ela", "me", "se", "te",
    "nós", "você", "voce", "sim", "não", "nao", "oi", "ola", "olá",
    "tudo", "bem", "mais", "mas", "aqui", "ali", "lá",
    "senhor", "senhora", "filho", "filha",
    "bom", "dia", "tarde", "noite", "então", "entao",
    "só", "so", "já", "ja", "agora", "depois",
    "doutor", "dotô", "doto", "ajuda", "ajude",
}
```

---

## 4. `supervisor.py` — Orquestrador

**Arquivo original:** `src/agents/supervisor.py`

### Regra: Dois modos de operação

#### Modo IA (quando DeepSeek está disponível)

```python
async def _processar_ia(texto: str, sessao: SessionState) -> str:
    """Usa DeepSeek + tools para processar."""
    from src.agents.tools.extrair_ocr import extrair_dados_ocr as extrair_ocr_tool

    @tool
    def classificar_beneficio(texto_cliente: str) -> str:
        """Classifica o tipo de benefício."""
        return json.dumps(classificar(texto_cliente), ensure_ascii=False)

    tools = [classificar_beneficio, extrair_ocr_tool]  # OCR tool ignorada no chatbot-only
    func_map = {t.name: t for t in tools}

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    # Últimas 12 mensagens do histórico
    for msg in sessao.conversa[-12:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content="[resposta anterior]"))

    messages.append(HumanMessage(content=texto))
    response = await _model_with_tools.ainvoke(messages)

    # Processar tool calls
    if response.tool_calls:
        for tc in response.tool_calls:
            resultado = await func_map[tc.name].ainvoke(
                json.loads(tc.function.arguments)
            )
            # Atualizar sessão com resultado
            _aplicar_resultado_tool(tc.name, resultado, sessao)
            # Enviar resultado de volta para IA (segunda chamada)
            messages.append(...)

    return resposta_final
```

#### Modo Fallback (sem IA)

```python
async def _processar_fallback(texto: str, sessao: SessionState) -> str:
    """Máquina de estados sem IA."""
    if sessao.status == SessionStatus.CLASSIFICANDO:
        return await _processar_classificando(texto, sessao)

    elif sessao.status == SessionStatus.AGUARDANDO_ADVOGADO:
        return await _processar_aguardando(texto, sessao)

    elif sessao.status == SessionStatus.COLETANDO_DADOS:
        return await _processar_coleta(texto, sessao)

    else:
        return "Como posso ajudar?"
```

### Regra: Handoff automático ao classificar

```python
async def _processar_classificando(texto: str, sessao: SessionState) -> str:
    from src.agents.tools.classificar import classificar

    resultado = classificar(texto)

    if resultado["confianca"] >= 0.85:
        sessao.tipo_beneficio = resultado["tipo"]
        sessao.esfera = resultado["esfera"]
        sessao.human_attending = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO

        nome_beneficio = BENEFICIOS[resultado["tipo"]]["nome"]
        return (
            f"Seu caso sobre {nome_beneficio} foi identificado. "
            "O atendimento tera continuidade com um advogado."
        )

    elif resultado["confianca"] >= 0.60:
        # Confirmar com o cliente
        nome_beneficio = BENEFICIOS[resultado["tipo"]]["nome"]
        return (
            f"Pelo que entendi, voce precisa de {nome_beneficio}. "
            "É isso mesmo?"
        )

    else:
        return (
            "Me conte um pouco mais sobre o que voce precisa "
            "para eu poder ajudar melhor."
        )
```

### Regra: Timeout com mensagem amigável

```python
try:
    response = await asyncio.wait_for(
        _model_with_tools.ainvoke(messages),
        timeout=30,
    )
except asyncio.TimeoutError:
    return (
        "Desculpe, estou demorando mais que o normal. "
        "Pode tentar novamente? "
    )
```

### Regra: Resposta para quota excedida

```python
if "RESOURCE_EXHAUSTED" in str(e):
    return (
        "Olá! No momento estou com alta demanda e não consegui "
        "processar sua mensagem. Um humano vai atender voce em breve."
    )
```

---

## 5. `state.py` — Modelo SessionState

**Arquivo original:** `src/conversation/state.py`

```python
class SessionStatus(str, Enum):
    NOVO = "novo"                       # Primeira interação
    CLASSIFICANDO = "classificando"      # Identificando benefício
    COLETANDO_DADOS = "coletando_dados"  # Coletando informações
    AGUARDANDO_ADVOGADO = "aguardando_advogado"  # Handoff feito
    REVISAO_ADVOGADO = "revisao_advogado"
    GERANDO = "gerando"
    CONCLUIDO = "concluido"
    PAUSADO = "pausado"
    ARQUIVADO = "arquivado"
    ERRO = "erro"


class SessionState(BaseModel):
    whatsapp_id: str
    status: SessionStatus = SessionStatus.CLASSIFICANDO
    existing_client: bool = False
    human_attending: bool = False
    conversa: list[dict] = []           # Histórono: [{"role", "content"}, ...]
    dados_cliente: dict = {}             # Dados extraídos (nome, etc.)
    tipo_beneficio: Optional[str] = None
    esfera: Optional[str] = None
    ultima_atividade: str = ""           # ISO datetime
    processed_message_ids: list[str] = []
    reminder_count: int = 0
    motivo_pausa: Optional[str] = None
```

### Regra: existing_client vs novo cliente

```python
# Session loaded from disk → existing
# Session created fresh → novo (existing_client = False)
# Regra de ouro: sessão em disco é a única fonte de verdade
```

---

## 6. `storage.py` — Persistência de Sessão

**Arquivo original:** `src/conversation/storage.py`

### Regra: Sessões sobrevivem a restart (com PostgreSQL)

```python
async def carregar_sessao(key: str) -> Optional[SessionState]:
    """Tenta carregar do banco (se configurado), senão do disco."""
    if _db_disponivel:
        return await _carregar_do_banco(key)
    return await _carregar_do_disco(key)

async def salvar_sessao(sessao: SessionState) -> None:
    sessao.ultima_atividade = datetime.now(timezone.utc).isoformat()
    if _db_disponivel:
        return await _salvar_no_banco(sessao)
    return await _salvar_no_disco(sessao)
```

### Estrutura de diretório (modo disco)

```
data/sessions/
├── 557199999999.json
├── 557188888888.json
└── ...
```

### Arquivo individual

```json
{
  "whatsapp_id": "557199999999@c.us",
  "status": "classificando",
  "existing_client": true,
  "conversa": [
    {"role": "user", "content": "Oi, preciso de ajuda"},
    {"role": "assistant", "content": "Qual seu nome?"}
  ],
  "dados_cliente": {"nome": "João"},
  "tipo_beneficio": null,
  "ultima_atividade": "2026-07-13T10:30:00+00:00"
}
```

---

## 7. `router.py` — Webhook + Roteamento

**Arquivo original:** `src/conversation/router.py`

### Regra: Fluxo de processamento de mensagem

```python
async def processar_mensagem_texto(whatsapp_id: str, texto: str, ...):
    sessao = await _obter_ou_criar_sessao(whatsapp_id)

    # 1. Comandos administrativos
    cmd_resposta = await _admin_commands(texto, sessao, ...)
    if cmd_resposta is not None:
        await _salvar_e_enviar(sessao, whatsapp_id, cmd_resposta)
        return

    # 2. Cliente existente → modo silencioso (ignorado no chatbot-only)
    if sessao.existing_client:
        await _processar_humano(texto, sessao)
        return

    # 3. Verificar inatividade (>30 min → pausa automática)
    msg_inatividade = await _verificar_inatividade(sessao, whatsapp_id)
    if msg_inatividade:
        await _salvar_e_enviar(sessao, whatsapp_id, msg_inatividade)
        return

    # 4. Detectar abandono
    if _detectar_abandono(texto):
        # "deixar pra lá", "cancelar", etc.
        sessao.status = SessionStatus.PAUSADO
        resposta = "Sem problemas! Quando quiser retomar, é só me chamar."
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return

    # 5. Sessão pausada → retomar
    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = SessionStatus.CLASSIFICANDO
        resposta = "Bem-vindo de volta! Vamos continuar?"
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return

    # 6. Processar normalmente (IA ou fallback)
    resposta = await processar(texto, sessao)
    # SILENT = não enviar mensagem (modo silencioso)
    await _salvar_e_enviar(sessao, whatsapp_id, resposta)
```

### Regra: Tratamento de imagens (mínimo — sem OCR)

```python
async def processar_mensagem_midia(whatsapp_id: str, midia_id: str):
    sessao = await _obter_ou_criar_sessao(whatsapp_id)

    if sessao.status in (SessionStatus.COLETANDO_DADOS, SessionStatus.CLASSIFICANDO):
        # No chatbot-only, apenas confirma recebimento
        resposta = "Recebi sua imagem! Pode continuar me enviando informações por texto."
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return
```

### Regra: Criação de sessão (sem verificação OpenWA)

```python
async def _obter_ou_criar_sessao(whatsapp_id: str) -> SessionState:
    key = whatsapp_id.split("@")[0] if "@" in whatsapp_id else whatsapp_id

    # Cache em memória
    if key in sessoes_ativas:
        return sessoes_ativas[key]

    # Disco / banco
    sessao = await carregar_sessao(key)
    if sessao is None:
        sessao = SessionState(whatsapp_id=whatsapp_id)
        # Novo cliente → existing_client = False (padrão)
    else:
        sessao.existing_client = True  # Tem sessão salva = cliente existente

    sessoes_ativas[key] = sessao
    return sessao
```

---

## 8. `whatsapp_openwa.py` — Conexão WhatsApp

**Arquivo original:** `src/services/whatsapp_openwa.py`

### Regra: Envio de mensagem com retry

```python
async def enviar_mensagem(whatsapp_id: str, texto: str) -> bool:
    """Envia mensagem via OpenWA com retry automático.

    Args:
        whatsapp_id: JID do destinatário (ex: 557199999999@c.us)
        texto: Texto da mensagem (máx 4096 caracteres)

    Returns:
        True se enviado com sucesso, False se falhou após retries.
    """
    for tentativa in range(3):
        try:
            jid = _formatar_jid(whatsapp_id)
            resp = await client.post(
                f"{base}/sessions/{session_id}/send-text",
                headers=headers,
                json={"jid": jid, "message": texto},
                timeout=10,
            )
            if resp.status_code < 400:
                return True
        except Exception as e:
            logger.warning("Tentativa %d/3 falhou: %s", tentativa + 1, e)
            await asyncio.sleep(1 * (tentativa + 1))  # backoff
    return False
```

### Regra: Heartbeat e auto-reconnect

```python
async def verificar_e_reconectar() -> dict:
    """Heartbeat a cada 60s. Se sessão 'failed', reconecta."""
    status = await obter_status_sessao()
    if status.get("status") in ("failed", "disconnected"):
        await deletar_sessao()
        await criar_sessao()
        await iniciar_sessao()
        await configurar_webhook()
        return {"status": "reconectado"}
    return {"status": "ok"}
```

### Regra: QR code para nova conexão

```python
async def obter_qr() -> dict:
    """Retorna QR code PNG em base64 para escaneamento."""
    resp = await client.get(f"{base}/sessions/{session_id}/qr", ...)
    if resp.status_code == 200:
        return {"status": "pending", "qr_base64": resp.text}
    return {"status": "error"}
```

---

## 9. `admin_commands.py` — Comandos do Administrador

**Arquivo original:** `src/conversation/admin_commands.py`

### Regra: Intervenção humana no atendimento

```python
# Palavras que ATIVAM modo humano
ADMIN_ALIASES = {
    "/human": "human",
    "/humano": "human",
    "/advogado": "human",
    "/sair": "human",       # Sair de admin
    "/status": "status",
    "/historico": "historico",
    "/dados": "dados",
    "/pausar": "pausar",
    "/retomar": "retomar",
    "/concluir": "concluir",
    "/arquivar": "arquivar",
    "/admin": "admin",       # Entrar em modo admin
    "/help": "help",
    "/ajuda": "help",
    "/mensagem": "mensagem", # Enviar msg como bot
    "/reenviar": "reenviar",
}
```

### Implementação do comando /human

```python
async def _cmd_human(texto: str, sessao: SessionState, admin_id: str) -> Optional[str]:
    """Ativa atendimento humano na sessão."""
    sessao.human_attending = True
    sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
    # Todas as mensagens seguintes do cliente vão para o admin
    return (
        f"Modo atendimento humano ativado para {sessao.whatsapp_id}.\n"
        "Todas as mensagens desse cliente serao encaminhadas para voce."
    )
```

### Fluxo de comando

```python
async def processar_admin_commands(
    texto: str,
    sessao: SessionState,
    admin_cmd: bool = False,
) -> Optional[str]:
    """Processa comandos administrativos. Retorna resposta ou None se não for comando."""
    texto_lower = texto.strip().lower()

    comando = ADMIN_ALIASES.get(texto_lower)
    if comando is None:
        return None  # Não é comando

    if comando == "human":
        return await _cmd_human(texto, sessao, admin_id)
    elif comando == "status":
        return await _cmd_status(sessao)
    elif comando == "historico":
        return await _cmd_historico(sessao)
    # ...
```

---

## 10. `config.py` — Configurações

**Arquivo original:** `src/config.py`

### Regra: Configuração via .env com pydantic-settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "ChatBot Previdenciário"
    debug: bool = False

    # Provedor WhatsApp
    whatsapp_provider: str = "openwa"  # "openwa" | "meta"
    whatsapp_token: str = ""           # Meta Cloud API
    openwa_api_url: str = "http://openwa:2785/api"
    openwa_api_key: str = ""
    openwa_session_id: str = "chatbot-prev"

    # DeepSeek (IA)
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # Admin
    admin_username: str = "admin"
    admin_password: str = ""
    admin_whatsapp: str = ""

    # Limites
    max_ocr_retries: int = 3          # Ignorado no chatbot-only
    session_timeout_minutes: int = 30  # Pausa automática por inatividade
    session_archive_days: int = 30     # Arquivamento automático

    # Lembretes
    reminder_cooldown_days: int = 3
    reminder_max_count: int = 2

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()
```

---

## Resumo das Regras e Onde Estão Implementadas

| Regra | Implementada em |
|---|---|
| Escopo limitado (só nome + benefício) | `prompts.py` (SYSTEM_PROMPT) |
| Máx 2 frases por mensagem | `prompts.py` (SYSTEM_PROMPT) |
| Handoff após classificar | `supervisor.py:_processar_classificando()` |
| Não mentir / não afirmar sem certeza | `prompts.py` (SYSTEM_PROMPT) |
| Não coletar CPF/RG/endereço | `prompts.py` (SYSTEM_PROMPT) |
| Não pedir fotos de documentos | `prompts.py` (SYSTEM_PROMPT) |
| Entender gírias/sotaques | `text.py` (normalizar_texto + remover_acentos) |
| Classificar fala popular | `classificar.py` (PALAVRAS_CHAVE + classificar()) |
| Pausa automática por inatividade | `router.py:_verificar_inatividade()` |
| Detectar abandono | `router.py:_detectar_abandono()` |
| Retomar sessão pausada | `router.py:processar_mensagem_texto()` |
| Comandos admin (/human, /status) | `admin_commands.py` |
| Persistência entre restarts | `storage.py` (disco + PostgreSQL) |
| Cliente existente vs novo | `state.py` (existing_client) + `router.py` |
| Heartbeat / auto-reconnect | `whatsapp_openwa.py:verificar_e_reconectar()` |
| Rate limit de webhook | `config.py` (rate_limit_webhook) |
| Timeout de IA com mensagem amigável | `supervisor.py:_processar_ia()` |
| Resposta para quota excedida | `supervisor.py:_processar_ia()` |

---

## Dependências Mínimas (requirements.txt)

```txt
# Core
pydantic>=2.10.0
pydantic-settings>=2.6.0

# Web
fastapi>=0.115.0
uvicorn[standard]>=0.34.0

# IA
openai>=1.55.0             # DeepSeek (compatível OpenAI)

# WhatsApp
httpx>=0.28.0

# DB (opcional para produção)
# asyncpg>=0.30.0
# sqlalchemy[asyncio]>=2.0.0
```

---

## Modelo de Dados Simplificado (para chatbot-only)

```python
# state.py — mínimo necessário
from enum import Enum
from pydantic import BaseModel
from typing import Optional

class SessionStatus(str, Enum):
    CLASSIFICANDO = "classificando"
    AGUARDANDO_ADVOGADO = "aguardando_advogado"
    PAUSADO = "pausado"
    CONCLUIDO = "concluido"
    ARQUIVADO = "arquivado"

class SessionState(BaseModel):
    whatsapp_id: str
    status: SessionStatus = SessionStatus.CLASSIFICANDO
    existing_client: bool = False
    human_attending: bool = False
    conversa: list[dict] = []
    dados_cliente: dict = {}
    tipo_beneficio: Optional[str] = None
    esfera: Optional[str] = None
    ultima_atividade: str = ""
```
