"""Serviço de integração com a WhatsApp Cloud API (Meta).

Funções assíncronas para envio/recebimento de mensagens e mídia.
Gerencia um client httpx.AsyncClient reutilizável para eficiência.
"""
import hashlib
import hmac
import logging

import httpx
from src.config import settings
from src.engine.idempotency import cache as idemp_cache, gerar_chave_idempotencia
from src.engine.retry import async_retry

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

_RETRYABLE_HTTP = (httpx.RequestError, httpx.HTTPStatusError)


def _whatsapp_api_base() -> str:
    """Retorna a base URL da API Meta (lida do settings a cada chamada)."""
    return (
        f"https://graph.facebook.com/"
        f"{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}"
    )


def _get_headers() -> dict:
    """Retorna headers com token atual (lido do settings em cada chamada)."""
    return {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }


def _deve_retentar_whatsapp(exc: Exception) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


async def get_client() -> httpx.AsyncClient:
    """Retorna o client httpx reutilizável, criando se necessário."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def close_client() -> None:
    """Fecha o client e permite recriação."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _whatsapp_configurado() -> bool:
    return bool(settings.whatsapp_token and settings.whatsapp_phone_number_id)


@async_retry(exceptions=_RETRYABLE_HTTP, should_retry=_deve_retentar_whatsapp)
async def enviar_mensagem(whatsapp_id: str, texto: str) -> dict:
    """Envia uma mensagem de texto para um cliente no WhatsApp.

    Usa Idempotency-Key (header) para deduplicação no lado da API Meta
    + cache local para evitar chamadas redundantes.
    A chave é determinística (baseada no conteúdo), então retries
    usam a mesma chave — resolvendo o bug H3.
    """
    if not _whatsapp_configurado():
        return {"status": "error", "message": "WhatsApp não configurado"}

    msg_key = gerar_chave_idempotencia("msg", whatsapp_id, texto)

    if idemp_cache.exists(msg_key):
        cached = idemp_cache.get(msg_key)
        if cached is not None:
            return cached

    payload = {
        "messaging_product": "whatsapp",
        "to": whatsapp_id,
        "type": "text",
        "text": {"body": texto},
    }
    headers = _get_headers()
    headers["Idempotency-Key"] = msg_key
    client = await get_client()
    resp = await client.post(f"{_whatsapp_api_base()}/messages", json=payload, headers=headers)
    resp.raise_for_status()
    result = resp.json()
    idemp_cache.set(msg_key, result)
    return result


@async_retry(exceptions=_RETRYABLE_HTTP, should_retry=_deve_retentar_whatsapp)
async def enviar_midia(whatsapp_id: str, url_midia: str, tipo: str = "image") -> dict:
    """Envia uma mídia (imagem, documento, etc.) para um cliente no WhatsApp.

    Usa Idempotency-Key (header) para deduplicação no lado da API Meta
    + cache local para evitar chamadas redundantes.
    """
    if not _whatsapp_configurado():
        return {"status": "error", "message": "WhatsApp não configurado"}

    msg_key = gerar_chave_idempotencia("midia", whatsapp_id, url_midia, tipo)

    if idemp_cache.exists(msg_key):
        cached = idemp_cache.get(msg_key)
        if cached is not None:
            return cached

    payload = {
        "messaging_product": "whatsapp",
        "to": whatsapp_id,
        "type": tipo,
        tipo: {"link": url_midia},
    }
    headers = _get_headers()
    headers["Idempotency-Key"] = msg_key
    client = await get_client()
    resp = await client.post(f"{_whatsapp_api_base()}/messages", json=payload, headers=headers)
    resp.raise_for_status()
    result = resp.json()
    idemp_cache.set(msg_key, result)
    return result


@async_retry(exceptions=_RETRYABLE_HTTP, should_retry=_deve_retentar_whatsapp)
async def baixar_midia(midia_id: str) -> bytes:
    """Baixa o conteúdo binário de uma mídia enviada pelo cliente.

    Obtém a URL de download via API Meta e depois baixa o arquivo.
    """
    if not _whatsapp_configurado():
        raise RuntimeError("WhatsApp não configurado")
    client = await get_client()

    url_resp = await client.get(
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/{midia_id}",
        headers=_get_headers(),
    )
    url_resp.raise_for_status()
    url_data = url_resp.json()
    download_url = url_data.get("url", "")
    if not download_url:
        raise ValueError(f"URL de download não encontrada para mídia {midia_id}")

    media_resp = await client.get(download_url, headers=_get_headers())
    media_resp.raise_for_status()
    return media_resp.content


def verificar_webhook(modo: str, token: str) -> bool:
    """Verifica o token de webhook exigido pela Meta no handshake inicial.

    Mantida síncrona porque é chamada apenas no GET de verificação (handshake),
    que não precisa de chamadas HTTP externas.
    """
    return modo == "subscribe" and token == settings.webhook_verify_token


def verificar_webhook_meta(payload: bytes, signature: str | None) -> bool:
    """Verifica X-Hub-Signature-256 da Meta.

    Meta envia o header 'X-Hub-Signature-256' com HMAC-SHA256 do body.
    Se o secret não estiver configurado, aceita o webhook (fallback seguro).
    """
    secret = settings.whatsapp_token
    if not secret:
        logger.warning("WHATSAPP_TOKEN não configurado — webhooks Meta não serão validados!")
        return True
    if not signature:
        logger.warning("Webhook Meta sem header de assinatura")
        return False
    expected = hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    provided = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
