"""Proteção anti-ban para OpenWA.

Estratégias para evitar bloqueio do WhatsApp ao usar whatsapp-web.js:
- Delay entre mensagens (simula comportamento humano)
- Limite de mensagens por minuto/hora/dia
- Detecção de múltiplas conversas simultâneas
- Pausa automática se detectar atividade suspeita
"""
import asyncio
import logging
import random
import time

logger = logging.getLogger(__name__)

# ── Configurações de segurança ──
INTERVALO_MIN_SEGUNDOS = 3.0
INTERVALO_MAX_SEGUNDOS = 8.0
INTERVALO_MESMO_CONTATO = 15.0
MAX_MENSAGENS_POR_MINUTO = 12
MAX_MENSAGENS_POR_HORA = 300
MAX_MENSAGENS_POR_DIA = 1500
MAX_CONVERSAS_SIMULTANEAS = 3
DIGITACAO_CHAR_SEGUNDO = 0.12
CONVERSA_TIMEOUT = 300
RETRY_ESPERA_MINUTO = 35
RETRY_ESPERA_HORA = 310

# ── Estado global ──
_ultimo_envio: float = 0.0
_ultimo_envio_por_contato: dict[str, float] = {}
_mensagens_no_minuto: list[float] = []
_mensagens_na_hora: list[float] = []
_mensagens_no_dia: list[float] = []
_conversas_ativas: dict[str, float] = {}


def reset() -> None:
    """Reseta todo o estado anti-ban (útil para testes)."""
    global _ultimo_envio
    _ultimo_envio = 0.0
    _ultimo_envio_por_contato.clear()
    _mensagens_no_minuto.clear()
    _mensagens_na_hora.clear()
    _mensagens_no_dia.clear()
    _conversas_ativas.clear()


def _agora() -> float:
    return time.time()


def _limpar_janelas():
    """Remove registros antigos das janelas de tempo."""
    agora = _agora()
    _mensagens_no_minuto[:] = [t for t in _mensagens_no_minuto if agora - t < 60]
    _mensagens_na_hora[:] = [t for t in _mensagens_na_hora if agora - t < 3600]
    _mensagens_no_dia[:] = [t for t in _mensagens_no_dia if agora - t < 86400]


def _limpar_conversas_inativas():
    """Remove conversas inativas (mais de CONVERSA_TIMEOUT segundos sem atividade)."""
    agora = _agora()
    expirados = [k for k, v in _conversas_ativas.items() if agora - v > CONVERSA_TIMEOUT]
    for k in expirados:
        _conversas_ativas.pop(k, None)
        logger.debug("Conversa %s removida por inatividade", k)


async def _delay_digitacao(texto: str):
    """Simula o tempo que um humano levaria para digitar o texto."""
    chars = len(texto)
    segundos = chars * DIGITACAO_CHAR_SEGUNDO
    segundos = max(0.5, min(segundos, 5.0))
    segundos += random.uniform(-0.3, 0.3)
    segundos = max(0.3, segundos)
    if segundos > 0.5:
        logger.debug("Aguardando %.1fs (simulando digitação de %d caracteres)", segundos, chars)
        await asyncio.sleep(segundos)


async def _delay_entre_envios(whatsapp_id: str):
    """Aplica delay entre envios para evitar padrão de bot."""
    global _ultimo_envio

    agora = _agora()

    desde_ultimo = agora - _ultimo_envio
    if _ultimo_envio > 0 and desde_ultimo < INTERVALO_MIN_SEGUNDOS:
        espera = random.uniform(INTERVALO_MIN_SEGUNDOS - desde_ultimo, INTERVALO_MAX_SEGUNDOS - desde_ultimo)
        espera = max(0.5, espera)
        logger.debug("Aguardando %.1fs (delay entre mensagens)", espera)
        await asyncio.sleep(espera)

    ultimo_deste_contato = _ultimo_envio_por_contato.get(whatsapp_id, 0.0)
    desde_mesmo_contato = _agora() - ultimo_deste_contato
    if ultimo_deste_contato > 0 and desde_mesmo_contato < INTERVALO_MESMO_CONTATO:
        espera = INTERVALO_MESMO_CONTATO - desde_mesmo_contato
        espera += random.uniform(0, 3.0)
        logger.debug("Aguardando %.1fs (mesmo contato)", espera)
        await asyncio.sleep(espera)


async def verificar_limites() -> tuple[bool, str]:
    """Verifica se os limites de segurança foram atingidos.

    Returns:
        (True, "") se pode enviar.
        (False, "minuto") se excedeu limite por minuto.
        (False, "hora") se excedeu limite por hora.
        (False, "dia") se excedeu limite diário.
    """
    _limpar_janelas()

    if len(_mensagens_no_minuto) >= MAX_MENSAGENS_POR_MINUTO:
        logger.warning("Limite de %d mensagens/minuto atingido.", MAX_MENSAGENS_POR_MINUTO)
        return False, "minuto"

    if len(_mensagens_na_hora) >= MAX_MENSAGENS_POR_HORA:
        logger.warning("Limite de %d mensagens/hora atingido.", MAX_MENSAGENS_POR_HORA)
        return False, "hora"

    if len(_mensagens_no_dia) >= MAX_MENSAGENS_POR_DIA:
        logger.warning("Limite diário de %d mensagens atingido.", MAX_MENSAGENS_POR_DIA)
        return False, "dia"

    return True, ""


def registrar_conversa(whatsapp_id: str) -> bool:
    """Registra uma conversa como ativa. Retorna False se excedeu limite."""
    _limpar_conversas_inativas()
    if len(_conversas_ativas) >= MAX_CONVERSAS_SIMULTANEAS:
        logger.warning("Máximo de %d conversas simultâneas atingido.", MAX_CONVERSAS_SIMULTANEAS)
        return False
    _conversas_ativas[whatsapp_id] = _agora()
    return True


def finalizar_conversa(whatsapp_id: str):
    """Marca uma conversa como finalizada."""
    _conversas_ativas.pop(whatsapp_id, None)


async def registrar_envio(whatsapp_id: str):
    """Registra um envio e atualiza contadores."""
    global _ultimo_envio

    agora = _agora()
    _ultimo_envio = agora
    _ultimo_envio_por_contato[whatsapp_id] = agora
    _mensagens_no_minuto.append(agora)
    _mensagens_na_hora.append(agora)
    _mensagens_no_dia.append(agora)
    _conversas_ativas[whatsapp_id] = agora


async def esperar_vez(whatsapp_id: str, texto: str, max_retry_attempts: int = 3) -> bool:
    """Ponto único de controle anti-ban antes de enviar.

    Aplica delays, verifica limites e registra a conversa.
    Re-tenta automaticamente quando limites são excedidos.

    Args:
        whatsapp_id: ID do WhatsApp do destinatário.
        texto: Texto da mensagem (para simular digitação).
        max_retry_attempts: Máximo de retentativas quando limite é atingido.

    Returns:
        True se pode prosseguir, False se deve abortar.
    """
    for tentativa in range(max_retry_attempts):
        ok, motivo = await verificar_limites()
        if not ok:
            if motivo == "minuto":
                logger.info("Limite/minuto — aguardando %ds (tentativa %d/%d)", RETRY_ESPERA_MINUTO, tentativa + 1, max_retry_attempts)
                await asyncio.sleep(RETRY_ESPERA_MINUTO)
                continue
            elif motivo == "hora":
                logger.info("Limite/hora — aguardando %ds (tentativa %d/%d)", RETRY_ESPERA_HORA, tentativa + 1, max_retry_attempts)
                await asyncio.sleep(RETRY_ESPERA_HORA)
                continue
            elif motivo == "dia":
                logger.warning("Limite diário atingido — abortando envio")
                return False

        if not registrar_conversa(whatsapp_id):
            await asyncio.sleep(10)
            continue

        await _delay_entre_envios(whatsapp_id)
        await _delay_digitacao(texto)
        return True

    logger.warning("Todas as %d tentativas de esperar_vez esgotadas para %s", max_retry_attempts, whatsapp_id)
    return False


async def enviar_com_seguranca(
    whatsapp_id: str,
    texto: str,
    funcao_envio,
) -> dict:
    """Wrapper que envolve o envio com proteção anti-ban.

    Args:
        whatsapp_id: ID do WhatsApp do destinatário.
        texto: Texto da mensagem.
        funcao_envio: Função assíncrona que executa o envio real.

    Returns:
        Dict com resultado do envio.
    """
    try:
        if not await esperar_vez(whatsapp_id, texto):
            return {"status": "error", "message": "Limite de segurança atingido. Tente novamente mais tarde."}

        resultado = await funcao_envio(whatsapp_id, texto)

        await registrar_envio(whatsapp_id)
        return resultado
    finally:
        finalizar_conversa(whatsapp_id)
