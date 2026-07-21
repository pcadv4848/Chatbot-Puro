import asyncio
import json
import logging
import re
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)
from src.conversation.state import SessionState, SessionStatus
from src.conversation.prompts import SYSTEM_PROMPT
from src.agents.tools.classificar import classificar
from src.agents.tools.validar import validar_dados
from src.agents.constants import (
    PERGUNTAS_CAMPOS, PERGUNTAS_SIMPLES, VALIDAR_CAMPO,
    MESES_PT, UF_MAP, PADROES_CAMPO,
    MAX_TENTATIVAS_CLASSIFICACAO as _MAX_TENTATIVAS_CLASSIFICACAO,
    MIN_STEPS_EARLY_CLASSIFY as _MIN_STEPS_EARLY,
    MIN_STEPS_PARA_CONCLUIR as _MIN_STEPS_PARA_CONCLUIR,
    EARLY_CLASSIFY_CONFIDENCE as _EARLY_CONF,
    TRAFEGO_SAUDACAO as _TRAFEGO_SAUDACAO,
    TRAFEGO_HISTORIA as _TRAFEGO_HISTORIA,
    TRAFEGO_FINALIZAR as _TRAFEGO_FINALIZAR,
    SINAIS_DIFICULDADE as _SINAIS_DIFICULDADE,
    MENSAGEM_NAO_ENTENDI, MENSAGEM_ERRO_IA, MENSAGEM_QUOTA_EXCEDIDA,
    MENSAGEM_FORA_ESCOPO, MENSAGEM_HUMANO, SILENT,
    PERGUNTAS_CLASSIFICACAO as _PERGUNTAS_CLASSIFICACAO,
    PALAVRAS_SIM as _PALAVRAS_SIM,
    PALAVRAS_NAO as _PALAVRAS_NAO,
    PREFIXOS_NOME, PREFIXOS_RUA, NACIONALIDADES,
)
from src.agents.text_utils import (
    verificar_sim as _verificar_sim,
    verificar_nao as _verificar_nao,
    normalizar_data as _normalizar_data,
    normalizar_uf as _normalizar_uf,
    extrair_nome as _extrair_nome,
    validar_cpf_digitos as _validar_cpf,
    sanitizar_id as _sanitizar_id,
)
from src.agents.extraction import (
    extrair_e_salvar_campo as _extrair_e_salvar_campo,
)

# ── Inicializar LLM (DeepSeek > Verboo > Gemini > Claude) ──
_model = None
MODO_IA = False

if settings.deepseek_api_key:
    try:
        from langchain_openai import ChatOpenAI
        from langchain.tools import tool
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        _model = ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0.3,
        )
        MODO_IA = True
        logger.info("IA: DeepSeek configurado (modelo=%s)", settings.deepseek_model)
    except Exception as e:
        logger.warning("IA: DeepSeek falhou ao inicializar: %s", e, exc_info=True)

if not MODO_IA and settings.verboo_api_key:
    try:
        from langchain_openai import ChatOpenAI
        from langchain.tools import tool
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        _model = ChatOpenAI(
            model=settings.verboo_model,
            api_key=settings.verboo_api_key,
            base_url=settings.verboo_endpoint,
            temperature=0.3,
        )
        MODO_IA = True
        logger.info("IA: Verboo configurado (modelo=%s)", settings.verboo_model)
    except Exception as e:
        logger.warning("IA: Verboo falhou ao inicializar: %s", e, exc_info=True)

if not MODO_IA and settings.gemini_api_key:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain.tools import tool
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        _model = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.3,
        )
        MODO_IA = True
        logger.info("IA: Gemini configurado (modelo=%s)", settings.gemini_model)
    except Exception as e:
        logger.warning("IA: Gemini falhou ao inicializar: %s", e, exc_info=True)

if not MODO_IA and settings.anthropic_api_key:
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain.tools import tool
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        _model = ChatAnthropic(
            model=settings.claude_model,
            anthropic_api_key=settings.anthropic_api_key,
            temperature=0.3,
        )
        MODO_IA = True
        logger.info("IA: Anthropic configurado (modelo=%s)", settings.claude_model)
    except Exception as e:
        logger.warning("IA: Anthropic falhou ao inicializar: %s", e, exc_info=True)

if not MODO_IA:
    logger.warning("IA: NENHUM provedor disponivel — usando fallback classico")


def _pode_classificar(confianca: float, step: int) -> bool:
    """Define se o sistema pode aceitar a classificação do benefício.

    Permite classificação antecipada (step >= 6) apenas com confiança alta (>= 0.7).
    A partir do step ideal (>= 14), aceita com confiança normal (>= 0.5).
    """
    if step >= _MIN_STEPS_PARA_CONCLUIR:
        return confianca >= 0.5
    if step >= _MIN_STEPS_EARLY:
        return confianca >= _EARLY_CONF
    return False


async def _processar_humano(texto: str, sessao: SessionState) -> str:
    return SILENT


# ═══════════════════════════════════════════════════════════
#  Ponto de entrada principal
# ═══════════════════════════════════════════════════════════

async def processar(texto: str, sessao: SessionState) -> str:
    """Processa a mensagem do cliente e retorna a resposta."""
    logger.info("DEBUG processar: step=%s, midia=%s, human=%s, existing=%s, status=%s, texto='%s'",
                 sessao.step, sessao.midia_inicial_enviada, sessao.human_attending,
                 sessao.existing_client, sessao.status.value if sessao.status else None, texto[:50])

    if sessao.human_attending:
        logger.info("DEBUG processar: human_attending=True → _processar_humano")
        return await _processar_humano(texto, sessao)

    if sessao.existing_client:
        logger.info("DEBUG processar: existing_client=True → _processar_humano")
        return await _processar_humano(texto, sessao)

    if sessao.step == 0 and not sessao.midia_inicial_enviada:
        logger.info("DEBUG processar: ENVIANDO AUDIO INICIAL!")
        await _enviar_audio_inicial(sessao)
        sessao.step += 1
        return SILENT

    if sessao.status == SessionStatus.CONCLUIDO:
        sessao.status = SessionStatus.CLASSIFICANDO
        sessao.existing_client = False
        sessao.human_attending = False
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        logger.info("Sessão CONCLUIDO reativada para nova mensagem")

    if sessao.trafego_pago and sessao.status == SessionStatus.CLASSIFICANDO:
        sessao.status = SessionStatus.TRAFEGO_PAGO
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        return await _processar_trafego_pago(texto, sessao)

    if MODO_IA and _model is not None:
        try:
            return await _processar_ia(texto, sessao)
        except Exception as e:
            logger.warning("IA falhou, usando fallback: %s", e, exc_info=True)
    return await _processar_fallback(texto, sessao)


# ═══════════════════════════════════════════════════════════
#  Modo IA (DeepSeek via LangChain)
# ═══════════════════════════════════════════════════════════

async def _processar_ia(texto: str, sessao: SessionState) -> str:
    from langchain_core.messages import ToolMessage
    from src.conversation.storage import salvar_sessao

    @tool
    def classificar_beneficio(texto_cliente: str) -> str:
        """Identifica o tipo de beneficio previdenciario com base no relato do cliente."""
        return json.dumps(classificar(texto_cliente), ensure_ascii=False)

    # Só expõe a tool de classificação quando step estiver próximo do mínimo
    # para evitar que a IA identifique o benefício prematuramente
    tools = []
    if sessao.step + 1 >= _MIN_STEPS_EARLY:
        tools.append(classificar_beneficio)
    func_map = {t.name: t for t in tools}

    _local_model = _model.bind_tools(tools)

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for msg in sessao.conversa[-12:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=texto))

    try:
        response = await asyncio.wait_for(
            _local_model.ainvoke(messages),
            timeout=30,
        )
    except asyncio.TimeoutError:
        logger.error("Timeout (30s) na primeira chamada da IA")
        return MENSAGEM_ERRO_IA
    except Exception as e:
        logger.exception("Erro na primeira chamada da IA")
        if "RESOURCE_EXHAUSTED" in str(e):
            return MENSAGEM_QUOTA_EXCEDIDA
        return MENSAGEM_ERRO_IA

    sessao.step += 1
    if sessao.step > _MAX_TENTATIVAS_CLASSIFICACAO:
        from src.services.attended_clients import mark_attended
        await mark_attended(sessao.whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        await salvar_sessao(sessao)
        return MENSAGEM_HUMANO

    for _ in range(2):
        if not hasattr(response, "tool_calls") or not response.tool_calls:
            break

        messages.append(response)
        alguma_executou = False
        for tc in response.tool_calls:
            fn = func_map.get(tc["name"])
            if fn is None:
                continue
            try:
                result = await fn.ainvoke(tc["args"])
            except Exception as e:
                logger.exception("Erro ao executar tool %s", tc["name"])
                result = json.dumps(
                    {"success": False, "error": str(e)},
                    ensure_ascii=False,
                )
            await _atualizar_sessao_por_tool(tc["name"], result, sessao)
            messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )
            alguma_executou = True

        if alguma_executou:
            await salvar_sessao(sessao)
            if sessao.status == SessionStatus.AGUARDANDO_ADVOGADO:
                from src.services.attended_clients import mark_attended
                await mark_attended(sessao.whatsapp_id)
                sessao.human_attending = True
                sessao.existing_client = True
                sessao.step = 0
                await salvar_sessao(sessao)
                return MENSAGEM_HUMANO

        if not alguma_executou:
            break

        try:
            response = await asyncio.wait_for(
                _local_model.ainvoke(messages),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.error("Timeout (30s) na chamada da IA (loop)")
            return MENSAGEM_ERRO_IA
        except Exception as e:
            logger.exception("Erro na chamada da IA (loop)")
            if "RESOURCE_EXHAUSTED" in str(e):
                return MENSAGEM_QUOTA_EXCEDIDA
            ultimo_conteudo = messages[-1].content if hasattr(messages[-1], "content") else ""
            return str(ultimo_conteudo) if ultimo_conteudo else MENSAGEM_ERRO_IA

    if hasattr(response, "content") and response.content:
        return response.content
    return MENSAGEM_ERRO_IA


async def _atualizar_sessao_por_tool(nome_tool: str, resultado: str, sessao: SessionState):
    try:
        dados = json.loads(resultado) if isinstance(resultado, str) else resultado
    except (json.JSONDecodeError, TypeError):
        return

    if nome_tool == "classificar_beneficio":
        confianca = dados.get("confianca", 0)
        if _pode_classificar(confianca, sessao.step):
            sessao.tipo_beneficio = dados.get("tipo", sessao.tipo_beneficio)
            sessao.esfera = dados.get("esfera", sessao.esfera)
            if sessao.status == SessionStatus.CLASSIFICANDO:
                sessao.status = SessionStatus.AGUARDANDO_ADVOGADO

    elif nome_tool == "validar_dados_cliente":
        pass


# ═══════════════════════════════════════════════════════════
#  Modo fallback (máquina de estados + palavras-chave)
# ═══════════════════════════════════════════════════════════

async def _processar_fallback(texto: str, sessao: SessionState) -> str:
    texto = texto.strip()

    if sessao.status == SessionStatus.CLASSIFICANDO:
        return await _processar_classificando(texto, sessao)

    elif sessao.status == SessionStatus.AGUARDANDO_ADVOGADO:
        return await _processar_humano(texto, sessao)

    elif sessao.status == SessionStatus.CONCLUIDO:
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        return SILENT

    elif sessao.status in (
        SessionStatus.CONFIRMANDO,
        SessionStatus.COLETANDO_DADOS,
    ):
        sessao.human_attending = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        from src.conversation.storage import salvar_sessao
        await salvar_sessao(sessao)
        return MENSAGEM_HUMANO

    elif sessao.status == SessionStatus.AGUARDANDO_DOC:
        return await _processar_aguardando_doc(sessao)

    elif sessao.status in (SessionStatus.GERANDO, SessionStatus.REVISAO_ADVOGADO):
        return (
            "Seus documentos estao sendo processados. "
            "Em breve retornamos o contato."
        )

    elif sessao.status == SessionStatus.FORA_ESCOPO:
        resultado = classificar(texto)
        if resultado["confianca"] >= 0.5:
            sessao.step = 0
            sessao.status = SessionStatus.CLASSIFICANDO
            from src.conversation.storage import salvar_sessao
            await salvar_sessao(sessao)
            return await _processar_classificando(texto, sessao)
        sessao.tipo_beneficio = resultado["tipo"]
        sessao.esfera = resultado.get("esfera", "adm")
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        await salvar_sessao(sessao)
        return (
            "Vamos dar continuidade ao atendimento."
        )

    elif sessao.status == SessionStatus.TRAFEGO_PAGO:
        return await _processar_trafego_pago(texto, sessao)

    elif sessao.status == SessionStatus.PAUSADO:
        return (
            "Ola! Seu atendimento estava pausado. "
            "Se quiser retomar, e so me dizer o que precisa!"
        )

    return MENSAGEM_NAO_ENTENDI


# ── Estado: classificando ──

async def _processar_classificando(texto: str, sessao: SessionState) -> str:
    from src.conversation.storage import salvar_sessao

    if not sessao.resumo_caso:
        sessao.resumo_caso = f"Cliente: {texto}\n"
    else:
        sessao.resumo_caso += f"Cliente: {texto}\n"

    if sessao.step == 0 and not sessao.midia_inicial_enviada:
        await _enviar_audio_inicial(sessao)
        sessao.step += 1
        return SILENT

    sessao.step += 1
    resultado = classificar(sessao.resumo_caso)
    confianca = resultado["confianca"]

    if _pode_classificar(confianca, sessao.step):
        sessao.tipo_beneficio = resultado["tipo"]
        sessao.esfera = resultado["esfera"]
        sessao.step = 0
        from src.services.attended_clients import mark_attended
        await mark_attended(sessao.whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        await salvar_sessao(sessao)
        return (
            "Vamos dar continuidade ao atendimento. "
            "Para agilizar o preparo dos seus documentos, "
            "envie fotos do seu RG e CPF por aqui mesmo. "
            "Assim que tiver os dados, começamos a gerar tudo!"
        )

    if sessao.step > _MAX_TENTATIVAS_CLASSIFICACAO:
        from src.services.attended_clients import mark_attended
        await mark_attended(sessao.whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        sessao.motivo_pausa = "nao foi possivel identificar o beneficio"
        await salvar_sessao(sessao)
        return (
            "Vamos dar continuidade ao atendimento."
        )

    idx_pergunta = min(sessao.step - 1, len(_PERGUNTAS_CLASSIFICACAO) - 1)
    pergunta = _PERGUNTAS_CLASSIFICACAO[idx_pergunta]
    nome = sessao.dados_cliente.get("nome", "")
    if nome:
        pergunta = f"{nome}, {pergunta}"
    if sessao.step == 1:
        return f"Ola! {pergunta}"
    return pergunta


async def _enviar_audio_inicial(sessao: SessionState) -> None:
    from src.services.whatsapp import enviar_midia
    from src.conversation.storage import salvar_sessao
    audio_url = f"{settings.app_url}/data/AudioInicial.ogg"
    try:
        await enviar_midia(sessao.whatsapp_id, audio_url, "audio")
        sessao.midia_inicial_enviada = True
        sessao.conversa.append({"role": "assistant", "content": "[AudioInicial.ogg enviado]"})
        await salvar_sessao(sessao)
        logger.info("AudioInicial.ogg enviado para %s", sessao.whatsapp_id)
    except Exception as e:
        logger.error("Falha ao enviar AudioInicial.ogg para %s: %s", sessao.whatsapp_id, e)


def _msg_variada(lista: list[str], sessao: SessionState, **kwargs) -> str:
    if not lista:
        return ""
    idx = min(sessao.step, len(lista) - 1)
    msg = lista[idx]
    if kwargs:
        msg = msg.format(**kwargs)
    return msg


async def _processar_confirmando(texto: str, sessao: SessionState) -> str:
    from src.conversation.storage import salvar_sessao

    if _verificar_sim(texto):
        sessao.status = SessionStatus.COLETANDO_DADOS
        sessao.step = 0
        msg = (
            "Vamos dar continuidade ao atendimento."
            " Vou precisar de alguns dados seus:"
        )
        primeiro = _perguntar_proximo_campo(sessao)
        await salvar_sessao(sessao)
        return f"{msg} {primeiro}"

    if _verificar_nao(texto):
        sessao.tipo_beneficio = None
        sessao.esfera = None
        sessao.status = SessionStatus.CLASSIFICANDO
        sessao.step += 1
        if sessao.step >= _MAX_TENTATIVAS_CLASSIFICACAO:
            sessao.status = SessionStatus.FORA_ESCOPO
            sessao.motivo_pausa = "fora do escopo após múltiplas tentativas"
            await salvar_sessao(sessao)
            return MENSAGEM_FORA_ESCOPO
        await salvar_sessao(sessao)
        return (
            "Entendi! Vou tentar de novo."
            " Me conte o que você precisa. Por exemplo:"
            " auxílio-doença, aposentadoria, pensão ou revisão de benefício."
        )

    return (
        "As informações que você forneceu estão corretas? "
        "Responda **sim** ou **nao** para eu continuar."
    )


# ── Estado: trafego_pago ──

async def _processar_trafego_pago(texto: str, sessao: SessionState) -> str:
    from src.conversation.storage import salvar_sessao
    t = texto.strip()

    _detectar_dificuldade(texto, sessao)

    if sessao.step == 0:
        nome_atual = sessao.dados_cliente.get("nome", "")
        if nome_atual:
            sessao.step = 1
            await salvar_sessao(sessao)
            return _msg_variada(_TRAFEGO_HISTORIA, sessao, nome=nome_atual)
        sessao.step = 1
        await salvar_sessao(sessao)
        return _msg_variada(_TRAFEGO_SAUDACAO, sessao)

    if sessao.step == 1:
        nome = t
        if len(nome) < 3 or re.search(r"\d", nome):
            return "Desculpe, nao entendi o nome. Pode me dizer seu nome completo?"
        palavras_recusadas = {"nao", "não", "nao quero", "não quero", "por que", "porque", "qual", "como assim"}
        if any(p in nome.lower() for p in palavras_recusadas):
            return "Entendo que pode ser pessoal, mas preciso do seu nome para dar continuidade. Pode me informar?"
        if len(nome.split()) > 6:
            return "Pode me informar apenas seu nome completo?"
        sessao.dados_cliente["nome"] = nome
        sessao.step = 2
        sessao.historico_perguntas.append({"pergunta": "nome", "resposta": nome})
        sessao.resumo_caso = f"Cliente: {nome}\n"
        await salvar_sessao(sessao)
        return _msg_variada(_TRAFEGO_HISTORIA, sessao, nome=nome)

    sessao.resumo_caso += f"Historia: {t}\n"
    sessao.historico_perguntas.append({"pergunta": "historia", "resposta": t})
    nome = sessao.dados_cliente.get("nome", "voce")

    resultado = classificar(sessao.resumo_caso)
    tipo = resultado.get("tipo", "outro")
    sessao.tipo_beneficio = tipo
    sessao.esfera = resultado.get("esfera")

    sessao.step = 0
    sessao.trafego_pago = False
    sessao.human_attending = True
    sessao.existing_client = True
    sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
    from src.services.attended_clients import mark_attended
    await mark_attended(sessao.whatsapp_id)
    await salvar_sessao(sessao)

    return _msg_variada(_TRAFEGO_FINALIZAR, sessao, nome=nome)


# ── Estado: coletando_dados ──

async def _processar_coleta_dados(texto: str, sessao: SessionState) -> str:
    from src.conversation.storage import salvar_sessao

    _detectar_dificuldade(texto, sessao)

    dados_antes = dict(sessao.dados_cliente)
    _extrair_e_salvar_campo(texto, sessao)
    dados_mudaram = sessao.dados_cliente != dados_antes

    if not dados_mudaram:
        campos_faltando = _campos_obrigatorios_faltando(sessao)
        if campos_faltando:
            prox = campos_faltando[0]
            perguntas = PERGUNTAS_SIMPLES if sessao.simplify_mode else PERGUNTAS_CAMPOS
            pergunta = perguntas.get(prox, f"Qual seu {prox}?")
            if sessao.simplify_mode:
                msg = f"Nao consegui entender. {pergunta}"
            else:
                msg = (
                    f"Não consegui entender."
                    f" {pergunta}"
                    f" Se preferir, pode me dizer 'não lembro' ou pedir ajuda."
                )
            if sessao.step < 1:
                sessao.step += 1
                await salvar_sessao(sessao)
                return msg
            else:
                return (
                    f"Vamos tentar de outro jeito. {pergunta}"
                    f" Se não souber, pode pedir pra pular este campo."
                )

    resultado_validacao = validar_dados(sessao.dados_cliente, sessao.tipo_beneficio or "outro")

    if resultado_validacao["valido"]:
        sessao.step = 0
        sessao.status = SessionStatus.AGUARDANDO_DOC
        return await _processar_aguardando_doc(sessao)

    if resultado_validacao["campos_faltantes"]:
        faltando = resultado_validacao["campos_faltantes"]
        sessao.step = 0
        await salvar_sessao(sessao)
        return _perguntar_proximo_campo(sessao, campos_faltando=faltando)

    if resultado_validacao["inconsistencias"]:
        inconsistencias = resultado_validacao["inconsistencias"]
        sessao.step += 1

        mensagem = "Encontrei alguns problemas nos dados:\n"
        for inc in inconsistencias:
            mensagem += f"  - {inc}\n"

        if sessao.step >= _MAX_TENTATIVAS_CLASSIFICACAO + 1:
            sessao.dados_cliente.pop("cpf", None)
            sessao.dados_cliente.pop("rg", None)
            sessao.dados_cliente.pop("email", None)
            sessao.dados_cliente.pop("telefone", None)
            sessao.dados_cliente.pop("cep", None)
            mensagem += (
                "\nVou limpar esses campos pra você. "
                "Me informe novamente com calma. "
            )
        elif any("CPF" in inc for inc in inconsistencias):
            mensagem += (
                "\nParece que o CPF não está válido. "
                "Pode verificar se digitou corretamente? "
                "Se preferir, pode enviar uma foto do seu CPF que eu leio. "
            )
        else:
            mensagem += "\nPode corrigir esses dados? "

        await salvar_sessao(sessao)
        return mensagem

    return MENSAGEM_NAO_ENTENDI


def _campos_obrigatorios_faltando(sessao: SessionState,
                                  resultado_validacao: dict | None = None) -> list[str]:
    if resultado_validacao is None:
        from src.agents.tools.validar import validar_dados
        resultado_validacao = validar_dados(sessao.dados_cliente, sessao.tipo_beneficio or "outro")
    return resultado_validacao["campos_faltantes"]


def _detectar_dificuldade(texto: str, sessao: SessionState) -> bool:
    t = texto.strip().lower()
    if any(s in t for s in _SINAIS_DIFICULDADE):
        sessao.simplify_mode = True
        return True
    if len(t.split()) <= 2 and sessao.step > 2:
        sessao.simplify_mode = True
        return True
    return sessao.simplify_mode


def _perguntar_proximo_campo(sessao: SessionState,
                              campos_faltando: list[str] | None = None) -> str:
    if campos_faltando is None:
        campos_faltando = _campos_obrigatorios_faltando(sessao)
    if not campos_faltando:
        return ""

    campo = campos_faltando[0]
    perguntas = PERGUNTAS_SIMPLES if sessao.simplify_mode else PERGUNTAS_CAMPOS
    pergunta = perguntas.get(campo, f"Qual seu {campo}?")
    return pergunta


# ── Estado: aguardando_doc ──

async def _processar_aguardando_doc(sessao: SessionState) -> str:
    from src.conversation.storage import salvar_sessao
    sessao.status = SessionStatus.GERANDO
    await salvar_sessao(sessao)
    return await _processar_gerando(sessao)





# ── Estado: gerando ──

async def _processar_gerando(sessao: SessionState, force: bool = False) -> str:
    from src.conversation.storage import salvar_sessao
    from src.services.attended_clients import mark_attended

    if not force:
        validacao = validar_dados(sessao.dados_cliente, sessao.tipo_beneficio or "outro")
        if not validacao["valido"]:
            mensagem = "Notei que alguns dados precisam de ajuste antes de finalizar:\n"
            for inc in validacao.get("inconsistencias", []):
                mensagem += f"  - {inc}\n"
            if validacao.get("campos_faltantes"):
                mensagem += "\nPreciso também de:\n"
                for campo in validacao["campos_faltantes"]:
                    mensagem += f"  - {campo}\n"
            sessao.status = SessionStatus.COLETANDO_DADOS
            await salvar_sessao(sessao)
            if validacao.get("campos_faltantes"):
                mensagem += f"\n{_perguntar_proximo_campo(sessao)}"
            else:
                mensagem += "\nPode corrigir esses dados? "
            return mensagem

    sessao.status = SessionStatus.CONCLUIDO
    sessao.existing_client = True
    await mark_attended(sessao.whatsapp_id)
    await salvar_sessao(sessao)

    return "Seu caso foi registrado com sucesso!"
