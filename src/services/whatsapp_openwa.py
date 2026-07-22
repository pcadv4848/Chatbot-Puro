"""Serviço de integração com OpenWA (self-hosted WhatsApp API Gateway).

Funções assíncronas para envio/recebimento de mensagens e mídia via OpenWA REST API.
OpenWA usa whatsapp-web.js internamente — requer QR code scan para conectar.

Uso direto (raro — prefira o dispatcher em whatsapp.py):
    from src.services.whatsapp_openwa import enviar_mensagem
"""
import asyncio
import json
import logging
import random
import time
import httpx
from src.config import settings
from src.engine.retry import async_retry
from src.services.antiban import enviar_com_seguranca, finalizar_conversa

logger = logging.getLogger(__name__)

_ultimo_envio_global: float = 0.0
_envio_lock: asyncio.Lock = asyncio.Lock()
INTERVALO_ENVIO_SEGUNDOS = 1


def _deve_retentar(e: Exception) -> bool:
    """Só retenta em erros de rede (RequestError) ou servidor 5xx."""
    if isinstance(e, httpx.RequestError):
        return True
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500
    return False

_client: httpx.AsyncClient | None = None
_session_id_override: str | None = None
_ultimo_erro_envio: dict | None = None
_ultimos_envios: dict[str, float] = {}
"""Registro de quando o bot enviou mensagem para cada JID (raw number, sem @).
Usado pelo webhook para distinguir eco do bot vs mensagem do humano."""


def _get_headers() -> dict:
    """Retorna headers com API key do OpenWA."""
    return {
        "X-API-Key": settings.openwa_api_key,
        "Content-Type": "application/json",
    }


def _get_base_url() -> str:
    """Retorna a base URL da API OpenWA (lida do settings a cada chamada)."""
    return settings.openwa_api_url


def _get_session_id() -> str:
    """Retorna o ID da sessão OpenWA.

    Usa o UUID real retornado pela API (se disponível, via _session_id_override),
    ou o valor da config/settings como fallback.
    Isso resolve o problema de a API OpenWA não resolver nomes de sessão.
    """
    return _session_id_override or settings.openwa_session_id


async def _get_session_id_garantido() -> str:
    """Retorna o session_id, resolvendo UUID primeiro se necessário.

    Garante que o UUID real seja usado em vez do nome da sessão,
    evitando erros 404/500 ao chamar a API OpenWA com um nome não resolvido.
    Tenta resolver até 3x se _session_id_override ainda estiver None.
    """
    for tentativa in range(3):
        if _session_id_override is not None:
            break
        await resolver_uuid_sessao()
        if _session_id_override is None and tentativa < 2:
            await asyncio.sleep(2)
    sid = _get_session_id()
    if sid == settings.openwa_session_id:
        logger.warning("UUID da sessão NÃO resolvido — usando nome '%s' (pode causar 404)", sid)
    return sid


def _safe_json(resp: httpx.Response) -> dict:
    """Retorna JSON da resposta ou dict vazio se não for JSON válido."""
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return {}


def _normalizar_id(whatsapp_id: str) -> str:
    """Normaliza o ID do WhatsApp para o formato JID esperado pelo OpenWA.

    Se já tiver sufixo JID (@c.us, @lid, @g.us, etc.), preserva o original.
    Se for número cru (ex: 558199999999), adiciona @c.us."""
    if "@" in whatsapp_id:
        return whatsapp_id
    return f"{whatsapp_id}@c.us"


def _openwa_configurado() -> bool:
    return bool(settings.openwa_api_key)


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def close_client() -> None:
    """Fecha o cliente HTTP e permite recriação."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _rate_limit_envio():
    global _ultimo_envio_global
    async with _envio_lock:
        agora = time.time()
        desde_ultimo = agora - _ultimo_envio_global
        if desde_ultimo < INTERVALO_ENVIO_SEGUNDOS:
            espera = INTERVALO_ENVIO_SEGUNDOS - desde_ultimo
            logger.debug("Rate limit: aguardando %.1fs (global)", espera)
            await asyncio.sleep(espera)
        _ultimo_envio_global = time.time()


async def _enviar_mensagem_uma_vez(whatsapp_id: str, texto: str) -> dict:
    """Envia mensagem via OpenWA (sem proteção anti-ban, sem retry)."""
    global _ultimo_erro_envio
    if not _openwa_configurado():
        return {"status": "error", "message": "OpenWA não configurado (OPENWA_API_KEY ausente)"}

    await _rate_limit_envio()

    payload = {
        "chatId": _normalizar_id(whatsapp_id),
        "text": texto,
    }
    client = await get_client()
    resp = await client.post(
        f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/messages/send-text",
        json=payload,
        headers=_get_headers(),
    )
    if resp.status_code >= 400:
        body_text = resp.text[:2000]
        _ultimo_erro_envio = {
            "status_code": resp.status_code,
            "body": body_text,
            "whatsapp_id": whatsapp_id,
            "texto": texto[:200],
            "session_id": await _get_session_id_garantido(),
        }
        logger.error("OpenWA send-text error %s: %s", resp.status_code, body_text)
    resp.raise_for_status()
    _ultimo_erro_envio = None  # limpa erro anterior no sucesso
    key = whatsapp_id.split("@")[0] if "@" in whatsapp_id else whatsapp_id
    _ultimos_envios[key] = time.time()
    if len(_ultimos_envios) > 1000:
        agora = time.time()
        expirados = [k for k, v in _ultimos_envios.items() if agora - v > 3600]
        for k in expirados:
            del _ultimos_envios[k]
    return _safe_json(resp)


_enviar_mensagem_raw = async_retry(should_retry=_deve_retentar)(_enviar_mensagem_uma_vez)


async def _reiniciar_openwa() -> bool:
    """Tenta reiniciar o servico OpenWA via API de infra."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                f"{_get_base_url()}/infra/restart",
                headers=_get_headers(),
            )
            if resp.status_code < 400:
                logger.info("OpenWA reiniciado via API /infra/restart")
                return True
            logger.warning("Falha ao reiniciar OpenWA: %s", resp.status_code)
    except Exception as e:
        logger.warning("Erro ao reiniciar OpenWA: %s", e)
    return False


async def _enviar_mensagem_com_recovery(whatsapp_id: str, texto: str) -> dict:
    """Envia mensagem com recovery automático: se falhar 5xx, tenta re-resolver UUID e re-tenta.

    1. Tenta enviar com retry (até 3x)
    2. Se todas falharem com 5xx: re-resolve UUID e tenta +1x
    3. Se ainda falhar: tenta restart do OpenWA via API de infra
    4. Por último: recovery completo (restart sessão) + +1x
    """
    for tentativa in range(4):
        try:
            return await _enviar_mensagem_raw(whatsapp_id, texto)
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                raise
            if tentativa == 0:
                logger.warning("send-text 5xx (tentativa 1/4) — re-resolvendo UUID...")
                global _session_id_override
                _session_id_override = None
                await resolver_uuid_sessao()
                await asyncio.sleep(1)
                continue
            if tentativa == 1:
                logger.warning("send-text 5xx (tentativa 2/4) — restart OpenWA via API...")
                reiniciado = await _reiniciar_openwa()
                if reiniciado:
                    await asyncio.sleep(10)
                continue
            if tentativa == 2:
                logger.warning("send-text 5xx (tentativa 3/4) — recovery da sessão...")
                try:
                    from src.services.whatsapp import verificar_e_reconectar
                    await verificar_e_reconectar()
                    await asyncio.sleep(3)
                except Exception as rec_err:
                    logger.error("Recovery falhou: %s", rec_err)
                continue
            raise
    return {"status": "error", "message": "send-text falhou após recovery"}


async def enviar_mensagem(whatsapp_id: str, texto: str) -> dict:
    """Envia mensagem com proteção anti-ban (delay, limite, simulação de digitação) e recovery automático."""
    return await enviar_com_seguranca(whatsapp_id, texto, _enviar_mensagem_com_recovery)


async def _enviar_midia_uma_vez(whatsapp_id: str, url_midia: str, tipo: str = "image") -> dict:
    """Envia mídia via OpenWA (sem proteção anti-ban, sem retry)."""
    if not _openwa_configurado():
        return {"status": "error", "message": "OpenWA não configurado"}

    tipo_map = {
        "image": "send-image",
        "document": "send-document",
        "audio": "send-audio",
        "video": "send-video",
    }
    endpoint = tipo_map.get(tipo, "send-image")

    payload = {
        "chatId": _normalizar_id(whatsapp_id),
        "url": url_midia,
    }
    if tipo == "audio":
        payload["ptt"] = True
    client = await get_client()
    resp = await client.post(
        f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/messages/{endpoint}",
        json=payload,
        headers=_get_headers(),
    )
    resp.raise_for_status()
    return _safe_json(resp)


_enviar_midia_raw = async_retry(should_retry=_deve_retentar)(_enviar_midia_uma_vez)


async def _enviar_midia_com_recovery(whatsapp_id: str, url_midia: str, tipo: str = "image") -> dict:
    """Envia mídia com recovery automático."""
    for tentativa in range(2):
        try:
            return await _enviar_midia_raw(whatsapp_id, url_midia, tipo)
        except httpx.HTTPStatusError as e:
            if tentativa == 0 and e.response.status_code >= 500:
                logger.warning("send-midia 5xx — recovery da sessão...")
                try:
                    from src.services.whatsapp import verificar_e_reconectar
                    await verificar_e_reconectar()
                    await asyncio.sleep(3)
                except Exception as rec_err:
                    logger.error("Recovery falhou: %s", rec_err)
                continue
            raise
    return {"status": "error", "message": "send-midia falhou após recovery"}


async def enviar_midia(whatsapp_id: str, url_midia: str, tipo: str = "image") -> dict:
    """Envia mídia com proteção anti-ban e recovery automático."""
    return await enviar_com_seguranca(
        whatsapp_id,
        f"[{tipo}]",
        lambda wa_id, _: _enviar_midia_com_recovery(wa_id, url_midia, tipo),
    )


@async_retry(should_retry=_deve_retentar)
async def baixar_midia(midia_id: str) -> bytes:
    """Baixa o conteúdo binário de uma mídia via OpenWA API."""
    if not _openwa_configurado():
        raise RuntimeError("OpenWA não configurado")

    client = await get_client()
    resp = await client.get(
        f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/media/{midia_id}",
        headers=_get_headers(),
    )
    resp.raise_for_status()
    return resp.content


async def criar_sessao() -> dict:
    """Cria uma nova sessão no OpenWA.

    Se a API retornar UUID no campo 'id', armazena como _session_id_override
    para que chamadas subsequentes usem o UUID em vez do nome.
    """
    global _session_id_override
    client = await get_client()
    nome = settings.openwa_session_id
    resp = await client.post(
        f"{_get_base_url()}/sessions",
        json={"name": nome},
        headers=_get_headers(),
    )

    if resp.status_code == 409:
        # Sessão já existe — buscar UUID pelo nome
        logger.info("Sessão '%s' já existe — buscando UUID...", nome)
        list_resp = await client.get(
            f"{_get_base_url()}/sessions",
            headers=_get_headers(),
        )
        sessions = _safe_json(list_resp)
        if isinstance(sessions, list):
            for s in sessions:
                if s.get("name") == nome and s.get("id"):
                    _session_id_override = s["id"]
                    logger.info("UUID encontrado para sessão '%s': %s", nome, s["id"])
                    return s
        # Fallback: se não encontrou, propaga o 409 original
        resp.raise_for_status()

    resp.raise_for_status()
    data = _safe_json(resp)
    uuid = data.get("id") or ""
    if uuid:
        _session_id_override = uuid
        logger.info("Session UUID resolvido: %s (name=%s)", uuid, nome)
    return data


async def iniciar_sessao() -> dict:
    """Inicia a sessão e retorna status (pode gerar QR code)."""
    client = await get_client()
    resp = await client.post(
        f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/start",
        headers=_get_headers(),
    )
    resp.raise_for_status()
    return _safe_json(resp)


def extrair_qr_base64(resp: dict) -> str:
    """Extrai o QR code base64 da resposta da API, tentando múltiplas chaves e removendo prefixo data:."""
    for chave in ("qrCode", "qr", "base64", "qrcode", "qr_code"):
        valor = resp.get(chave, "")
        if valor:
            if valor.startswith("data:image/"):
                # Remove o prefixo data:image/...;base64,
                if ";base64," in valor:
                    valor = valor.split(";base64,", 1)[1]
                else:
                    # Caso raro: data:image/png,... sem base64 explícito
                    valor = valor.split(",", 1)[1] if "," in valor else valor
            return valor
    return ""


async def obter_qr() -> dict:
    """Retorna o QR code atual da sessão (base64) para escaneamento."""
    client = await get_client()
    try:
        resp = await client.get(
            f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/qr",
            headers=_get_headers(),
        )
        resp.raise_for_status()
        data = _safe_json(resp)
        logger.info("Resposta da API OpenWA (qr): %s", data)
        return data
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.warning("QR HTTP error: %s — %s", e.response.status_code, body)
        if "already authenticated" in body:
            return {"status": "connected", "message": "WhatsApp já conectado"}
        if "not ready yet" in body or "not ready" in body:
            return {"status": "pending", "message": "QR code ainda sendo gerado"}
        return {"status": "error", "message": f"QR não disponível: {e.response.status_code}"}
    except Exception as e:
        logger.error("Erro inesperado ao obter QR: %s", e)
        return {"status": "error", "message": str(e)}


async def deletar_sessao() -> dict:
    """Remove a sessão atual do OpenWA (desconecta o WhatsApp) e limpa o UUID cacheado."""
    global _session_id_override
    client = await get_client()
    try:
        resp = await client.delete(
            f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}",
            headers=_get_headers(),
        )
        resp.raise_for_status()
        _session_id_override = None
        logger.info("Sessão removida e UUID cacheado limpo")
        return {"status": "ok", "message": "Sessão removida"}
    except httpx.HTTPStatusError as e:
        return {"status": "error", "message": f"Falha ao remover sessão: {e.response.status_code}"}


async def resolver_uuid_sessao() -> str | None:
    """Lista sessões no OpenWA e encontra o UUID real para o nome configurado.

    OpenWA cria sessões com um ``name`` (definido por nós) e um ``id`` interno (UUID).
    A API espera o UUID na URL, não o nome. Esta função consulta a listagem de sessões
    e descobre o UUID correspondente ao ``openwa_session_id``.

    Retorna o UUID encontrado ou None.
    """
    global _session_id_override
    if _session_id_override:
        return _session_id_override

    client = await get_client()
    try:
        list_resp = await client.get(
            f"{_get_base_url()}/sessions",
            headers=_get_headers(),
        )
        list_resp.raise_for_status()
        sessions = _safe_json(list_resp)
        if not isinstance(sessions, list):
            logger.warning("resolver_uuid: resposta não é lista: %s", sessions)
            return None

        nome_busca = settings.openwa_session_id
        for s in sessions:
            if s.get("name") == nome_busca and s.get("id"):
                _session_id_override = s["id"]
                logger.info(
                    "UUID resolvido: '%s' → %s (status=%s)",
                    nome_busca, s["id"], s.get("status", "?"),
                )
                return s["id"]
        logger.warning(
            "resolver_uuid: sessão '%s' não encontrada na listagem (%d sessões)",
            nome_busca, len(sessions),
        )
        return None
    except Exception as e:
        logger.warning("resolver_uuid: erro ao listar sessões: %s", e)
        return None


async def configurar_webhook(webhook_url: str, force: bool = False) -> dict:
    """Configura o webhook no OpenWA para receber mensagens.

    Por padrão (force=False), apenas verifica se já existe um webhook ativo
    com a mesma URL e eventos. Se existir, retorna sem deletar/criar.
    Use force=True apenas no startup para garantir que o webhook está correto.

    Remove webhooks antigos APENAS se force=True, evitando deletar webhooks
    a cada acesso à página de QR (que causava perda de mensagens).
    """
    global _session_id_override
    client = await get_client()

    await resolver_uuid_sessao()
    session_id = _get_session_id()

    # Verificar se já existe webhook ativo com a mesma URL
    try:
        list_resp = await client.get(
            f"{_get_base_url()}/sessions/{session_id}/webhooks",
            headers=_get_headers(),
        )
        if list_resp.status_code < 400:
            webhooks = _safe_json(list_resp)
            if isinstance(webhooks, list):
                for wh in webhooks:
                    if (
                        wh.get("url") == webhook_url
                        and wh.get("active") is not False
                        and "message.received" in (wh.get("events") or [])
                    ):
                        if not force:
                            logger.debug("Webhook já existe e está ativo: %s", wh.get("id"))
                            return wh
                        # force=True: remover para recriar
                        if wh.get("id"):
                            await client.delete(
                                f"{_get_base_url()}/sessions/{session_id}/webhooks/{wh['id']}",
                                headers=_get_headers(),
                            )
                            logger.info("Webhook antigo removido (force): %s", wh["id"])
    except Exception as e:
        logger.warning("Falha ao listar webhooks: %s", e)

    # Registrar webhook
    resp = await client.post(
        f"{_get_base_url()}/sessions/{session_id}/webhooks",
        json={
            "url": webhook_url,
                    "events": ["message.received", "session.status"],
            "secret": settings.openwa_api_key,
        },
        headers=_get_headers(),
    )

    # Se ainda falhou, tentar forçar resolução de UUID e re-tentar
    if resp.status_code >= 400 and session_id == settings.openwa_session_id:
        logger.info("Webhook falhou (status %s) — forçando resolução de UUID...", resp.status_code)
        _session_id_override = None
        await resolver_uuid_sessao()
        nova_id = _get_session_id()
        if nova_id != session_id:
            session_id = nova_id
            resp = await client.post(
                f"{_get_base_url()}/sessions/{session_id}/webhooks",
                json={
                    "url": webhook_url,
            "events": ["message.received", "session.status"],
                    "secret": settings.openwa_api_key,
                },
                headers=_get_headers(),
            )

    resp.raise_for_status()
    return _safe_json(resp)


async def desconectar_sessao() -> dict:
    """Envia comando de desconexão para o WhatsApp via OpenWA.

    Tenta POST /sessions/{id}/disconnect para desvincular o dispositivo
    do WhatsApp (logout). Se o endpoint não existir (404), ignora.
    """
    client = await get_client()
    try:
        resp = await client.post(
            f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}/disconnect",
            headers=_get_headers(),
        )
        resp.raise_for_status()
        logger.info("Comando de desconexão enviado com sucesso")
        return _safe_json(resp)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info("Endpoint /disconnect não disponível (OpenWA antigo)")
            return {"status": "ok", "message": "ignorado (404)"}
        raise


async def obter_status_sessao() -> dict:
    """Retorna o status atual da sessão (conectado/desconectado)."""
    client = await get_client()
    resp = await client.get(
        f"{_get_base_url()}/sessions/{await _get_session_id_garantido()}",
        headers=_get_headers(),
    )
    resp.raise_for_status()
    return _safe_json(resp)


# ── Verificação de contato existente no WhatsApp ───────────────────────────

_contatos_conhecidos: set[str] = set()
"""Cache em memória de contatos que já existem no WhatsApp.
Populado por verificar_contato_existente. Usado para evitar
chamadas repetidas à API OpenWA."""

_bot_ja_conectado_antes: bool | None = None
"""True se a sessão OpenWA já estava 'ready' no startup do bot.
Indica que o bot já estava conectado antes e há clientes existentes."""


def marcar_contato_conhecido(whatsapp_id: str) -> None:
    """Marca um contato como conhecido (já vimos antes)."""
    key = _session_key(whatsapp_id) if "@" in whatsapp_id else whatsapp_id
    _contatos_conhecidos.add(key)


def _session_key(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


async def verificar_contato_existente(whatsapp_id: str) -> bool | None:
    """Verifica se um contato já existe no WhatsApp (tem histórico de conversa).

    Tenta:
    1. Cache em memória (_contatos_conhecidos)
    2. API OpenWA: GET /sessions/{id}/contacts/{jid}
    3. API OpenWA: GET /sessions/{id}/chats (fallback)

    Returns:
        True  → contato existe (já tem histórico no WhatsApp)
        False → contato não encontrado
        None  → não foi possível verificar (API indisponível)
    """
    key = _session_key(whatsapp_id) if "@" in whatsapp_id else whatsapp_id
    if key in _contatos_conhecidos:
        return True

    jid = f"{key}@c.us"

    try:
        client = await get_client()
        base = _get_base_url()
        session_id = await _get_session_id_garantido()
        headers = _get_headers()

        # Tentativa 1: GET contacts/{jid}
        resp = await client.get(
            f"{base}/sessions/{session_id}/contacts/{jid}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = _safe_json(resp)
            if data and data.get("id"):
                _contatos_conhecidos.add(key)
                return True
        elif resp.status_code == 404:
            return False

        # Tentativa 2: listar chats e buscar pelo jid
        chats_resp = await client.get(
            f"{base}/sessions/{session_id}/chats",
            headers=headers,
            timeout=15,
        )
        if chats_resp.status_code == 200:
            chats = _safe_json(chats_resp)
            if isinstance(chats, list):
                for chat in chats:
                    chat_id = (chat.get("id") or chat.get("jid") or "").split("@")[0]
                    if chat_id == key:
                        _contatos_conhecidos.add(key)
                        return True
                return False  # lista de chats veio, não encontrou

        # API não disponível
        return None

    except Exception as e:
        logger.debug("verificar_contato_existente falhou para %s: %s", key, e)
        return None


async def detectar_conexao_anterior() -> bool:
    """Verifica se a sessão OpenWA já estava conectada antes do startup.

    Deve ser chamada UMA vez no startup do bot para setar
    _bot_ja_conectado_antes. Se a sessão já estava 'ready',
    significa que há clientes com histórico no WhatsApp.
    """
    global _bot_ja_conectado_antes
    try:
        status = await obter_status_sessao()
        _bot_ja_conectado_antes = status.get("status") == "ready"
        if _bot_ja_conectado_antes:
            # Registrar o próprio bot como contato conhecido
            me = status.get("me") or {}
            meu_jid = (me.get("id") or "").split("@")[0]
            if meu_jid:
                _contatos_conhecidos.add(meu_jid)
        return _bot_ja_conectado_antes
    except Exception as e:
        logger.debug("detectar_conexao_anterior falhou: %s", e)
        _bot_ja_conectado_antes = False
        return False


# ── Heartbeat / Auto-Reconnect ─────────────────────────────────────────────

HEARTBEAT_INTERVAL = 60       # verificar a cada 60s
RECONNECT_COOLDOWN = 300      # no máximo 1 reconexão a cada 5 min
_last_reconnect_attempt: float = 0.0


async def verificar_e_reconectar() -> dict:
    """Verifica o status da sessão e reconecta automaticamente se falhou.

    Se o status for 'failed' ou 'disconnected', tenta:
      1. Deletar sessão atual
      2. Criar nova sessão
      3. Iniciar (gerar QR)
      4. Registrar webhook
    """
    global _session_id_override, _last_reconnect_attempt

    now = time.time()
    if now - _last_reconnect_attempt < RECONNECT_COOLDOWN:
        return {"status": "ok", "message": "cooldown ativo"}

    try:
        status_data = await obter_status_sessao()
    except Exception as e:
        logger.warning("Heartbeat: falha ao obter status: %s", e)
        return {"status": "error", "message": str(e)}

    status = status_data.get("status", "")
    if status not in ("failed", "disconnected"):
        return {"status": "ok", "message": f"ativo: {status}"}

    logger.warning("Heartbeat: sessão %s — reconectando...", status)
    _last_reconnect_attempt = now

    # 1. Deletar sessão falha
    try:
        await deletar_sessao()
    except Exception as e:
        logger.warning("Heartbeat: falha ao deletar: %s", e)
    _session_id_override = None
    await asyncio.sleep(2)

    # 2. Criar nova sessão
    try:
        await criar_sessao()
    except Exception as e:
        logger.error("Heartbeat: falha ao criar sessão: %s", e)
        return {"status": "error", "message": f"criação: {e}"}
    await asyncio.sleep(2)

    # 3. Iniciar (gera QR)
    try:
        await iniciar_sessao()
    except Exception as e:
        logger.error("Heartbeat: falha ao iniciar: %s", e)

    # 4. Registrar webhook
    webhook_url = f"{settings.app_url}/webhook/whatsapp"
    try:
        await configurar_webhook(webhook_url, force=True)
    except Exception as e:
        logger.warning("Heartbeat: falha webhook: %s", e)

    logger.info("Heartbeat: reconexão concluída — QR gerado, escaneie")
    return {"status": "reconnected", "message": "sessão recriada"}


async def tarefa_heartbeat():
    """Background task: mantém sessão ativa com verificação periódica.

    Executa em loop infinito — deve ser lançada como asyncio.create_task().
    O intervalo tem jitter aleatório para evitar padrão de bot.
    """
    logger.info("Heartbeat: iniciando (intervalo base=%ds)", HEARTBEAT_INTERVAL)
    await asyncio.sleep(15)  # delay inicial p/ startup
    while True:
        try:
            await verificar_e_reconectar()
        except asyncio.CancelledError:
            logger.info("Heartbeat: cancelado")
            break
        except Exception as e:
            logger.error("Heartbeat: erro: %s", e)
        jitter = HEARTBEAT_INTERVAL * random.uniform(0.7, 1.3)
        await asyncio.sleep(jitter)
