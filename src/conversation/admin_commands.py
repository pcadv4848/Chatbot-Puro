import logging
from pathlib import Path

from src.conversation.state import SessionState, SessionStatus
from src.conversation.jid_utils import mesmo_telefone, session_key
from src.config import settings

logger = logging.getLogger(__name__)

STORAGE_DIR = None


def set_storage_dir(path) -> None:
    global STORAGE_DIR
    STORAGE_DIR = path


def _get_bot_phone() -> str:
    try:
        from src.conversation.router import _bot_phone_number
        return _bot_phone_number or ""
    except ImportError:
        return ""


ADMIN_INPUTS = {
    "RESETAR.", "BOT.", "HUMANO.", "STATUS.", "ANTIGO.", "FOLLOWUP.", "FOLLOWUP:",
}

ADMIN_ALIASES: dict[str, str] = {
    "Consegue entender?": "HUMANO.",
    "Olá!": "BOT.",
    "Vamos retomar.": "RESETAR.",
    "Um momento....": "ANTIGO.",
}


async def processar_admin_commands(texto: str, sessao: SessionState, admin_cmd: bool = False,
                                   cache: dict | None = None) -> str | None:
    admin_id = settings.admin_whatsapp or ""
    bot_phone = _get_bot_phone() or ""

    # ── RESETAR: disponível para qualquer usuário ──
    if texto.upper().strip(".!?") == "RESETAR":
        from src.conversation.storage import deletar_sessao
        key = session_key(sessao.whatsapp_id)
        await deletar_sessao(sessao.whatsapp_id)
        if cache is not None and key in cache:
            del cache[key]
        sessao.__init__(whatsapp_id=sessao.whatsapp_id)
        return "Conversa resetada! Vamos comecar do zero. Como voce se chama?"

    # ── Demais comandos: apenas admin ──
    if not admin_id and not bot_phone:
        return None
    if not admin_cmd and not mesmo_telefone(sessao.whatsapp_id, admin_id) and not mesmo_telefone(sessao.whatsapp_id, bot_phone):
        return None

    texto = ADMIN_ALIASES.get(texto, texto)

    if texto == "RESETAR.":
        from src.conversation.storage import deletar_sessao
        key = session_key(sessao.whatsapp_id)
        await deletar_sessao(sessao.whatsapp_id)
        if cache is not None and key in cache:
            del cache[key]
        sessao.__init__(whatsapp_id=sessao.whatsapp_id)
        return "Conversa resetada. Cliente pode recomecar."

    if texto == "BOT.":
        from src.services.attended_clients import mark_unattended
        await mark_unattended(sessao.whatsapp_id)
        sessao.human_attending = False
        sessao.existing_client = False
        sessao.step = 0
        sessao.status = SessionStatus.CLASSIFICANDO
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        return "Modo BOT ativado. Agora respondo automaticamente."

    if texto == "HUMANO.":
        from src.services.attended_clients import mark_attended
        await mark_attended(sessao.whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        return "Modo HUMANO ativado. Fico mudo e apenas extraio dados."

    if texto == "STATUS.":
        att = "humano" if sessao.human_attending else "bot"
        benef = sessao.tipo_beneficio or "nao identificado"
        dados = "; ".join(f"{k}={v}" for k, v in sessao.dados_cliente.items()) or "nenhum"
        return (
            f"Status: {sessao.status.value}\n"
            f"Modo: {att}\n"
            f"Beneficio: {benef}\n"
            f"Dados extraidos: {dados}"
        )

    if texto == "ANTIGO.":
        from src.services.attended_clients import mark_attended, count_attended
        await mark_attended(sessao.whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        total = await count_attended()
        return f"Cliente marcado como antigo. Total de clientes antigos: {total}"

    if texto == "FOLLOWUP.":
        caminho_txt = Path(__file__).parent.parent.parent / "data" / "FollowUp.txt"
        caminho_ogg = Path(__file__).parent.parent.parent / "data" / "FollowUp.ogg"
        try:
            from src.services.whatsapp import enviar_mensagem, enviar_midia
            from src.conversation.storage import salvar_sessao
            conteudo = caminho_txt.read_text(encoding="utf-8").strip()
            if not conteudo:
                return "FollowUp.txt está vazio. Envie 'FOLLOWUP: texto' para definir."
            if caminho_ogg.exists():
                audio_url = f"{settings.app_url}/data/FollowUp.ogg"
                try:
                    await enviar_midia(sessao.whatsapp_id, audio_url, "audio")
                except Exception as e:
                    logger.error("Falha ao enviar FollowUp.ogg: %s", e)
            await enviar_mensagem(sessao.whatsapp_id, conteudo)
            sessao.reminder_count += 1
            sessao.conversa.append({
                "role": "assistant",
                "content": f"[LEMBRETE #{sessao.reminder_count}] {conteudo}",
            })
            await salvar_sessao(sessao)
            return f"Followup enviado para {sessao.whatsapp_id}:\n{conteudo}"
        except FileNotFoundError:
            return "Arquivo FollowUp.txt não encontrado."

    if texto.startswith("FOLLOWUP:"):
        novo_texto = texto[len("FOLLOWUP:"):].strip()
        caminho = Path(__file__).parent.parent.parent / "data" / "FollowUp.txt"
        caminho.write_text(novo_texto, encoding="utf-8")
        logger.info("FollowUp.txt atualizado via comando admin")
        return f"FollowUp.txt atualizado:\n{novo_texto}"

    return None
