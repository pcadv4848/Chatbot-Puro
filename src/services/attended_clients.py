"""Gerenciamento de clientes atendidos no PostgreSQL.

Cliente "atendido" = ja passou pelo chatbot e foi marcado como antigo.
Estes clientes nao recebem resposta da IA (existing_client=True).
"""
import logging
import re

from sqlalchemy import select

from src.db.session import async_session

logger = logging.getLogger(__name__)

_JA_SINCRONIZOU = False
"""Evita sincronizar contatos do WhatsApp mais de uma vez por execucao."""


async def is_attended(whatsapp_id: str) -> bool:
    """Verifica se um cliente ja foi atendido."""
    from src.db.models import AttendedClient
    from src.conversation.jid_utils import normalizar_br
    candidatos = {whatsapp_id, normalizar_br(whatsapp_id)}
    dig = re.sub(r"\D", "", whatsapp_id)
    if len(dig) == 12 and dig.startswith("55"):
        candidatos.add(dig[:4] + "9" + dig[4:])
    async with async_session() as session:
        for wa in candidatos:
            result = await session.execute(
                select(AttendedClient).where(AttendedClient.whatsapp_id == wa)
            )
            if result.scalar_one_or_none():
                return True
    return False


async def mark_attended(whatsapp_id: str) -> None:
    """Marca um cliente como atendido (adiciona na tabela)."""
    from src.db.models import AttendedClient
    from src.conversation.jid_utils import normalizar_br
    wa_normalizado = normalizar_br(whatsapp_id)
    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).where(AttendedClient.whatsapp_id == wa_normalizado)
        )
        if result.scalar_one_or_none():
            return
        entry = AttendedClient(whatsapp_id=wa_normalizado)
        session.add(entry)
        await session.commit()
        logger.info("Cliente %s marcado como atendido (normalizado=%s)", whatsapp_id, wa_normalizado)


async def count_attended() -> int:
    """Retorna o total de clientes marcados como atendidos."""
    from src.db.models import AttendedClient
    async with async_session() as session:
        result = await session.execute(select(AttendedClient))
        return len(result.scalars().all())


async def sincronizar_atendidos_do_whatsapp() -> int:
    """Busca todos os contatos do WhatsApp Web e os marca como atendidos.

    Isso garante que contatos pre-existentes (como 557133706350)
    nao recebam respostas automaticas do bot nem follow-ups.

    Retorna o numero de contatos recem-marcados.
    """
    global _JA_SINCRONIZOU
    if _JA_SINCRONIZOU:
        return 0
    _JA_SINCRONIZOU = True

    from src.services.whatsapp_openwa import listar_chats

    contatos = await listar_chats()
    if not contatos:
        logger.info("sincronizar_atendidos: nenhum contato para sincronizar")
        return 0

    from src.conversation.jid_utils import normalizar_br
    marcados = 0
    for wa_id in contatos:
        try:
            await mark_attended(normalizar_br(wa_id))
            marcados += 1
        except Exception as e:
            logger.warning("sincronizar_atendidos: falha ao marcar %s: %s", wa_id, e)

    logger.info("sincronizar_atendidos: %d contatos sincronizados como atendidos", marcados)
    return marcados


async def mark_unattended(whatsapp_id: str) -> None:
    """Remove um cliente da lista de atendidos."""
    from src.db.models import AttendedClient
    from src.conversation.jid_utils import normalizar_br
    wa_normalizado = normalizar_br(whatsapp_id)
    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).where(AttendedClient.whatsapp_id == wa_normalizado)
        )
        entry = result.scalar_one_or_none()
        if entry:
            await session.delete(entry)
            await session.commit()
            logger.info("Cliente %s removido de atendidos (normalizado=%s)", whatsapp_id, wa_normalizado)
