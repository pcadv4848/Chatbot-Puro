"""Gerenciamento de etiquetas do WhatsApp Business via OpenWA REST API.

Usa os métodos nativos do OpenWA (getAllLabels, getChatsByLabel, addLabel)
para identificar clientes com a etiqueta "NOVO CLIENTE" e decidir se o bot
deve responder com IA ou permanecer em silêncio.

Elimina a dependência da Meta Graph API e configurações WHATSAPP_TOKEN/WABA_ID.
"""
import asyncio
import json
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
_label_id_cache: dict[str, str] = {}  # nome_da_label -> id_numerico


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
    """Obtém todas as etiquetas via OpenWA REST API (GET /labels)."""
    session_id = await _get_session_id()
    if not session_id:
        return None
    base = _get_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Tenta GET /labels (Easy API) — confirmado funcional
        for url in [
            f"{base}/sessions/{session_id}/labels",
            f"{base}/sessions/{session_id}/labels/list",
        ]:
            try:
                resp = await client.get(url, headers=headers)
                logger.info("GET %s -> %s", url, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("Resposta /labels: %s", json.dumps(data, ensure_ascii=False)[:1000])
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict) and "data" in data:
                        return data["data"]
            except Exception as e:
                logger.warning("GET %s falhou: %s", url, e)

        # Fallback: POST /getAllLabels
        try:
            resp = await client.post(
                f"{base}/sessions/{session_id}/getAllLabels",
                headers=headers, json={}, timeout=10,
            )
            logger.info("POST /getAllLabels -> %s", resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                logger.info("Resposta /getAllLabels: %s", json.dumps(data, ensure_ascii=False)[:1000])
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
        except Exception as e:
            logger.warning("POST getAllLabels falhou: %s", e)

    return None


async def _chats_por_label(nome_label: str) -> set[str]:
    """Obtém contatos com uma etiqueta específica via OpenWA.

    Fluxo:
      1. GET /labels → obtem ID numerico da label pelo nome
      2. GET /labels/{id}/chats → obtem contatos com aquela label
    """
    session_id = await _get_session_id()
    if not session_id:
        logger.warning("_chats_por_label: sem session_id")
        return set()

    base = _get_base_url()
    headers = _get_headers()

    # 1. Obter todas as labels para achar o ID da que queremos
    labels = await _listar_labels()
    if not labels:
        logger.warning("_chats_por_label: _listar_labels retornou None")
        return set()

    label_id: str | None = None
    for label in labels:
        nome = label.get("name", "").strip()
        if nome.upper() == nome_label.upper():
            label_id = str(label.get("id", ""))
            logger.info("Label '%s' encontrada: id=%s, payload=%s", nome, label_id,
                        json.dumps(label, ensure_ascii=False)[:500])
            break

    if not label_id:
        logger.warning("_chats_por_label: label '%s' nao encontrada entre %d labels",
                       nome_label, len(labels))
        return set()

    # 2. Buscar contatos com esta label via GET /labels/{id}/chats
    async with httpx.AsyncClient(timeout=10.0) as client:
        for tentativa_url in [
            f"{base}/sessions/{session_id}/labels/{label_id}/chats",
            f"{base}/sessions/{session_id}/labels/{label_id}/contacts",
        ]:
            try:
                resp = await client.get(tentativa_url, headers=headers, timeout=10)
                logger.info("GET %s -> %s", tentativa_url, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("Resposta chats da label %s: %s", label_id,
                                json.dumps(data, ensure_ascii=False)[:1000])
                    contatos: set[str] = set()
                    chats_raw: list = []
                    if isinstance(data, list):
                        chats_raw = data
                    elif isinstance(data, dict):
                        tmp = data.get("data") or data.get("items") or data.get("chats") or data.get("contacts") or data.get("result") or []
                        if isinstance(tmp, list):
                            chats_raw = tmp
                        elif isinstance(tmp, dict):
                            chats_raw = tmp.get("contacts") or tmp.get("chats") or tmp.get("list") or []
                    for chat in chats_raw:
                        cid = (chat.get("id", "") or chat.get("jid", "") or
                               chat.get("chatId", "") or chat.get("remoteJid", "") or
                               chat.get("participant", "") or "")
                        if cid:
                            contatos.add(_extrair_digitos(cid))
                    if contatos:
                        logger.info("_chats_por_label: %d contatos com label '%s'", len(contatos), nome_label)
                        return contatos
            except Exception as e:
                logger.warning("GET %s falhou: %s", tentativa_url, e)

    logger.warning("_chats_por_label: nenhum contato encontrado para label '%s' (id=%s)", nome_label, label_id)
    return set()


async def _obter_id_label(nome: str) -> str | None:
    """Obtém o ID numérico de uma label pelo nome."""
    if nome in _label_id_cache:
        return _label_id_cache[nome]
    labels = await _listar_labels()
    if not labels:
        return None
    for label in labels:
        if label.get("name", "").strip().upper() == nome.upper():
            lid = str(label.get("id", ""))
            _label_id_cache[nome] = lid
            logger.info("Label '%s' mapeada para id=%s", nome, lid)
            return lid
    logger.warning("Label '%s' nao encontrada entre %d labels", nome, len(labels))
    return None


async def _verificar_label_contato(whatsapp_id: str, label_id: str) -> bool:
    """Verifica se um contato especifico tem a label pelo ID via OpenWA.

    Tenta obter o chat/contato e verificar se a label esta presente.
    """
    session_id = await _get_session_id()
    if not session_id:
        return False
    base = _get_base_url()
    headers = _get_headers()
    jid = f"{_extrair_digitos(whatsapp_id)}@c.us"
    logger.info("verificar_label_contato(%s): jid=%s, label_id=%s", whatsapp_id, jid, label_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in [
            f"{base}/sessions/{session_id}/contacts/{jid}",
            f"{base}/sessions/{session_id}/chats/{jid}",
        ]:
            try:
                resp = await client.get(url, headers=headers, timeout=10)
                logger.info("GET %s -> %s", url, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("Resposta %s: %s", url.split("/")[-1],
                                json.dumps(data, ensure_ascii=False)[:600])
                    # Verifica varios campos possiveis de labels
                    raw_labels = data.get("labels") or data.get("labelIds") or data.get("labelsArray") or []
                    if isinstance(raw_labels, list):
                        for l in raw_labels:
                            lid = str(l.get("id", l)) if isinstance(l, dict) else str(l)
                            if lid == label_id:
                                logger.info("Label %s encontrada no contato %s!", label_id, whatsapp_id)
                                return True
                    elif isinstance(raw_labels, (int, str)):
                        if str(raw_labels) == label_id:
                            return True
            except Exception as e:
                logger.warning("GET %s falhou: %s", url, e)

    logger.info("Label %s NAO encontrada no contato %s", label_id, whatsapp_id)
    return False


async def atualizar_cache(force: bool = False) -> None:
    global _cache, _ultima_atualizacao, _inicializado, _funcionando

    if not settings.openwa_api_key or not settings.openwa_api_url:
        logger.info("atualizar_cache: OpenWA nao configurado")
        _cache = set()
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        _funcionando = False
        return

    async with _lock:
        now = datetime.now()
        if not force and _ultima_atualizacao and (now - _ultima_atualizacao).total_seconds() < CACHE_TTL_SECONDS:
            logger.info("atualizar_cache: cache ainda fresco (TTL %ds)", CACHE_TTL_SECONDS)
            return
        if force and _ultima_atualizacao and (now - _ultima_atualizacao).total_seconds() < 3:
            logger.info("atualizar_cache: force ignorado (ultima ha <3s)")
            return
        logger.info("atualizar_cache: %s", "FORCE" if force else "normal")

        # Tenta bulk via _chats_por_label (pode falhar se API nao suportar)
        contatos = set()
        try:
            contatos = await _chats_por_label(NOVO_CLIENTE_LABEL)
        except Exception as e:
            logger.warning("_chats_por_label falhou: %s", e)

        _cache = contatos
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        _funcionando = True
        if contatos:
            logger.info("Cache de labels atualizado: %d contatos com '%s'", len(_cache), NOVO_CLIENTE_LABEL)
        else:
            logger.info("Cache de labels atualizado: vazio (fallback para verificacao individual)")

    # Pre-aquece o cache de ID da label (independente do bulk)
    await _obter_id_label(NOVO_CLIENTE_LABEL)


async def verificar_label(whatsapp_id: str) -> bool:
    """Verifica se um contato tem a etiqueta 'NOVO CLIENTE'.

    Estrategia:
      1. Cache bulk (se o contato estiver na lista, resposta rapida)
      2. Cache miss → verificacao individual via API OpenWA
      3. Se individual falhar, tenta refresh bulk e re-tenta
    """
    global _ultima_atualizacao, _inicializado

    if not settings.openwa_api_key or not settings.openwa_api_url:
        logger.info("verificar_label: OpenWA nao configurado")
        _inicializado = True
        return False

    if not _inicializado:
        logger.info("verificar_label: nao inicializado, inicializando")
        await atualizar_cache(force=True)

    if not _funcionando:
        logger.info("verificar_label: label service nao esta funcionando")
        return False

    raw = _extrair_digitos(whatsapp_id)
    logger.info("verificar_label(%s): raw=%s, cache_size=%d", whatsapp_id, raw, len(_cache))

    # Tier 1: cache bulk hit
    if raw in _cache:
        logger.info("verificar_label(%s): CACHE HIT", raw)
        return True

    # Tier 2: verificacao individual via API
    label_id = await _obter_id_label(NOVO_CLIENTE_LABEL)
    if label_id:
        tem_label = await _verificar_label_contato(whatsapp_id, label_id)
        if tem_label:
            _cache.add(raw)
            logger.info("verificar_label(%s): confirmado via contato individual", raw)
            return True

    # Tier 3: refresh bulk (pode pegar labels adicionadas recentemente)
    if label_id is None:
        logger.info("verificar_label(%s): sem label_id, tentando refresh bulk", raw)
        await atualizar_cache(force=True)
        if raw in _cache:
            return True

    logger.info("verificar_label(%s): FALSE — cliente sem label '%s'", raw, NOVO_CLIENTE_LABEL)
    return False


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
