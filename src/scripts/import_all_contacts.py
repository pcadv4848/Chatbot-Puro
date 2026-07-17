"""Script para importar TODOS os contatos com conversa no WhatsApp para attended_clients.

Uso:
    python -m src.scripts.import_all_contacts

Após a importação, lista todos os contatos registrados no banco.
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
        try:
            resp = await client.get(
                url, headers=headers,
                params={"limit": limit, "offset": offset},
                timeout=30,
            )
            if resp.status_code != 200:
                logger.error("Erro %s: %s", resp.status_code, resp.text[:200])
                break
            data = resp.json()
            if not isinstance(data, list):
                break
            if not data:
                break
            items.extend(data)
            if len(data) < limit:
                break
            offset += limit
        except Exception as e:
            logger.error("Erro: %s", e)
            break
    return items


async def main():
    logger.info("=" * 60)
    logger.info("IMPORTAR TODOS OS CONTATOS WHATSAPP PARA attended_clients")
    logger.info("=" * 60)

    # 1. Buscar session ID
    from src.services.whatsapp_openwa import _get_session_id_garantido
    session_id = await _get_session_id_garantido()
    base = settings.openwa_api_url
    headers = {
        "X-API-Key": settings.openwa_api_key,
        "Content-Type": "application/json",
    }
    logger.info("Session ID: %s", session_id)

    # 2. Buscar chats e contatos
    async with httpx.AsyncClient(timeout=30) as client:
        logger.info("Buscando chats...")
        chats = await _fetch_all(client, f"{base}/sessions/{session_id}/chats", headers)
        logger.info("  Total chats: %d", len(chats))

        logger.info("Buscando contatos...")
        contacts = await _fetch_all(client, f"{base}/sessions/{session_id}/contacts", headers)
        logger.info("  Total contatos: %d", len(contacts))

    # 3. Extrair números únicos (não-grupo)
    numeros: dict[str, str] = {}

    for chat in chats:
        if chat.get("isGroup"):
            continue
        jid = chat.get("id", "")
        nome = (chat.get("name") or "").strip()
        numero = _extrair_numero(jid)
        if numero and numero not in numeros:
            numeros[numero] = nome or "(sem nome)"

    for ct in contacts:
        jid = ct.get("id", "")
        nome = (ct.get("name") or ct.get("pushName") or "").strip()
        numero = ct.get("number") or _extrair_numero(jid)
        if numero and numero not in numeros:
            numeros[numero] = nome or "(sem nome)"

    logger.info("Total contatos únicos (não-grupo): %d", len(numeros))

    # 4. Adicionar todos ao attended_clients
    from src.services.attended_clients import mark_attended, is_attended, count_attended

    antes = await count_attended()
    logger.info("Registros em attended_clients ANTES: %d", antes)

    adicionados = 0
    ja_existiam = 0

    for numero, nome in sorted(numeros.items()):
        if await is_attended(numero):
            ja_existiam += 1
        else:
            await mark_attended(numero)
            adicionados += 1

    depois = await count_attended()
    logger.info("")
    logger.info("=" * 60)
    logger.info("RESUMO DA IMPORTACAO")
    logger.info("  Contatos encontrados no WhatsApp: %d", len(numeros))
    logger.info("  Já existiam em attended_clients: %d", ja_existiam)
    logger.info("  Novos adicionados: %d", adicionados)
    logger.info("  Total agora em attended_clients: %d", depois)
    logger.info("=" * 60)

    # 5. Listar TODOS os contatos registrados no banco
    logger.info("")
    logger.info("=" * 60)
    logger.info("LISTA DE TODOS OS CONTATOS NO BANCO (attended_clients)")
    logger.info("=" * 60)

    from src.db.session import async_session
    from src.db.models import AttendedClient
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(AttendedClient).order_by(AttendedClient.created_at)
        )
        todos = result.scalars().all()

    logger.info("Total registrado: %d", len(todos))
    logger.info("")
    for i, c in enumerate(todos, 1):
        nome_wpp = numeros.get(c.whatsapp_id, "")
        created = c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "?"
        if nome_wpp:
            logger.info("  %3d. %s — %s (criado em %s)", i, c.whatsapp_id, nome_wpp, created)
        else:
            logger.info("  %3d. %s (criado em %s)", i, c.whatsapp_id, created)

    logger.info("")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
