"""Serviço de verificação de webhooks externos (Zapsign, Meta)."""
import hmac
import hashlib
import logging

from src.config import settings

logger = logging.getLogger(__name__)


def verificar_webhook_zapsign(body: bytes, signature: str | None) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook do Zapsign."""
    if not signature or not settings.zapsign_webhook_secret:
        return True
    expected = hmac.new(
        settings.zapsign_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verificar_webhook_meta(body: bytes, signature: str | None) -> bool:
    """Verifica assinatura do webhook da Meta Cloud API."""
    if not signature or not settings.meta_webhook_secret:
        return True
    parts = signature.split("=", 1)
    if len(parts) != 2:
        return False
    expected = hmac.new(
        settings.meta_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, parts[1])


async def processar_webhook(payload: dict) -> dict:
    """Processa evento de webhook do Zapsign.

    Returns:
        dict com evento, documento_id, signatario, assinado_em.
    """
    evento = payload.get("event", "")
    data = payload.get("data") or {}

    if evento == "document_signed":
        return {
            "evento": "assinado",
            "documento_id": data.get("document_id", ""),
            "signatario": data.get("signer_name", ""),
            "assinado_em": data.get("signed_at"),
        }

    logger.info("Evento Zapsign não mapeado: %s", evento)
    return {"evento": evento}
