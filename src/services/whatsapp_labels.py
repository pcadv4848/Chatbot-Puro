"""Gerenciamento de etiquetas do WhatsApp Business via OpenWA REST API.

Usa os métodos nativos do OpenWA (getAllLabels, getChatsByLabel, addLabel)
para identificar clientes com a etiqueta "NOVO CLIENTE" e decidir se o bot
deve responder com IA ou permanecer em silêncio.

Elimina a dependência da Meta Graph API e configurações WHATSAPP_TOKEN/WABA_ID.
"""
import asyncio
import logging
from datetime import datetime

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

NOVO_CLIENTE_LABEL = "NOVO CLIENTE"
CACHE_TTL_SECONDS = 300  # 5 minutos

_cache: set[str] = set()
_ultima_atualizacao: datetime | None = None
_lock = asyncio.Lock()
_inicializado: bool = False
_funcionando: bool = False


def _get_headers() -> dict:
    return {
        "X-API-Key": settings.openwa_api_key,
        "Content-Type": "application/json",
    }


def _get_base_url() -> str:
    return settings.openwa_api_url.rstrip("/")


async def _get_session_id() -> str | None:
    try:
        from src.services.whatsapp_openwa import _get_session_id_garantido
        return await _get_session_id_garantido()
    except Exception as e:
        logger.warning("Não foi possível obter session_id: %s", e)
        return settings.openwa_session_id or None


def _extrair_digitos(wa_id: str) -> str:
    return wa_id.split("@")[0] if "@" in wa_id else wa_id


async def _listar_labels() -> list[dict] | None:
    """Tenta obter todas as etiquetas via OpenWA REST API."""
    session_id = await _get_session_id()
    if not session_id:
        return None
    base = _get_base_url()
    headers = _get_headers()

    urls = [
        # Pattern 1: GET /sessions/{id}/labels (Easy API estruturado)
        f"{base}/sessions/{session_id}/labels",
        # Pattern 2: GET /sessions/{id}/labels/list
        f"{base}/sessions/{session_id}/labels/list",
        # Pattern 3: POST /sessions/{id}/getAllLabels (middleware-style)
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        if "data" in data:
                            return data["data"]
                elif resp.status_code == 404:
                    continue
                elif resp.status_code >= 400:
                    logger.debug("GET %s retornou %s", url, resp.status_code)
                    continue
            except Exception as e:
                logger.debug("GET %s falhou: %s", url, e)
                continue

        # Tentativa POST /getAllLabels
        try:
            resp = await client.post(
                f"{base}/sessions/{session_id}/getAllLabels",
                headers=headers,
                json={},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
        except Exception as e:
            logger.debug("POST getAllLabels falhou: %s", e)

    return None


async def _chats_por_label(nome_label: str) -> set[str]:
    """Obtém contatos com uma etiqueta específica via OpenWA.

    Tenta primeiro getChatsByLabel, depois extrai de getAllLabels.
    """
    session_id = await _get_session_id()
    if not session_id:
        return set()
    base = _get_base_url()
    headers = _get_headers()
    contatos: set[str] = set()

    from urllib.parse import quote
    label_encoded = quote(nome_label, safe="")

    # Tentativa 1: POST /getChatsByLabel (middleware-style)
    async with httpx.AsyncClient(timeout=10.0) as client:
        for tentativa_url in [
            f"{base}/sessions/{session_id}/getChatsByLabel",
            f"{base}/sessions/{session_id}/labels/chats-by-label",
            f"{base}/sessions/{session_id}/labels/{label_encoded}/chats",
        ]:
            try:
                resp = await client.post(
                    tentativa_url,
                    headers=headers,
                    json={"args": [nome_label], "label": nome_label},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for chat in data:
                            cid = chat.get("id", "") or chat.get("jid", "") or ""
                            if cid:
                                contatos.add(_extrair_digitos(cid))
                        if contatos:
                            return contatos
                    elif isinstance(data, dict):
                        items = data.get("items") or data.get("data") or []
                        for item in items:
                            cid = item.get("id", "") or item.get("jid", "") or ""
                            if cid:
                                contatos.add(_extrair_digitos(cid))
                        if contatos:
                            return contatos
            except Exception as e:
                logger.debug("POST %s falhou: %s", tentativa_url, e)

    # Tentativa 2: extrair de getAllLabels
    labels = await _listar_labels()
    if labels:
        for label in labels:
            if label.get("name", "").strip().upper() == nome_label.upper():
                items = label.get("items") or []
                for item in items:
                    cid = item.get("id", "") or ""
                    if cid:
                        contatos.add(_extrair_digitos(cid))
                if contatos:
                    return contatos
                break  # achou a label mas sem items

    return contatos


async def atualizar_cache(force: bool = False) -> None:
    global _cache, _ultima_atualizacao, _inicializado, _funcionando

    if not settings.openwa_api_key or not settings.openwa_api_url:
        logger.debug("OpenWA não configurado — labels desativados")
        _cache = set()
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        _funcionando = False
        return

    async with _lock:
        now = datetime.now()
        if not force and _ultima_atualizacao and (now - _ultima_atualizacao).total_seconds() < CACHE_TTL_SECONDS:
            return
        if force and _ultima_atualizacao and (now - _ultima_atualizacao).total_seconds() < 3:
            return
        contatos = await _chats_por_label(NOVO_CLIENTE_LABEL)
        _cache = contatos
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        _funcionando = True
        if contatos:
            logger.info("Cache de labels atualizado: %d contatos com '%s'", len(_cache), NOVO_CLIENTE_LABEL)
        else:
            logger.debug("Cache de labels atualizado: nenhum contato com '%s'", NOVO_CLIENTE_LABEL)


async def verificar_label(whatsapp_id: str) -> bool:
    """Verifica se um contato tem a etiqueta 'NOVO CLIENTE'.

    Two-tier:
      1. Cache hit → resposta instantanea
      2. Cache miss → força atualizacao sincrona e re-tenta
    """
    global _ultima_atualizacao, _inicializado

    if not settings.openwa_api_key or not settings.openwa_api_url:
        _inicializado = True
        return False

    if not _inicializado:
        await atualizar_cache(force=True)

    if not _funcionando:
        return False

    raw = _extrair_digitos(whatsapp_id)

    if raw in _cache:
        return True

    await atualizar_cache(force=True)
    return raw in _cache


async def adicionar_label(whatsapp_id: str) -> bool:
    """Adiciona a etiqueta 'NOVO CLIENTE' a um contato via OpenWA addLabel."""
    session_id = await _get_session_id()
    if not session_id:
        return False
    base = _get_base_url()
    headers = _get_headers()
    jid = f"{_extrair_digitos(whatsapp_id)}@c.us"

    urls = [
        f"{base}/sessions/{session_id}/addLabel",
        f"{base}/sessions/{session_id}/labels/add",
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                payload = {"args": [NOVO_CLIENTE_LABEL, jid], "label": NOVO_CLIENTE_LABEL, "chatId": jid}
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    _cache.add(_extrair_digitos(whatsapp_id))
                    logger.info("Label '%s' adicionada a %s", NOVO_CLIENTE_LABEL, whatsapp_id)
                    return True
            except Exception as e:
                logger.debug("addLabel via %s falhou: %s", url, e)

    logger.warning("Não foi possível adicionar label '%s' a %s", NOVO_CLIENTE_LABEL, whatsapp_id)
    return False


async def remover_label(whatsapp_id: str) -> bool:
    """Remove a etiqueta 'NOVO CLIENTE' de um contato."""
    session_id = await _get_session_id()
    if not session_id:
        return False
    base = _get_base_url()
    headers = _get_headers()
    jid = f"{_extrair_digitos(whatsapp_id)}@c.us"

    # Tenta POST /removeLabel (se disponível)
    urls = [
        f"{base}/sessions/{session_id}/removeLabel",
        f"{base}/sessions/{session_id}/labels/remove",
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                payload = {"args": [NOVO_CLIENTE_LABEL, jid], "label": NOVO_CLIENTE_LABEL, "chatId": jid}
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    _cache.discard(_extrair_digitos(whatsapp_id))
                    logger.info("Label '%s' removida de %s", NOVO_CLIENTE_LABEL, whatsapp_id)
                    return True
            except Exception as e:
                logger.debug("removeLabel via %s falhou: %s", url, e)

    logger.warning("RemoveLabel não suportado pela API — remova manualmente no WhatsApp Business")
    return False


async def inicializar_labels() -> None:
    await atualizar_cache()


async def tarefa_atualizacao_labels():
    while True:
        await asyncio.sleep(CACHE_TTL_SECONDS)
        try:
            await atualizar_cache()
        except Exception as e:
            logger.error("Erro na atualização periódica de labels: %s", e)
