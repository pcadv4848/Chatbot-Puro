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
    if not admin_cmd and not mesmo_telefone(sessao.whatsapp_id, admin_id):
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
        sessao.step = 0
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
    if texto.startswith("LABEL."):
        return await _cmd_labels(texto)
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


async def _cmd_labels(texto: str) -> str:
    """Gerencia labels: LABEL. add {telefone}, LABEL. remove {telefone}, LABEL. list, LABEL."""
    texto = texto.strip()
    partes = texto.split(None, 2)
    cmd = partes[0] if partes else "LABEL."

    try:
        from src.services.whatsapp_labels import (
            adicionar_label_local, remover_label_local,
            listar_labels_locais, atualizar_cache,
        )

        if len(partes) >= 3 and partes[1].lower() == "add":
            telefone = partes[2].strip()
            adicionar_label_local(telefone)
            return f"Label 'NOVO CLIENTE' adicionada localmente para {telefone}."

        if len(partes) >= 3 and partes[1].lower() == "remove":
            telefone = partes[2].strip()
            remover_label_local(telefone)
            return f"Label removida localmente de {telefone}."

        if len(partes) >= 2 and partes[1].lower() == "list":
            labels = listar_labels_locais()
            if labels:
                return "Contatos com label NOVO CLIENTE:\n" + "\n".join(labels)
            return "Nenhum contato com label NOVO CLIENTE."

        # LABEL. (simples) — refresh
        await atualizar_cache()
        total = len(listar_labels_locais())
        return f"Cache atualizado. {total} contato(s) com label NOVO CLIENTE."
    except Exception as e:
        logger.error("Erro no comando LABEL: %s", e)
        return f"Erro: {e}"
