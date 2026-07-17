"""Proteção anti-ban para OpenWA.

Estratégias para evitar bloqueio do WhatsApp ao usar whatsapp-web.js:
- Delay entre mensagens (simula comportamento humano)
- Limite de mensagens por minuto/hora/dia
- Detecção de múltiplas conversas simultâneas
- Pausa automática se detectar atividade suspeita
- Variação por horário (menos atividade em horários suspeitos)
- Limite de contatos únicos por hora
- Desaceleração progressiva ao aproximar dos limites
- Variação aleatória no limite diário
- Delay de pensamento entre turnos da conversa
- Ritmo da conversa (desacelera após mensagens rápidas consecutivas)
- Delay extra para novos contatos
- Pausas aleatórias simulando hesitação humana na digitação
"""
import asyncio
import logging
import random
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Configurações de segurança (base) ──
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

# ── Configurações avançadas ──
MAX_CONTATOS_POR_HORA = 10
PENSAMENTO_MIN_SEGUNDOS = 1.0
PENSAMENTO_MAX_SEGUNDOS = 4.0
MAX_MSG_RAPIDAS_CONVERSA = 8
JANELA_MSG_RAPIDAS = 120
LIMIAR_DESACELERACAO = 0.8
FATOR_MADRUGADA = 2.0
FATOR_NOITE = 1.4
FATOR_DIARIO_VARIACAO = 0.2

# ── Estado global (base) ──
_ultimo_envio: float = 0.0
_ultimo_envio_por_contato: dict[str, float] = {}
_mensagens_no_minuto: list[float] = []
_mensagens_na_hora: list[float] = []
_mensagens_no_dia: list[float] = []
_conversas_ativas: dict[str, float] = {}

# ── Estado global (avançado) ──
_contatos_enviados: set[str] = set()
_contatos_por_hora: dict[str, float] = {}
_conversa_historico: dict[str, list[float]] = {}
_diario_max_real: int = MAX_MENSAGENS_POR_DIA
_diario_data: str = ""


def reset() -> None:
    """Reseta todo o estado anti-ban (útil para testes)."""
    global _ultimo_envio
    _ultimo_envio = 0.0
    _ultimo_envio_por_contato.clear()
    _mensagens_no_minuto.clear()
    _mensagens_na_hora.clear()
    _mensagens_no_dia.clear()
    _conversas_ativas.clear()
    _contatos_enviados.clear()
    _contatos_por_hora.clear()
    _conversa_historico.clear()


def _agora() -> float:
    return time.time()


def _fator_horario() -> float:
    """Retorna multiplicador baseado na hora do dia.
    Reduz atividade em horários suspeitos (madrugada/início da noite)."""
    hora = datetime.now().hour
    if 0 <= hora < 6:
        return random.uniform(1.4, FATOR_MADRUGADA)
    elif 6 <= hora < 8:
        return random.uniform(1.2, 1.5)
    elif 22 <= hora < 24:
        return random.uniform(1.2, FATOR_NOITE)
    return random.uniform(0.9, 1.1)


def _get_limite_diario() -> int:
    """Retorna o limite diário com variação aleatória por dia.
    Evita que o corte seja sempre no mesmo número exato."""
    global _diario_max_real, _diario_data
    hoje = datetime.now().strftime("%Y-%m-%d")
    if hoje != _diario_data:
        _diario_data = hoje
        variacao = int(MAX_MENSAGENS_POR_DIA * FATOR_DIARIO_VARIACAO)
        _diario_max_real = MAX_MENSAGENS_POR_DIA - random.randint(0, variacao)
    return _diario_max_real


def _calcular_fator_progressivo() -> float:
    """Retorna > 1.0 quando próximo dos limites, causando desaceleração."""
    fator = 1.0
    if len(_mensagens_no_minuto) >= MAX_MENSAGENS_POR_MINUTO * LIMIAR_DESACELERACAO:
        fator *= random.uniform(1.2, 1.6)
    if len(_mensagens_na_hora) >= MAX_MENSAGENS_POR_HORA * LIMIAR_DESACELERACAO:
        fator *= random.uniform(1.1, 1.4)
    limite = _get_limite_diario()
    if len(_mensagens_no_dia) >= limite * LIMIAR_DESACELERACAO:
        fator *= random.uniform(1.3, 2.0)
    return fator


def _delay_gaussiano(media: float, desvio: float, minimo: float, maximo: float) -> float:
    """Gera delay com distribuição aproximadamente normal (mais humano que uniform)."""
    valor = random.gauss(media, desvio)
    return max(minimo, min(valor, maximo))


def _verificar_conversa_rapida(whatsapp_id: str) -> float:
    """Retorna delay adicional se muitas mensagens foram trocadas rapidamente na conversa."""
    agora = _agora()
    historico = _conversa_historico.setdefault(whatsapp_id, [])
    historico[:] = [t for t in historico if agora - t < JANELA_MSG_RAPIDAS]

    n = len(historico)
    if n >= MAX_MSG_RAPIDAS_CONVERSA:
        return random.uniform(4.0, 8.0)
    if n >= MAX_MSG_RAPIDAS_CONVERSA * 0.75:
        return random.uniform(2.0, 4.0)
    if n >= MAX_MSG_RAPIDAS_CONVERSA * 0.5:
        return random.uniform(0.5, 2.0)
    return 0.0


def _limpar_janelas():
    """Remove registros antigos das janelas de tempo."""
    agora = _agora()
    _mensagens_no_minuto[:] = [t for t in _mensagens_no_minuto if agora - t < 60]
    _mensagens_na_hora[:] = [t for t in _mensagens_na_hora if agora - t < 3600]
    _mensagens_no_dia[:] = [t for t in _mensagens_no_dia if agora - t < 86400]
    for k in list(_contatos_por_hora.keys()):
        if agora - _contatos_por_hora[k] >= 3600:
            del _contatos_por_hora[k]


def _limpar_conversas_inativas():
    """Remove conversas inativas (mais de CONVERSA_TIMEOUT segundos sem atividade)."""
    agora = _agora()
    expirados = [k for k, v in _conversas_ativas.items() if agora - v > CONVERSA_TIMEOUT]
    for k in expirados:
        _conversas_ativas.pop(k, None)
        _conversa_historico.pop(k, None)
        logger.debug("Conversa %s removida por inatividade", k)


async def _delay_pensamento():
    """Simula o tempo de leitura e pensamento antes de responder."""
    fator = _fator_horario()
    delay = _delay_gaussiano(
        (PENSAMENTO_MIN_SEGUNDOS + PENSAMENTO_MAX_SEGUNDOS) / 2,
        0.8,
        PENSAMENTO_MIN_SEGUNDOS,
        PENSAMENTO_MAX_SEGUNDOS,
    ) * fator
    if delay > 0.5:
        logger.debug("Aguardando %.1fs (pensamento)", delay)
        await asyncio.sleep(delay)


async def _delay_digitacao(texto: str):
    """Simula o tempo que um humano levaria para digitar o texto, com pausas aleatórias."""
    chars = len(texto)
    segundos = chars * DIGITACAO_CHAR_SEGUNDO
    segundos = max(0.5, min(segundos, 5.0))

    # Pausa aleatória no meio (simula hesitação humana ao digitar)
    if chars > 30 and random.random() < 0.25:
        segundos += random.uniform(0.5, 1.5)

    segundos += random.uniform(-0.3, 0.3)
    segundos = max(0.3, segundos)

    if segundos > 0.5:
        logger.debug("Aguardando %.1fs (simulando digitação de %d caracteres)", segundos, chars)
        await asyncio.sleep(segundos)


async def _delay_entre_envios(whatsapp_id: str):
    """Aplica delay entre envios para evitar padrão de bot.
    Considera horário, proximidade dos limites e se é novo contato."""
    global _ultimo_envio

    agora = _agora()
    fator_horario = _fator_horario()
    fator_progressivo = _calcular_fator_progressivo()
    fator_total = fator_horario * fator_progressivo

    desde_ultimo = agora - _ultimo_envio
    if _ultimo_envio > 0 and desde_ultimo < INTERVALO_MIN_SEGUNDOS:
        minimo = max(0.5, INTERVALO_MIN_SEGUNDOS - desde_ultimo)
        maximo = (INTERVALO_MAX_SEGUNDOS - desde_ultimo) * fator_total
        espera = _delay_gaussiano(
            minimo + 1.5,
            1.0,
            minimo,
            maximo,
        )
        logger.debug("Aguardando %.1fs (delay entre mensagens)", espera)
        await asyncio.sleep(espera)

    ultimo_deste_contato = _ultimo_envio_por_contato.get(whatsapp_id, 0.0)
    desde_mesmo_contato = _agora() - ultimo_deste_contato
    if ultimo_deste_contato > 0 and desde_mesmo_contato < INTERVALO_MESMO_CONTATO:
        espera = (INTERVALO_MESMO_CONTATO - desde_mesmo_contato) * fator_total
        espera += random.uniform(0, 3.0)
        logger.debug("Aguardando %.1fs (mesmo contato)", espera)
        await asyncio.sleep(espera)

    if whatsapp_id not in _contatos_enviados:
        espera = random.uniform(2.0, 5.0) * fator_horario
        logger.debug("Aguardando %.1fs (novo contato)", espera)
        await asyncio.sleep(espera)


async def verificar_limites() -> tuple[bool, str]:
    """Verifica se os limites de segurança foram atingidos.

    Inclui verificação de contatos únicos por hora.

    Returns:
        (True, "") se pode enviar.
        (False, "minuto") se excedeu limite por minuto.
        (False, "hora") se excedeu limite por hora ou contato/hora.
        (False, "dia") se excedeu limite diário.
    """
    _limpar_janelas()

    if len(_mensagens_no_minuto) >= MAX_MENSAGENS_POR_MINUTO:
        logger.warning("Limite de %d mensagens/minuto atingido.", MAX_MENSAGENS_POR_MINUTO)
        return False, "minuto"

    if len(_mensagens_na_hora) >= MAX_MENSAGENS_POR_HORA:
        logger.warning("Limite de %d mensagens/hora atingido.", MAX_MENSAGENS_POR_HORA)
        return False, "hora"

    if len(_contatos_por_hora) >= MAX_CONTATOS_POR_HORA:
        logger.warning("Limite de %d contatos/hora atingido.", MAX_CONTATOS_POR_HORA)
        return False, "hora"

    limite_diario = _get_limite_diario()
    if len(_mensagens_no_dia) >= limite_diario:
        logger.warning("Limite diário de %d mensagens atingido.", limite_diario)
        return False, "dia"

    return True, ""


def registrar_conversa(whatsapp_id: str) -> bool:
    """Registra uma conversa como ativa. Retorna False se excedeu limite."""
    _limpar_conversas_inativas()
    if len(_conversas_ativas) >= MAX_CONVERSAS_SIMULTANEAS:
        logger.warning("Máximo de %d conversas simultâneas atingido.", MAX_CONVERSAS_SIMULTANEAS)
        return False
    _conversas_ativas[whatsapp_id] = _agora()
    logger.debug("Conversa %s registrada (ativas: %d)", whatsapp_id, len(_conversas_ativas))
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

    _contatos_enviados.add(whatsapp_id)
    _contatos_por_hora[whatsapp_id] = agora
    historico = _conversa_historico.setdefault(whatsapp_id, [])
    historico.append(agora)
    if len(historico) > MAX_MSG_RAPIDAS_CONVERSA * 3:
        historico[:] = historico[-MAX_MSG_RAPIDAS_CONVERSA * 2:]


async def esperar_vez(whatsapp_id: str, texto: str, max_retry_attempts: int = 3) -> bool:
    """Ponto único de controle anti-ban antes de enviar.

    Aplica delays (pensamento, digitação, entre mensagens),
    verifica limites e registra a conversa.
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

        await _delay_pensamento()
        await _delay_entre_envios(whatsapp_id)
        await _delay_digitacao(texto)

        delay_conversa = _verificar_conversa_rapida(whatsapp_id)
        if delay_conversa > 0:
            logger.debug("Aguardando %.1fs (ritmo da conversa)", delay_conversa)
            await asyncio.sleep(delay_conversa)

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
