"""Gerenciamento de clientes atendidos no PostgreSQL.

Cliente "atendido" = ja passou pelo chatbot e foi marcado como antigo.
Estes clientes nao recebem resposta da IA (existing_client=True).
"""
import logging

from sqlalchemy import select

from src.db.session import async_session

logger = logging.getLogger(__name__)


async def is_attended(whatsapp_id: str) -> bool:
    """Verifica se um cliente ja foi atendido."""
    from src.db.models import AttendedClient
    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).where(AttendedClient.whatsapp_id == whatsapp_id)
        )
        return result.scalar_one_or_none() is not None


async def mark_attended(whatsapp_id: str) -> None:
    """Marca um cliente como atendido (adiciona na tabela)."""
    from src.db.models import AttendedClient
    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).where(AttendedClient.whatsapp_id == whatsapp_id)
        )
        if result.scalar_one_or_none():
            return
        entry = AttendedClient(whatsapp_id=whatsapp_id)
        session.add(entry)
        await session.commit()
        logger.info("Cliente %s marcado como atendido", whatsapp_id)


async def mark_unattended(whatsapp_id: str) -> None:
    """Remove um cliente da lista de atendidos."""
    from src.db.models import AttendedClient
    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).where(AttendedClient.whatsapp_id == whatsapp_id)
        )
        entry = result.scalar_one_or_none()
        if entry:
            await session.delete(entry)
            await session.commit()
            logger.info("Cliente %s removido de atendidos", whatsapp_id)
