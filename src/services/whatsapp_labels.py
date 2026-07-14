"""Gerenciamento de etiquetas do WhatsApp Business.

Como a API do OpenWA nao expoe quais contatos tem cada label (GET /labels/{id}/chats
retorna 404, GET /contacts/{jid} nao inclui campo labels), o sistema mantem um
arquivo JSON local com as atribuicoes. As chamadas OpenWA sao usadas apenas para
ADD/REMOVE (que funcionam), enquanto a leitura usa o arquivo local.
"""
import asyncio
import json
import logging
import os
from datetime import datetime

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

NOVO_CLIENTE_LABEL = "NOVO CLIENTE"
CACHE_TTL_SECONDS = 300
LOCAL_LABELS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "label_assignments.json")

_label_assignments: set[str] = set()  # digitos dos contatos com label
_label_assignments_loaded: bool = False
_cache: set[str] = set()
_ultima_atualizacao: datetime | None = None
_lock = asyncio.Lock()
_inicializado: bool = False
_funcionando: bool = False
_label_id_cache: dict[str, str] = {}


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
        logger.warning("Nao foi possivel obter session_id: %s", e)
        return settings.openwa_session_id or None


def _extrair_digitos(wa_id: str) -> str:
    return wa_id.split("@")[0] if "@" in wa_id else wa_id


# ── Arquivo local de atribuicoes ──

def _caminho_labels() -> str:
    return os.path.abspath(LOCAL_LABELS_FILE)


def _carregar_assignments() -> set[str]:
    global _label_assignments_loaded
    path = _caminho_labels()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if isinstance(dados, list):
                    _label_assignments.update(dados)
        _label_assignments_loaded = True
        logger.info("Assignments carregados: %d contatos em %s", len(_label_assignments), path)
    except Exception as e:
        logger.warning("Erro ao carregar assignments: %s", e)
        _label_assignments_loaded = True
    return _label_assignments


def _salvar_assignments() -> None:
    path = _caminho_labels()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(_label_assignments), f, ensure_ascii=False, indent=2)
        logger.info("Assignments salvos: %d contatos", len(_label_assignments))
    except Exception as e:
        logger.warning("Erro ao salvar assignments: %s", e)


def adicionar_label_local(telefone: str) -> bool:
    """Adiciona um telefone ao arquivo local de labels (sync)."""
    raw = _extrair_digitos(telefone)
    if raw in _label_assignments:
        return True
    _label_assignments.add(raw)
    _cache.add(raw)
    _salvar_assignments()
    logger.info("Label local adicionada para %s", raw)
    return True


def remover_label_local(telefone: str) -> bool:
    """Remove um telefone do arquivo local de labels (sync)."""
    raw = _extrair_digitos(telefone)
    if raw not in _label_assignments:
        return True
    _label_assignments.discard(raw)
    _cache.discard(raw)
    _salvar_assignments()
    logger.info("Label local removida de %s", raw)
    return True


def listar_labels_locais() -> list[str]:
    return sorted(_label_assignments)


# ── OpenWA label listing (apenas metadados) ──

async def _listar_labels() -> list[dict] | None:
    session_id = await _get_session_id()
    if not session_id:
        return None
    base = _get_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in [
            f"{base}/sessions/{session_id}/labels",
            f"{base}/sessions/{session_id}/labels/list",
        ]:
            try:
                resp = await client.get(url, headers=headers)
                logger.info("GET %s -> %s", url, resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict) and "data" in data:
                        return data["data"]
            except Exception as e:
                logger.warning("GET %s falhou: %s", url, e)

        try:
            resp = await client.post(
                f"{base}/sessions/{session_id}/getAllLabels",
                headers=headers, json={}, timeout=10,
            )
            logger.info("POST /getAllLabels -> %s", resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
        except Exception as e:
            logger.warning("POST getAllLabels falhou: %s", e)

    return None


# ── OpenWA bulk query (fallback, geralmente falha) ──

async def _chats_por_label(nome_label: str) -> set[str]:
    """Tenta obter contatos via OpenWA GET /labels/{id}/chats (quase sempre 404)."""
    session_id = await _get_session_id()
    if not session_id:
        return set()

    base = _get_base_url()
    headers = _get_headers()

    labels = await _listar_labels()
    if not labels:
        return set()

    label_id: str | None = None
    for label in labels:
        if label.get("name", "").strip().upper() == nome_label.upper():
            label_id = str(label.get("id", ""))
            break

    if not label_id:
        return set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in [
            f"{base}/sessions/{session_id}/labels/{label_id}/chats",
            f"{base}/sessions/{session_id}/labels/{label_id}/contacts",
        ]:
            try:
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    contatos: set[str] = set()
                    raw_list: list = []
                    if isinstance(data, list):
                        raw_list = data
                    elif isinstance(data, dict):
                        tmp = data.get("data") or data.get("items") or data.get("chats") or data.get("contacts") or []
                        raw_list = tmp if isinstance(tmp, list) else []
                    for chat in raw_list:
                        cid = (chat.get("id", "") or chat.get("jid", "") or
                               chat.get("chatId", "") or chat.get("remoteJid", "") or "")
                        if cid:
                            contatos.add(_extrair_digitos(cid))
                    if contatos:
                        return contatos
            except Exception:
                pass

    return set()


async def _obter_id_label(nome: str) -> str | None:
    if nome in _label_id_cache:
        return _label_id_cache[nome]
    labels = await _listar_labels()
    if not labels:
        return None
    for label in labels:
        if label.get("name", "").strip().upper() == nome.upper():
            lid = str(label.get("id", ""))
            _label_id_cache[nome] = lid
            return lid
    return None


async def atualizar_cache(force: bool = False) -> None:
    global _cache, _ultima_atualizacao, _inicializado, _funcionando

    if not _label_assignments_loaded:
        _carregar_assignments()

    if not settings.openwa_api_key or not settings.openwa_api_url:
        _cache = _label_assignments.copy()
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

        # Tenta bulk — quase sempre vazio, mas tentamos
        contatos = set()
        try:
            contatos = await _chats_por_label(NOVO_CLIENTE_LABEL)
        except Exception:
            pass

        # Unir com assignments locais (fonte primaria)
        _cache = contatos | _label_assignments
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        _funcionando = True
        logger.info("Cache atualizado: %d via OpenWA + %d local = %d total",
                    len(contatos), len(_label_assignments), len(_cache))

    await _obter_id_label(NOVO_CLIENTE_LABEL)


async def verificar_label(whatsapp_id: str) -> bool:
    """Verifica se um contato tem a etiqueta 'NOVO CLIENTE'.

    Fontes (em ordem):
      1. Arquivo local (label_assignments.json)
      2. Cache OpenWA (bulk, geralmente vazio)
    """
    global _ultima_atualizacao, _inicializado

    if not _label_assignments_loaded:
        _carregar_assignments()

    if not settings.openwa_api_key or not settings.openwa_api_url:
        _inicializado = True
        return _extrair_digitos(whatsapp_id) in _label_assignments

    if not _inicializado:
        await atualizar_cache(force=True)

    if not _funcionando:
        return _extrair_digitos(whatsapp_id) in _label_assignments

    raw = _extrair_digitos(whatsapp_id)

    # 1. Local (fonte primaria)
    if raw in _label_assignments:
        logger.info("verificar_label(%s): LOCAL HIT", raw)
        return True

    # 2. Cache OpenWA
    if raw in _cache:
        logger.info("verificar_label(%s): CACHE HIT", raw)
        return True

    # 3. Tenta refresh
    await atualizar_cache(force=True)
    if raw in _cache or raw in _label_assignments:
        return True

    logger.info("verificar_label(%s): FALSE", raw)
    return False


async def adicionar_label(whatsapp_id: str) -> bool:
    """Adiciona a label via OpenWA E no arquivo local."""
    raw = _extrair_digitos(whatsapp_id)
    adicionar_label_local(raw)

    session_id = await _get_session_id()
    if not session_id:
        return True
    base = _get_base_url()
    headers = _get_headers()
    jid = f"{raw}@c.us"

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in [
            f"{base}/sessions/{session_id}/addLabel",
            f"{base}/sessions/{session_id}/labels/add",
        ]:
            try:
                payload = {"args": [NOVO_CLIENTE_LABEL, jid], "label": NOVO_CLIENTE_LABEL, "chatId": jid}
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Label '%s' adicionada via OpenWA a %s", NOVO_CLIENTE_LABEL, raw)
                    return True
            except Exception as e:
                logger.debug("addLabel via %s falhou: %s", url, e)

    logger.warning("addLabel OpenWA falhou para %s — mantida apenas local", raw)
    return True


async def remover_label(whatsapp_id: str) -> bool:
    """Remove a label via OpenWA E do arquivo local."""
    raw = _extrair_digitos(whatsapp_id)
    remover_label_local(raw)

    session_id = await _get_session_id()
    if not session_id:
        return True
    base = _get_base_url()
    headers = _get_headers()
    jid = f"{raw}@c.us"

    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in [
            f"{base}/sessions/{session_id}/removeLabel",
            f"{base}/sessions/{session_id}/labels/remove",
        ]:
            try:
                payload = {"args": [NOVO_CLIENTE_LABEL, jid], "label": NOVO_CLIENTE_LABEL, "chatId": jid}
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Label '%s' removida via OpenWA de %s", NOVO_CLIENTE_LABEL, raw)
                    return True
            except Exception as e:
                logger.debug("removeLabel via %s falhou: %s", url, e)

    logger.warning("removeLabel OpenWA falhou para %s — removida apenas local", raw)
    return True


async def inicializar_labels() -> None:
    _carregar_assignments()
    await atualizar_cache()


async def tarefa_atualizacao_labels():
    while True:
        await asyncio.sleep(CACHE_TTL_SECONDS)
        try:
            await atualizar_cache()
        except Exception as e:
            logger.error("Erro na atualizacao periodica: %s", e)


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
