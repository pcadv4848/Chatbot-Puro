"""Serviço de OCR com fallback: Tesseract → regex.

Pipeline:
  imagem → Tesseract local → [falha] → regex

Tesseract é o backend primário (local, gratuito, offline).
Regex é o fallback final para padrões simples (CPF, RG, CEP).
"""
import logging
import re
from functools import lru_cache
from typing import Optional

from src.agents.schemas_ocr import (
    DadosCPF,
    DadosEndereco,
    DadosRG,
    ResultadoOCR,
    TipoDocumento,
)

logger = logging.getLogger(__name__)

try:
    from src.services.ocr_tesseract import extrair_texto_sync as extrair_texto_tesseract
    _TESSERACT_DISPONIVEL = True
except ImportError:
    extrair_texto_tesseract = lambda b: ""
    _TESSERACT_DISPONIVEL = False


RE_CPF = re.compile(r"(\d{3}\.?\d{3}\.?\d{3}-?\d{2})")
RE_RG = re.compile(r"(\d{1,2}\.?\d{3}\.?\d{3}-?[\dxX])")
RE_DATA = re.compile(r"(\d{2}/\d{2}/\d{4})")
RE_CEP = re.compile(r"(\d{5}-?\d{3})")


def tesseract_disponivel() -> bool:
    """Verifica se Tesseract OCR local está disponível."""
    if not _TESSERACT_DISPONIVEL:
        return False
    from src.services.ocr_tesseract import tesseract_disponivel as _check
    return _check()


async def extrair_texto(imagem_bytes: bytes) -> str:
    """Extrai texto bruto de uma imagem via Tesseract.

    Tenta Tesseract OCR local primeiro.
    Se falhar ou não estiver disponível, retorna vazio
    (os parsers regex tentarão extrair padrões mesmo assim).

    Args:
        imagem_bytes: conteúdo binário da imagem (JPEG, PNG, etc.)

    Returns:
        Texto extraído, ou string vazia se falhar.
    """
    if tesseract_disponivel():
        try:
            texto = extrair_texto_tesseract(imagem_bytes)
            if texto:
                return texto
            logger.info("Tesseract retornou texto vazio")
        except Exception as e:
            logger.warning("Tesseract OCR falhou: %s", e)

    if not tesseract_disponivel():
        logger.info(
            "Tesseract não disponível — instalando padrões simples via regex. "
            "Para melhor resultado: apt install tesseract-ocr tesseract-ocr-por"
        )

    return ""


async def extrair_dados_rg(imagem_bytes: bytes) -> ResultadoOCR:
    texto = await extrair_texto(imagem_bytes)
    resultado = _parse_texto_para_rg(texto)
    resultado.texto_bruto = texto
    return resultado


async def extrair_dados_cpf(imagem_bytes: bytes) -> ResultadoOCR:
    texto = await extrair_texto(imagem_bytes)
    resultado = _parse_texto_para_cpf(texto)
    resultado.texto_bruto = texto
    return resultado


async def extrair_endereco(imagem_bytes: bytes) -> ResultadoOCR:
    texto = await extrair_texto(imagem_bytes)
    resultado = _parse_texto_para_endereco(texto)
    resultado.texto_bruto = texto
    return resultado


async def extrair_dados_automatico(imagem_bytes: bytes) -> ResultadoOCR:
    """Tenta identificar automaticamente o tipo de documento e extrair dados.
    Ordem de tentativa: RG → CPF → comprovante de endereço → texto genérico."""
    texto = await extrair_texto(imagem_bytes)
    if not texto:
        return ResultadoOCR(texto_bruto="")

    texto_superior = texto[:500].upper()

    if _procurar("RG", texto_superior) or _procurar("IDENTIDADE", texto_superior) or _procurar("CARTEIRA DE IDENTIDADE", texto_superior):
        resultado = _parse_texto_para_rg(texto)
        resultado.tipo_documento = TipoDocumento.rg
        resultado.texto_bruto = texto
        return resultado

    if _procurar("CPF", texto_superior) and RE_CPF.search(texto):
        resultado = _parse_texto_para_cpf(texto)
        resultado.tipo_documento = TipoDocumento.cpf
        resultado.texto_bruto = texto
        return resultado

    if _procurar("CEP", texto_superior) or (_procurar("ENDEREÇO", texto_superior) and _procurar("BAIRRO", texto_superior)):
        resultado = _parse_texto_para_endereco(texto)
        resultado.tipo_documento = TipoDocumento.comprovante_endereco
        resultado.texto_bruto = texto
        return resultado

    resultado = _parse_texto_generico(texto)
    resultado.texto_bruto = texto
    return resultado


def _procurar(palavra: str, texto: str) -> bool:
    sem_acento = palavra.translate(str.maketrans("ÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇ", "AAAAAEEEEIIIIOOOOOUUUUC"))
    texto_normalizado = texto.translate(str.maketrans("ÁÀÃÂÄÉÈÊËÍÌÎÏÓÒÕÔÖÚÙÛÜÇ", "AAAAAEEEEIIIIOOOOOUUUUC"))
    return sem_acento in texto_normalizado


# ═══════════════════════════════════════════════════════════
#  Parsers fallback (regex)
# ═══════════════════════════════════════════════════════════

def _extrair_linha_apos(rotulo: str, texto: str) -> Optional[str]:
    for linha in texto.split("\n"):
        if rotulo.lower() in linha.lower():
            partes = re.split(r"[:]\s*", linha, maxsplit=1)
            if len(partes) > 1 and partes[1].strip():
                return partes[1].strip()
    return None


def _linha_contem(rotulo: str, texto: str) -> Optional[str]:
    for linha in texto.split("\n"):
        if rotulo.lower() in linha.lower():
            return re.sub(rf"(?i){re.escape(rotulo)}[\s:]*", "", linha).strip()
    return None


def _parse_texto_para_rg(texto: str) -> ResultadoOCR:
    dados = DadosRG()
    texto_upper = texto.upper()
    nome = _extrair_linha_apos("NOME", texto_upper) or _linha_contem("NOME", texto_upper)
    if nome and len(nome) > 5:
        dados.nome = nome.title()
    cpf_match = RE_CPF.search(texto)
    if cpf_match:
        dados.cpf = cpf_match.group(1)
    rg_match = RE_RG.search(texto)
    if rg_match:
        dados.rg = rg_match.group(1)
    for linha in texto.split("\n"):
        campos = re.split(r"[\s:]{2,}|:\s*", linha.strip(), maxsplit=1)
        if len(campos) == 2:
            rotulo, valor = campos
            if "SSP" in rotulo.upper() or "DETRAN" in rotulo.upper() or "POLÍCIA" in rotulo.upper():
                dados.orgao_emissor = valor
                break
            if "ORGÃO" in rotulo.upper() or "ORGAO" in rotulo.upper():
                dados.orgao_emissor = valor
                break
    if not dados.orgao_emissor:
        for linha in texto.split("\n"):
            if "SSP" in linha or "DETRAN" in linha or "POLÍCIA" in linha:
                dados.orgao_emissor = linha.strip()
                break
    data_match = RE_DATA.search(texto)
    if data_match:
        dados.data_nascimento = data_match.group(1)
    pai = _linha_contem("FILIAÇÃO", texto_upper) or _linha_contem("FILIACAO", texto_upper)
    if pai:
        if "/" in pai:
            partes = pai.split("/")
            dados.filiacao_pai = partes[0].strip()
            dados.filiacao_mae = partes[1].strip() if len(partes) > 1 else None
        else:
            dados.filiacao_pai = pai
    return ResultadoOCR(tipo_documento=TipoDocumento.rg, dados_rg=dados)


def _parse_texto_para_cpf(texto: str) -> ResultadoOCR:
    dados = DadosCPF()
    texto_upper = texto.upper()
    nome = _extrair_linha_apos("NOME", texto_upper) or _linha_contem("NOME", texto_upper)
    if nome and len(nome) > 5:
        dados.nome = nome.title()
    cpf_match = RE_CPF.search(texto)
    if cpf_match:
        dados.cpf = cpf_match.group(1)
    data_match = RE_DATA.search(texto)
    if data_match:
        dados.data_nascimento = data_match.group(1)
    return ResultadoOCR(tipo_documento=TipoDocumento.cpf, confianca_media=0.7, dados_cpf=dados)


def _parse_texto_para_endereco(texto: str) -> ResultadoOCR:
    dados = DadosEndereco()
    texto_upper = texto.upper()
    cep_match = RE_CEP.search(texto)
    if cep_match:
        dados.cep = cep_match.group(1)
    rotulos_rua = ("RUA", "AVENIDA", "AV", "TRAVESSA", "PRACA", "LOGRADOURO", "ESTRADA")
    for linha in texto.split("\n"):
        upper = linha.strip().upper()
        if not upper:
            continue
        for rotulo in rotulos_rua:
            if re.match(rf"^{re.escape(rotulo)}\s*:", upper):
                _, _, valor = linha.partition(":")
                valor = valor.strip()
                if valor and len(valor) > 3:
                    dados.logradouro = linha.strip()
                break
        if dados.logradouro:
            break
    if not dados.logradouro:
        for linha in texto.split("\n"):
            upper = linha.strip().upper()
            for rotulo in rotulos_rua:
                if upper.startswith(rotulo) and not upper.startswith(rotulo + ":"):
                    dados.logradouro = linha.strip()
                    break
            if dados.logradouro:
                break
    bairro = _linha_contem("BAIRRO", texto_upper) or _linha_contem("DISTRITO", texto_upper)
    if bairro:
        dados.bairro = bairro
    for linha in texto.split("\n"):
        match = re.search(r"([A-ZÀ-Ú\s]+)\s*[-–/]\s*([A-Z]{2})", linha.upper())
        if match:
            cidade = match.group(1).strip()
            uf = match.group(2).strip()
            if len(uf) == 2 and len(cidade) > 3:
                dados.cidade = cidade.title()
                dados.uf = uf
                break
    if dados.logradouro:
        match_num = re.search(r"(?:n[º°]?\s*\.?\s*)\s*(\d+)", dados.logradouro)
        if not match_num:
            match_num = re.search(r",\s*(\d+)(?:\s*$|\s*-)", dados.logradouro)
        if not match_num:
            numbers = re.findall(r"\d+", dados.logradouro)
            if numbers:
                dados.numero = numbers[-1]
        else:
            dados.numero = match_num.group(1)
    return ResultadoOCR(tipo_documento=TipoDocumento.comprovante_endereco, dados_endereco=dados)


def _parse_texto_generico(texto: str) -> ResultadoOCR:
    dados_rg = DadosRG()
    dados_cpf = DadosCPF()
    dados_end = DadosEndereco()
    cpf_match = RE_CPF.search(texto)
    if cpf_match:
        dados_cpf.cpf = cpf_match.group(1)
    rg_match = RE_RG.search(texto)
    if rg_match:
        dados_rg.rg = rg_match.group(1)
    data_match = RE_DATA.search(texto)
    if data_match:
        dados_rg.data_nascimento = data_match.group(1)
        dados_cpf.data_nascimento = data_match.group(1)
    cep_match = RE_CEP.search(texto)
    if cep_match:
        dados_end.cep = cep_match.group(1)
    return ResultadoOCR(
        tipo_documento=TipoDocumento.desconhecido,
        confianca_media=0.3,
        dados_rg=dados_rg if any([dados_rg.rg, dados_rg.data_nascimento]) else None,
        dados_cpf=dados_cpf if dados_cpf.cpf else None,
        dados_endereco=dados_end if dados_end.cep else None,
    )


@lru_cache(maxsize=1)
def obter_prompt_estruturacao() -> str:
    """Prompt para Claude estruturar o texto bruto do OCR em JSON.
    Usado por extrair_ocr.py quando Claude está disponível."""
    return (
        "Você recebe o texto extraído por OCR de um documento brasileiro.\n"
        "Identifique o tipo de documento e extraia os campos solicitados.\n\n"
        "Tipo: RG (carteira de identidade)\n"
        "Campos: nome, cpf (se houver), rg, orgao_emissor, uf_rg, "
        "data_nascimento (dd/mm/aaaa), filiacao_pai, filiacao_mae, data_emissao\n\n"
        "Tipo: CPF (cadastro de pessoa física)\n"
        "Campos: nome, cpf, data_nascimento (dd/mm/aaaa)\n\n"
        "Tipo: Comprovante de residência\n"
        "Campos: logradouro, numero, complemento, bairro, cep, cidade, uf\n\n"
        "Retorne APENAS um JSON válido com a chave 'tipo_documento' "
        "(string: 'rg'|'cpf'|'comprovante_endereco'|'desconhecido') "
        "e os dados extraídos. Exemplo:\n"
        '{"tipo_documento": "rg", "dados": {"nome": "João Silva", '
        '"cpf": "123.456.789-00", "rg": "12.345.678-9", '
        '"data_nascimento": "15/03/1980"}}\n'
        "NÃO adicione texto antes ou depois do JSON."
    )
