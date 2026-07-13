import asyncio
import logging
from datetime import datetime, timezone, timedelta

from celery import Celery
from src.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "chatbot_puro",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    beat_schedule={
        "arquivar-sessoes-inativas": {
            "task": "src.worker.arquivar_sessoes_inativas",
            "schedule": timedelta(hours=1),
        },
    },
)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def enviar_mensagem_whatsapp(self, whatsapp_id: str, texto: str) -> dict:
    try:
        from src.services.whatsapp import enviar_mensagem
        return asyncio.run(enviar_mensagem(whatsapp_id, texto))
    except Exception as e:
        logger.error("Falha ao enviar WhatsApp para %s: %s", whatsapp_id, e)
        raise self.retry(exc=e)


@celery_app.task
def arquivar_sessoes_inativas() -> int:
    try:
        from src.conversation.storage import arquivar_sessoes_inativas as arquivar
        asyncio.run(arquivar({}))
        logger.info("Arquivamento de sessões concluído")
        return 0
    except Exception as e:
        logger.error("Erro no arquivamento: %s", e)
        return 1
