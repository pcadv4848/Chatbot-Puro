import logging

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
    "RESETAR.", "BOT.", "HUMANO.", "STATUS.",
}

ADMIN_ALIASES: dict[str, str] = {
    "Consegue entender?": "HUMANO.",
    "Olá!": "BOT.",
    "Vamos retomar.": "RESETAR.",
}


async def processar_admin_commands(texto: str, sessao: SessionState, admin_cmd: bool = False) -> str | None:
    admin_id = settings.admin_whatsapp or _get_bot_phone() or ""
    if not admin_id:
        return None
    if not admin_cmd and not mesmo_telefone(sessao.whatsapp_id, admin_id):
        return None

    texto = ADMIN_ALIASES.get(texto, texto)

    if texto == "RESETAR.":
        from src.conversation.storage import deletar_sessao
        await deletar_sessao(sessao.whatsapp_id)
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

    return None
