"""Configuração compartilhada de rate limiting para webhooks.

Usa X-Forwarded-For quando disponível para funcionar atrás de proxy reverso.
"""
from slowapi import Limiter


def _ip_real(request) -> str:
    """Extrai o IP real do cliente, respeitando X-Forwarded-For para proxy reverso."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_ip_real)
