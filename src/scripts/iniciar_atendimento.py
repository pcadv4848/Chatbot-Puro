"""Inicia atendimento para números que enviaram mensagens mas não receberam resposta.

Uso: python3 -m src.scripts.iniciar_atendimento
Execute de dentro do diretório chatbot-puro/ com o venv ativado.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from src.config import settings
from src.services.attended_clients import mark_unattended
from src.conversation.storage import deletar_sessao
from src.conversation.state import SessionState, SessionStatus


async def iniciar_atendimento(whatsapp_id: str):
    """Remove de attended_clients, deleta sessão existente e envia mensagem inicial."""
    logger.info("=" * 60)
    logger.info("Processando número: %s", whatsapp_id)

    # Remove de attended_clients se presente
    try:
        await mark_unattended(whatsapp_id)
        logger.info("  Removido de attended_clients")
    except Exception as e:
        logger.warning("  Erro ao remover de attended_clients: %s", e)

    # Deleta sessão existente
    try:
        await deletar_sessao(whatsapp_id)
        logger.info("  Sessão existente deletada")
    except Exception as e:
        logger.warning("  Erro ao deletar sessão: %s", e)

    # Envia mensagem via OpenWA
    from src.services.whatsapp import enviar_mensagem

    mensagem = (
        "Olá! Recebemos sua mensagem anterior e estamos prontos para continuar seu atendimento. "
        "Me conta, o que está acontecendo?"
    )
    try:
        resultado = await enviar_mensagem(whatsapp_id, mensagem)
        logger.info("  Mensagem enviada com sucesso: %s", resultado)
    except Exception as e:
        logger.error("  Erro ao enviar mensagem: %s", e)
        raise

    logger.info("  Atendimento iniciado para %s", whatsapp_id)


async def main():
    numeros = [
        "552799157892",
        "5527999157892",
    ]

    logger.info("Iniciando atendimento para %d número(s)", len(numeros))
    for numero in numeros:
        try:
            await iniciar_atendimento(numero)
        except Exception as e:
            logger.error("Falha ao processar %s: %s", numero, e)

    logger.info("Processo concluído.")


if __name__ == "__main__":
    asyncio.run(main())