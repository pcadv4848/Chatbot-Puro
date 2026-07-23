"""Sistema de lembretes para conversas abandonadas.

Background task que verifica sessões ativas de clientes novos que não
completaram o atendimento e envia lembretes após período de inatividade.

Regras:
- Só envia para clientes novos (existing_client=False)
- Só envia se benefício NÃO foi identificado (tipo_beneficio is None)
- Só envia se bot ainda está ativo (human_attending=False)
- Só envia se houver ao menos 3 trocas na conversa
- Máximo de REMINDER_MAX_COUNT lembretes por sessão
- Intervalo mínimo de REMINDER_COOLDOWN_DAYS dias entre lembretes

Configuração via .env:
  REMINDER_COOLDOWN_DAYS=3   (dias de inatividade antes do lembrete)
  REMINDER_MAX_COUNT=2       (máximo de lembretes por sessão)
  REMINDER_INTERVAL_HOURS=6  (intervalo entre verificações)
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings
from src.conversation.state import SessionState, SessionStatus
from src.conversation.storage import salvar_sessao, carregar_sessao

logger = logging.getLogger(__name__)

_FOLLOWUP_DIR = Path(__file__).parent.parent.parent / "data"


def _ler_followup_texto() -> str:
    caminho = _FOLLOWUP_DIR / "FollowUp.txt"
    try:
        if caminho.exists():
            texto = caminho.read_text(encoding="utf-8").strip()
            if texto:
                return texto
    except Exception as e:
        logger.warning("Erro ao ler FollowUp.txt: %s", e)
    return (
        "Olá, vi que sua conversa ficou parada. \n\n"
        "Ainda precisa de ajuda com seu caso?"
        " Estou aqui para entender sua situação e te ajudar,"
        " é só me chamar."
    )


async def _verificar_lembrete(sessao: SessionState) -> bool:
    """Verifica se a sessão precisa de lembrete. Retorna True se lembrou."""
    if sessao.existing_client:
        return False
    if sessao.tipo_beneficio is not None:
        return False
    if sessao.human_attending:
        return False
    if sessao.status in (SessionStatus.CONCLUIDO, SessionStatus.ARQUIVADO):
        return False
    if len(sessao.conversa) < 3:
        return False

    try:
        ultima = datetime.fromisoformat(sessao.ultima_atividade)
        if ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False

    agora = datetime.now(timezone.utc)
    diff_dias = (agora - ultima).total_seconds() / 86400

    if diff_dias < settings.reminder_cooldown_days:
        return False

    # Evita enviar durante madrugada (0h-7h)
    hora_local = agora.hour
    if hora_local < 7:
        return False

    from src.services.whatsapp import enviar_mensagem, enviar_midia

    followup_texto = _ler_followup_texto()

    try:
        audio_path = _FOLLOWUP_DIR / "FollowUp.ogg"
        if audio_path.exists():
            audio_url = f"{settings.app_url}/data/FollowUp.ogg"
            try:
                await enviar_midia(sessao.whatsapp_id, audio_url, "audio")
                logger.info("FollowUp.ogg enviado para %s", sessao.whatsapp_id)
            except Exception as e:
                logger.error("Falha ao enviar FollowUp.ogg para %s: %s", sessao.whatsapp_id, e)

        await enviar_mensagem(sessao.whatsapp_id, followup_texto)
        sessao.reminder_count += 1
        sessao.conversa.append({
            "role": "assistant",
            "content": f"[LEMBRETE #{sessao.reminder_count}] {followup_texto}",
        })
        await salvar_sessao(sessao)
        logger.info(
            "Lembrete enviado para %s (count=%d, inatividade=%.1fd)",
            sessao.whatsapp_id, sessao.reminder_count, diff_dias,
        )
        return True
    except Exception as e:
        logger.error("Erro ao enviar lembrete para %s: %s", sessao.whatsapp_id, e)
        return False


async def _verificar_todas_sessoes(sessoes_ativas: dict) -> int:
    """Percorre todas as sessões e envia lembretes quando devido.
    Retorna quantos lembretes foram enviados.
    """
    enviados = 0

    for key, sessao in list(sessoes_ativas.items()):
        if sessao.reminder_count >= settings.reminder_max_count:
            continue
        if await _verificar_lembrete(sessao):
            enviados += 1

    from src.conversation.storage import carregar_todas_sessoes
    todas = await carregar_todas_sessoes()
    for key, sessao in todas.items():
        if key in sessoes_ativas:
            continue
        if sessao.reminder_count >= settings.reminder_max_count:
            continue
        if await _verificar_lembrete(sessao):
            enviados += 1

    return enviados


async def tarefa_lembretes(sessoes_ativas: dict):
    """Background task que verifica e envia lembretes periodicamente.

    Deve ser lançada como asyncio.create_task().
    A cada REMINDER_INTERVAL_HOURS horas, percorre sessões ativas
    e envia lembretes para quem está inativo há REMINDER_COOLDOWN_DAIS dias.
    """
    logger.info(
        "Lembretes: iniciando (cooldown=%dd, max=%d, intervalo=%dh)",
        settings.reminder_cooldown_days,
        settings.reminder_max_count,
        settings.reminder_interval_hours,
    )
    await asyncio.sleep(60)  # delay inicial para startup completo
    while True:
        try:
            enviados = await _verificar_todas_sessoes(sessoes_ativas)
            if enviados:
                logger.info("Lembretes: %d lembrete(s) enviado(s)", enviados)
        except asyncio.CancelledError:
            logger.info("Lembretes: tarefa cancelada")
            break
        except Exception as e:
            logger.error("Lembretes: erro na verificação: %s", e)
        await asyncio.sleep(settings.reminder_interval_hours * 3600)
