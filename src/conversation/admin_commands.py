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
        caminho = Path(__file__).parent.parent.parent / "data" / "FollowUp.txt"
        try:
            conteudo = caminho.read_text(encoding="utf-8")
            if not conteudo.strip():
                return "FollowUp.txt está vazio. Envie 'FOLLOWUP: texto' para definir."
            return f"FollowUp.txt:\n{conteudo}"
        except FileNotFoundError:
            return "Arquivo FollowUp.txt não encontrado."

    if texto.startswith("FOLLOWUP:"):
        novo_texto = texto[len("FOLLOWUP:"):].strip()
        caminho = Path(__file__).parent.parent.parent / "data" / "FollowUp.txt"
        caminho.write_text(novo_texto, encoding="utf-8")
        logger.info("FollowUp.txt atualizado via comando admin")
        return f"FollowUp.txt atualizado:\n{novo_texto}"

    return None
