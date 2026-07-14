import logging

from src.conversation.state import SessionState, SessionStatus
from src.conversation.jid_utils import session_key
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
    "GENERAR_DOCS.", "LABEL.",
}

ADMIN_ALIASES: dict[str, str] = {
    "Consegue entender?": "HUMANO.",
    "Olá!": "BOT.",
    "Vamos retomar.": "RESETAR.",
    "Aguarde, irei gerar sua documentação": "GENERAR_DOCS.",
}


async def processar_admin_commands(texto: str, sessao: SessionState, admin_cmd: bool = False) -> str | None:
    admin_id = settings.admin_whatsapp or _get_bot_phone() or ""
    if not admin_id:
        return None
    if not admin_cmd and session_key(sessao.whatsapp_id) != session_key(admin_id):
        return None

    texto = ADMIN_ALIASES.get(texto, texto)

    if texto == "RESETAR.":
        from src.conversation.storage import deletar_sessao
        await deletar_sessao(sessao.whatsapp_id)
        sessao.__init__(whatsapp_id=sessao.whatsapp_id)
        return "Conversa resetada. Cliente pode recomecar."
    if texto == "BOT.":
        sessao.human_attending = False
        sessao.existing_client = False
        sessao.status = SessionStatus.CLASSIFICANDO
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        return "Modo BOT ativado. Agora respondo automaticamente."
    if texto == "HUMANO.":
        sessao.human_attending = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        return "Modo HUMANO ativado. Fico mudo e apenas extraio dados."
    if texto == "STATUS.":
        att = "humano" if sessao.human_attending else "bot"
        benef = sessao.tipo_beneficio or "nao identificado"
        docs = "; ".join(d.get("nome", "") for d in sessao.documentos_gerados) or "nenhum"
        dados = "; ".join(f"{k}={v}" for k, v in sessao.dados_cliente.items()) or "nenhum"
        return (
            f"Status: {sessao.status.value}\n"
            f"Modo: {att}\n"
            f"Beneficio: {benef}\n"
            f"Dados extraidos: {dados}\n"
            f"Documentos gerados: {docs}"
        )
    if texto == "GENERAR_DOCS.":
        return await _cmd_gerar_docs(sessao)
    if texto == "LABEL.":
        return await _cmd_atualizar_labels()
    return None


async def _cmd_gerar_docs(sessao: SessionState) -> str:
    """Força geração de documentos."""
    from src.agents.supervisor import _processar_gerando
    from src.agents.tools.classificar import classificar

    if not sessao.tipo_beneficio:
        if sessao.resumo_caso:
            resultado = classificar(sessao.resumo_caso)
        else:
            textos = [
                m["content"] for m in sessao.conversa
                if isinstance(m, dict) and m.get("content")
            ]
            texto_conv = " ".join(textos[-10:]) if textos else ""
            resultado = classificar(texto_conv) if texto_conv else {"confianca": 0}
        if resultado.get("confianca", 0) >= 0.5:
            sessao.tipo_beneficio = resultado["tipo"]
            sessao.esfera = resultado.get("esfera", "adm")
            from src.conversation.storage import salvar_sessao
            await salvar_sessao(sessao)
        else:
            return (
                "Não foi possível identificar o tipo de benefício "
                "com base nos dados disponíveis. "
                "Use STATUS. para verificar as informações coletadas."
            )

    return await _processar_gerando(sessao, force=True)


async def _cmd_atualizar_labels() -> str:
    """Atualiza cache de labels do WhatsApp Business."""
    try:
        from src.services.whatsapp_labels import atualizar_cache
        await atualizar_cache()
        return "Cache de labels atualizado manualmente."
    except Exception as e:
        logger.error("Erro ao atualizar labels: %s", e)
        return f"Erro ao atualizar labels: {e}"
