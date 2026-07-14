import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

from src.engine.logging_filter import DadosSensiveisFilter

logging.getLogger().addFilter(DadosSensiveisFilter())

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from src.config import settings
from src.conversation.router import router as webhook_router, iniciar_carregamento_sessoes, tarefa_arquivamento, sessoes_ativas
from src.conversation.chat_local import router as chat_router
from src.engine.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.db.models import Base
    from src.db.session import engine as _engine

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tabelas do banco verificadas/criadas")

    from src.services.whatsapp import get_client, close_client, configurar_webhook

    await iniciar_carregamento_sessoes()

    if settings.whatsapp_provider == "openwa" and settings.openwa_api_key:
        from src.services.whatsapp_openwa import resolver_uuid_sessao
        uuid = await resolver_uuid_sessao()
        if uuid:
            logger.info("Session UUID resolvido na inicialização: %s", uuid)
        else:
            logger.warning("Session UUID não encontrado")

    if settings.whatsapp_provider == "openwa" and settings.openwa_api_key:
        webhook_url = f"{settings.app_url}/webhook/whatsapp"
        for tentativa in range(3):
            try:
                await configurar_webhook(webhook_url)
                logger.info("Webhook OpenWA registrado: %s", webhook_url)
                break
            except Exception as e:
                logger.warning(
                    "Falha ao registrar webhook (tentativa %d/3): %s",
                    tentativa + 1, e,
                )
                await asyncio.sleep(2)

    if settings.whatsapp_provider == "openwa" and settings.openwa_api_key:
        from src.services.whatsapp_openwa import detectar_conexao_anterior
        ja_conectado = await detectar_conexao_anterior()
        if ja_conectado:
            logger.info("Sessão OpenWA já estava conectada — há clientes com histórico no WhatsApp")
        else:
            logger.info("Sessão OpenWA é nova — ainda não há contatos sincronizados")

    heartbeat_task = None
    if settings.whatsapp_provider == "openwa" and settings.openwa_api_key:
        from src.services.whatsapp import tarefa_heartbeat
        heartbeat_task = asyncio.create_task(tarefa_heartbeat())
        logger.info("Heartbeat OpenWA iniciado")

    labels_task = None
    if settings.whatsapp_provider == "openwa" and settings.openwa_api_key:
        try:
            from src.services.whatsapp_labels import inicializar_labels, tarefa_atualizacao_labels
            await inicializar_labels()
            labels_task = asyncio.create_task(tarefa_atualizacao_labels())
            logger.info("Serviço de etiquetas OpenWA iniciado")
        except Exception as e:
            logger.warning("Etiquetas não disponíveis: %s", e)

    task = asyncio.create_task(tarefa_arquivamento())

    if settings.reminder_cooldown_days > 0:
        from src.engine.reminder import tarefa_lembretes
        reminder_task = asyncio.create_task(tarefa_lembretes(sessoes_ativas))
        logger.info(
            "Lembretes iniciados (cooldown=%dd, max=%d, intervalo=%dh)",
            settings.reminder_cooldown_days,
            settings.reminder_max_count,
            settings.reminder_interval_hours,
        )
    else:
        reminder_task = None

    client = await get_client()
    yield

    task.cancel()
    if reminder_task:
        reminder_task.cancel()
        logger.info("Lembretes cancelado")
    if heartbeat_task:
        heartbeat_task.cancel()
        logger.info("Heartbeat cancelado")
    if labels_task:
        labels_task.cancel()
        logger.info("Etiquetas cancelado")
    await close_client()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

if settings.debug:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif settings.cors_origins:
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

app.include_router(webhook_router)
app.include_router(chat_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": settings.app_name}
