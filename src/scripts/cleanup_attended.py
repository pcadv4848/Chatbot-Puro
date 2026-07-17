"""Remove de attended_clients contatos sem conversa ativa no WhatsApp.

Uso:
    python -m src.scripts.cleanup_attended
"""
import asyncio
import logging
import re

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from src.config import settings


def _extrair_numero(jid: str) -> str | None:
    if not jid or '@lid' in jid or '@g.us' in jid:
        return None
    parte = jid.split('@')[0]
    parte = re.sub(r'\D', '', parte)
    return parte if parte else None


async def _fetch_all(client: httpx.AsyncClient, url: str, headers: dict) -> list[dict]:
    items = []
    offset = 0
    limit = 1000
    while True:
        resp = await client.get(
            url, headers=headers,
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Erro %s: %s", resp.status_code, resp.text[:200])
            break
        data = resp.json()
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return items


async def main():
    logger.info("=" * 60)
    logger.info("REMOVER CONTATOS SEM CONVERSA DE attended_clients")
    logger.info("=" * 60)

    from src.services.whatsapp_openwa import _get_session_id_garantido
    session_id = await _get_session_id_garantido()
    base = settings.openwa_api_url
    headers = {
        "X-API-Key": settings.openwa_api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        logger.info("Buscando chats (conversas ativas)...")
        chats = await _fetch_all(client, f"{base}/sessions/{session_id}/chats", headers)
        logger.info("  Total chats: %d", len(chats))

    numeros_com_conversa: set[str] = set()
    for chat in chats:
        if chat.get("isGroup"):
            continue
        jid = chat.get("id", "")
        numero = _extrair_numero(jid)
        if numero:
            numeros_com_conversa.add(numero)

    logger.info("Números únicos com conversa (não-grupo): %d", len(numeros_com_conversa))

    from src.db.session import async_session
    from src.db.models import AttendedClient
    from sqlalchemy import select, delete

    async with async_session() as session:
        result = await session.execute(select(AttendedClient))
        todos = result.scalars().all()
        logger.info("Registros em attended_clients: %d", len(todos))

        removidos = 0
        mantidos = 0
        for c in todos:
            if c.whatsapp_id not in numeros_com_conversa:
                await session.delete(c)
                removidos += 1
            else:
                mantidos += 1

        await session.commit()

    logger.info("")
    logger.info("=" * 60)
    logger.info("RESUMO")
    logger.info("  Total em attended_clients antes: %d", len(todos))
    logger.info("  Mantidos (com conversa): %d", mantidos)
    logger.info("  Removidos (sem conversa): %d", removidos)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
