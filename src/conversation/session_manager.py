import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from src.conversation.state import SessionState, SessionStatus
from src.conversation.storage import (
    salvar_sessao, carregar_sessao, carregar_todas_sessoes,
    arquivar_sessoes_inativas, STORAGE_DIR,
)
from src.conversation.jid_utils import session_key
from src.config import settings

logger = logging.getLogger(__name__)

sessoes_ativas: dict[str, SessionState] = {}
_bot_phone_number: str | None = None


def set_bot_phone(phone: str | None) -> None:
    global _bot_phone_number
    _bot_phone_number = phone


async def iniciar_carregamento_sessoes() -> None:
    sessoes = await carregar_todas_sessoes()
    sessoes_ativas.clear()
    for k, v in sessoes.items():
        key = session_key(k)
        sessoes_ativas[key] = v
    logger.info("Carregadas %d sessões do disco", len(sessoes_ativas))


async def tarefa_arquivamento():
    while True:
        try:
            await asyncio.sleep(3600)
            await arquivar_sessoes_inativas(sessoes_ativas)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Erro na tarefa de arquivamento: %s", e)


async def obter_ou_criar_sessao(whatsapp_id: str) -> SessionState:
    key = session_key(whatsapp_id)
    if key in sessoes_ativas:
        old = sessoes_ativas[key]
        if old.whatsapp_id != whatsapp_id:
            old.whatsapp_id = whatsapp_id
        return old

    sessao = await carregar_sessao(key)
    if sessao is None:
        sessao = SessionState(whatsapp_id=whatsapp_id)
    else:
        sessao.whatsapp_id = whatsapp_id
        sessao.existing_client = True
        if sessao.status == SessionStatus.PAUSADO:
            logger.info("Sessão retomada para %s", whatsapp_id)
        elif sessao.status == SessionStatus.ARQUIVADO:
            sessao.status = SessionStatus.CLASSIFICANDO
            sessao.motivo_pausa = None
            logger.info("Sessão arquivada reativada para %s", whatsapp_id)

    sessoes_ativas[key] = sessao
    return sessao
