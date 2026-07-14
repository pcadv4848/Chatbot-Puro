"""Serviço de verificação de webhooks externos (Meta Cloud API)."""
import hmac
import hashlib
import logging

from src.config import settings

logger = logging.getLogger(__name__)


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
