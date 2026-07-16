"""Dispatcher unificado entre Meta Cloud API e OpenWA.

Seleciona o provider baseado em settings.whatsapp_provider:
  - "meta":   WhatsApp Cloud API oficial (Meta)
  - "openwa": Self-hosted OpenWA Gateway (whatsapp-web.js)

Uso:
    from src.services.whatsapp import enviar_mensagem
    await enviar_mensagem("5511999999999", "Olá")
"""
import asyncio
import logging
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

_PROVIDER = None


def _get_provider():
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    provider_name = settings.whatsapp_provider.lower()
    if provider_name == "meta":
        from src.services.whatsapp_meta import (
            enviar_mensagem,
            enviar_midia,
            baixar_midia,
            get_client,
            close_client,
        )
        from src.services.whatsapp_meta import verificar_webhook_meta

        _PROVIDER = {
            "enviar_mensagem": enviar_mensagem,
            "enviar_midia": enviar_midia,
            "baixar_midia": baixar_midia,
            "verificar_webhook": verificar_webhook_meta,
            "get_client": get_client,
            "close_client": close_client,
            "criar_sessao": _nao_suportado("Meta não gerencia sessões (usa token permanente)"),
            "iniciar_sessao": _nao_suportado("Meta não gerencia sessões"),
            "obter_qr": _nao_suportado("Meta não usa QR code (token permanente)"),
            "configurar_webhook": _nao_suportado("Configure o webhook no dashboard da Meta"),
            "obter_status_sessao": _nao_suportado("Meta não gerencia sessões"),
        }
        logger.info("Provider WhatsApp: Meta Cloud API")
    elif provider_name == "openwa":
        from src.services.whatsapp_openwa import (
            enviar_mensagem,
            enviar_midia,
            baixar_midia,
            criar_sessao,
            iniciar_sessao,
            obter_qr,
            deletar_sessao,
            configurar_webhook,
            obter_status_sessao,
            resolver_uuid_sessao,
            verificar_e_reconectar,
            tarefa_heartbeat,
            get_client,
            close_client,
        )

        async def _verificar_webhook_openwa(payload: dict, sig: str | None) -> bool:
            from src.conversation.router import verificar_webhook_openwa
            return verificar_webhook_openwa(payload, sig)

        _PROVIDER = {
            "enviar_mensagem": enviar_mensagem,
            "enviar_midia": enviar_midia,
            "baixar_midia": baixar_midia,
            "criar_sessao": criar_sessao,
            "iniciar_sessao": iniciar_sessao,
            "obter_qr": obter_qr,
            "deletar_sessao": deletar_sessao,
            "configurar_webhook": configurar_webhook,
            "obter_status_sessao": obter_status_sessao,
            "resolver_uuid_sessao": resolver_uuid_sessao,
            "verificar_e_reconectar": verificar_e_reconectar,
            "tarefa_heartbeat": tarefa_heartbeat,
            "verificar_webhook": _verificar_webhook_openwa,
            "get_client": get_client,
            "close_client": close_client,
        }
        logger.info("Provider WhatsApp: OpenWA")
    else:
        raise ValueError(
            f"WHATSAPP_PROVIDER inválido: '{provider_name}'. Use 'meta' ou 'openwa'."
        )
    return _PROVIDER


def _nao_suportado(mensagem: str):
    async def _fn(*args, **kwargs) -> dict:
        logger.warning("Operação não suportada pelo provider atual: %s", mensagem)
        return {"status": "error", "message": mensagem}
    return _fn


def reset_provider():
    """Limpa o cache do provider (útil em testes)."""
    global _PROVIDER
    _PROVIDER = None


# ── Funções públicas da interface comum ──

async def enviar_mensagem(whatsapp_id: str, texto: str) -> dict:
    return await _get_provider()["enviar_mensagem"](whatsapp_id, texto)


async def enviar_midia(whatsapp_id: str, url_midia: str, tipo: str = "image") -> dict:
    return await _get_provider()["enviar_midia"](whatsapp_id, url_midia, tipo)


async def baixar_midia(midia_id: str) -> bytes:
    return await _get_provider()["baixar_midia"](midia_id)


async def verificar_webhook(body: bytes, signature: str | None) -> bool:
    fn = _get_provider()["verificar_webhook"]
    if asyncio.iscoroutinefunction(fn):
        return await fn(body, signature)
    return fn(body, signature)


async def get_client():
    fn = _get_provider()["get_client"]
    if asyncio.iscoroutinefunction(fn):
        return await fn()
    return fn()


async def close_client():
    fn = _get_provider()["close_client"]
    if asyncio.iscoroutinefunction(fn):
        return await fn()
    return fn()


async def criar_sessao() -> dict:
    return await _get_provider()["criar_sessao"]()


async def iniciar_sessao() -> dict:
    return await _get_provider()["iniciar_sessao"]()


async def obter_qr() -> dict:
    return await _get_provider()["obter_qr"]()


async def deletar_sessao() -> dict:
    return await _get_provider()["deletar_sessao"]()


async def configurar_webhook(webhook_url: str, **kwargs) -> dict:
    return await _get_provider()["configurar_webhook"](webhook_url, **kwargs)


async def obter_status_sessao() -> dict:
    return await _get_provider()["obter_status_sessao"]()


async def verificar_e_reconectar() -> dict:
    return await _get_provider()["verificar_e_reconectar"]()


async def tarefa_heartbeat():
    fn = _get_provider()["tarefa_heartbeat"]
    if asyncio.iscoroutinefunction(fn):
        return await fn()
    return fn()
