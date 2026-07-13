"""Tool de extração de dados de documentos via Tesseract + LLM.

Pipeline:
  1. Tesseract (primário): OCR local extrai texto bruto
  2. DeepSeek/Gemini/Claude (fallback): estrutura o texto em campos
  3. Regex (fallback final): padrões simples (CPF, RG, CEP)

Usada pelo agente supervisor quando o cliente envia foto de documento.
"""
import json
import logging
from typing import Optional

from src.config import settings
from src.services import ocr as ocr_service
from src.services.whatsapp import baixar_midia
from src.agents.schemas_ocr import ResultadoOCR, TipoDocumento

logger = logging.getLogger(__name__)

_llm = None


def _get_llm():
    """Retorna LLM configurado: DeepSeek > Verboo > Gemini > Claude."""
    global _llm
    if _llm is not None:
        return _llm

    if settings.deepseek_api_key:
        try:
            from langchain_openai import ChatOpenAI
            _llm = ChatOpenAI(
                model=settings.deepseek_model,
                api_key=settings.deepseek_api_key,
                base_url="https://api.deepseek.com",
                temperature=0.1,
            )
            return _llm
        except Exception:
            logger.warning("Falha ao inicializar DeepSeek no extrair_ocr")

    if settings.verboo_api_key:
        try:
            from langchain_openai import ChatOpenAI
            _llm = ChatOpenAI(
                model=settings.verboo_model,
                api_key=settings.verboo_api_key,
                base_url=settings.verboo_endpoint,
                temperature=0.1,
            )
            return _llm
        except Exception:
            logger.warning("Falha ao inicializar Verboo no extrair_ocr")

    if settings.gemini_api_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            _llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=settings.gemini_api_key,
                temperature=0.1,
            )
            return _llm
        except Exception:
            logger.warning("Falha ao inicializar Gemini no extrair_ocr")

    if settings.anthropic_api_key:
        try:
            from langchain_anthropic import ChatAnthropic
            _llm = ChatAnthropic(
                model=settings.claude_model,
                anthropic_api_key=settings.anthropic_api_key,
                temperature=0.1,
            )
        except Exception:
            logger.warning("Falha ao inicializar Claude no extrair_ocr")
    return _llm


# ── Decorator tool (fallback para dev sem langchain) ──
try:
    from langchain.tools import tool
except ImportError:
    def tool(func):
        return func


@tool
async def extrair_dados_ocr(whatsapp_midia_id: str) -> str:
    """Extrai dados estruturados de uma imagem de documento enviada pelo cliente.

    Chamar esta ferramenta quando o cliente enviar fotos de RG, CPF
    ou comprovante de residência.

    Args:
        whatsapp_midia_id: ID da mídia no WhatsApp Cloud API.

    Returns:
        JSON string com tipo_documento, dados extraídos e confianca.
    """
    try:
        imagem_bytes = await baixar_midia(whatsapp_midia_id)
        resultado = await _processar_imagem(imagem_bytes)
        return resultado.model_dump_json(by_alias=True, exclude_none=True)

    except Exception as e:
        logger.exception("Falha ao extrair dados OCR da mídia %s", whatsapp_midia_id)
        return ResultadoOCR(
            tipo_documento=TipoDocumento.desconhecido,
            confianca_media=0.0,
            texto_bruto="",
        ).model_dump_json(by_alias=True, exclude_none=True)


async def _processar_imagem(imagem_bytes: bytes) -> ResultadoOCR:
    """Processa uma imagem: Tesseract → LLM → regex."""
    # 1. Tesseract extrai texto bruto
    texto_bruto = await ocr_service.extrair_texto(imagem_bytes)
    if not texto_bruto:
        return ResultadoOCR(tipo_documento=TipoDocumento.desconhecido, texto_bruto="")

    # 2. Se IA disponível, estrutura o texto do OCR com LLM
    llm = _get_llm()
    if llm is not None:
        return await _estruturar_com_llm(texto_bruto, llm)

    # 3. Fallback final: parser regex
    return await ocr_service.extrair_dados_automatico(imagem_bytes)


async def _estruturar_com_llm(texto_bruto: str, llm) -> ResultadoOCR:
    """Usa IA (DeepSeek/Gemini/Claude) para estruturar o texto bruto do OCR em campos."""
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = ocr_service.obter_prompt_estruturacao()
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"Texto extraído por OCR:\n\n{texto_bruto}"),
    ]
    response = await llm.ainvoke(messages)

    try:
        dados = json.loads(response.content)
        tipo = dados.get("tipo_documento", "desconhecido")
        return ResultadoOCR(
            tipo_documento=TipoDocumento(tipo),
            confianca_media=0.85,
            texto_bruto=texto_bruto,
            dados_rg=_dict_para_rg(dados.get("dados", {})),
            dados_cpf=_dict_para_cpf(dados.get("dados", {})),
            dados_endereco=_dict_para_endereco(dados.get("dados", {})),
        )
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning("Falha ao interpretar resposta do LLM: %s", e)
        return ResultadoOCR(
            tipo_documento=TipoDocumento.desconhecido,
            texto_bruto=texto_bruto,
        )


# ── Helpers de conversão ──

def _dict_para_rg(dados: dict) -> Optional["DadosRG"]:
    from src.agents.schemas_ocr import DadosRG
    if any(k in dados for k in ("rg", "orgao_emissor", "filiacao_pai", "filiacao_mae")):
        return DadosRG(**{k: v for k, v in dados.items() if k in DadosRG.model_fields})
    return None


def _dict_para_cpf(dados: dict) -> Optional["DadosCPF"]:
    from src.agents.schemas_ocr import DadosCPF
    if "cpf" in dados:
        return DadosCPF(**{k: v for k, v in dados.items() if k in DadosCPF.model_fields})
    return None


def _dict_para_endereco(dados: dict) -> Optional["DadosEndereco"]:
    from src.agents.schemas_ocr import DadosEndereco
    if any(k in dados for k in ("logradouro", "bairro", "cep", "cidade")):
        return DadosEndereco(**{k: v for k, v in dados.items() if k in DadosEndereco.model_fields})
    return None


# ═══════════════════════════════════════════════════════════
#  Função auxiliar para o supervisor (fallback mode)
# ═══════════════════════════════════════════════════════════

async def processar_midia_ocr(midia_id: str) -> tuple[dict, str, bool, str]:
    """Baixa a mídia, roda OCR e retorna (dados_cliente, mensagem_resumo, erro_servico, tipo_documento).

    Usada pelo supervisor em modo fallback para integrar OCR
    automaticamente quando chega uma imagem.

    Returns:
        (dados_extraidos, mensagem, erro_servico, tipo_documento): dict para merge no
        sessao.dados_cliente, uma mensagem descritiva, flag indicando
        se houve erro de serviço (True) vs documento não reconhecido (False),
        e string do tipo de documento (rg, cpf, comprovante_endereco, desconhecido).
    """
    try:
        imagem_bytes = await baixar_midia(midia_id)
        resultado = await _processar_imagem(imagem_bytes)

        tipo_str = resultado.tipo_documento.value if resultado.tipo_documento else "desconhecido"

        if resultado.tipo_documento == TipoDocumento.desconhecido:
            return {}, "Recebi! ", False, "desconhecido"

        dados_cliente = resultado.para_dados_cliente()
        campos = ", ".join(dados_cliente.keys())

        if resultado.tipo_documento == TipoDocumento.rg:
            msg = f"Recebi seu RG!  Consegui ler: {campos}."
        elif resultado.tipo_documento == TipoDocumento.cpf:
            msg = f"Recebi seu CPF!  Consegui ler: {campos}."
        elif resultado.tipo_documento == TipoDocumento.comprovante_endereco:
            msg = f"Recebi seu comprovante!  Consegui ler: {campos}."
        else:
            msg = "Recebi! "

        return dados_cliente, msg, False, tipo_str

    except Exception as e:
        logger.exception("Erro ao processar mídia OCR")
        return {}, "Recebi! Vou processar sua imagem. ", True, "desconhecido"
