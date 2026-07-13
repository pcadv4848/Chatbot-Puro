"""Consulta etiquetas do WhatsApp Business para determinar se um contato é novo cliente.

Usa a Meta Graph API para:
  1. Listar todas as etiquetas da WABA
  2. Encontrar a etiqueta "NOVO CLIENTE"
  3. Listar contatos com essa etiqueta
  4. Manter cache periódico (evita chamadas repetidas à API)

Configuração necessária no .env:
  WHATSAPP_TOKEN=<system_user_token>
  WHATSAPP_WABA_ID=<waba_id>
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
_label_id: str | None = None
_lock = asyncio.Lock()
_inicializado: bool = False


def _api_url(path: str) -> str:
    return f"https://graph.facebook.com/v{settings.whatsapp_api_version}/{path}"


def _headers() -> dict:
    token = settings.whatsapp_token
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def _encontrar_label_id(client: httpx.AsyncClient) -> str | None:
    """Busca o ID da etiqueta 'NOVO CLIENTE' na WABA."""
    waba_id = settings.whatsapp_waba_id
    if not waba_id:
        return None

    resp = await client.get(_api_url(f"{waba_id}/labels"), headers=_headers())
    if resp.is_error:
        logger.warning("Erro ao listar labels WABA: %s %s", resp.status_code, resp.text)
        return None

    data = resp.json()
    for label in data.get("data", []):
        if label.get("name", "").strip().upper() == NOVO_CLIENTE_LABEL:
            return label["id"]
    logger.warning("Label '%s' não encontrada na WABA. Labels disponíveis: %s",
                   NOVO_CLIENTE_LABEL, [l.get("name") for l in data.get("data", [])])
    return None


async def _buscar_contatos_com_label(client: httpx.AsyncClient, label_id: str) -> set[str]:
    """Busca todos os contatos que possuem a etiqueta especificada."""
    contatos: set[str] = set()
    url = _api_url(f"{label_id}/contacts")
    next_url = url

    while next_url:
        resp = await client.get(next_url, headers=_headers())
        if resp.is_error:
            logger.warning("Erro ao buscar contatos do label %s: %s %s", label_id, resp.status_code, resp.text)
            break
        data = resp.json()
        for item in data.get("data", []):
            wa_id = item.get("wa_id", "")
            if wa_id:
                contatos.add(wa_id)
        next_url = data.get("paging", {}).get("next", "")
    return contatos


async def atualizar_cache() -> None:
    """Atualiza o cache de contatos com a etiqueta 'NOVO CLIENTE'."""
    global _cache, _ultima_atualizacao, _label_id, _inicializado

    if not settings.whatsapp_token or not settings.whatsapp_waba_id:
        logger.debug("WHATSAPP_TOKEN ou WHATSAPP_WABA_ID não configurados — labels desativados")
        _cache = set()
        _ultima_atualizacao = datetime.now()
        _inicializado = True
        return

    async with _lock:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if _label_id is None:
                lid = await _encontrar_label_id(client)
                if lid is None:
                    _ultima_atualizacao = datetime.now()
                    _inicializado = True
                    return
                _label_id = lid
                logger.info("Label '%s' encontrado: ID %s", NOVO_CLIENTE_LABEL, _label_id)

            contatos = await _buscar_contatos_com_label(client, _label_id)
            _cache = contatos
            _ultima_atualizacao = datetime.now()
            _inicializado = True
            logger.info("Cache de labels atualizado: %d contatos com '%s'", len(_cache), NOVO_CLIENTE_LABEL)


async def verificar_label(whatsapp_id: str) -> bool:
    """Verifica se um contato tem a etiqueta 'NOVO CLIENTE'.

    Usa cache local; se desatualizado, atualiza assincronamente.
    Retorna True se o contato tem a etiqueta, False caso contrário.
    Se labels não estiverem configurados, retorna True (comportamento padrão).
    """
    global _ultima_atualizacao, _inicializado

    if not settings.whatsapp_token or not settings.whatsapp_waba_id:
        _inicializado = True
        return True

    # Cache ainda não inicializado — comportamento seguro (permite todos)
    if not _inicializado:
        return True

    # Se cache desatualizado, atualiza em background (não bloqueia)
    if (datetime.now() - _ultima_atualizacao).total_seconds() > CACHE_TTL_SECONDS:
        asyncio.create_task(atualizar_cache())

    # Normaliza o ID: remove @c.us, @lid, @s.whatsapp.net para comparar
    raw = whatsapp_id.split("@")[0] if "@" in whatsapp_id else whatsapp_id
    return raw in _cache


async def inicializar_labels() -> None:
    """Inicializa o cache de labels na inicialização do app."""
    await atualizar_cache()


async def tarefa_atualizacao_labels():
    """Task periódica que mantém o cache de labels atualizado."""
    while True:
        await asyncio.sleep(CACHE_TTL_SECONDS)
        try:
            await atualizar_cache()
        except Exception as e:
            logger.error("Erro na atualização periódica de labels: %s", e)
