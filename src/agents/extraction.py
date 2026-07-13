import re

from src.conversation.state import SessionState
from src.agents.constants import (
    PADROES_CAMPO, VALIDAR_CAMPO, PREFIXOS_RUA, NACIONALIDADES,
    CIVIS, PROF_PALAVRAS,
)
from src.agents.text_utils import (
    normalizar_data, normalizar_uf, extrair_nome, validar_cpf_digitos,
)
from src.agents.tools.validar import validar_dados


MAPEAMENTO_CAMPOS = {
    "contratante": "nome",
    "outorgante": "nome",
    "estado civil": "estado_civil",
    "profissão": "profissao",
    "profissao": "profissao",
    "e-mail": "email",
    "email": "email",
    "telefone": "telefone",
    "cpf": "cpf",
    "rg": "rg",
    "rua": "logradouro",
    "endereço": "logradouro",
    "endereco": "logradouro",
    "nº": "numero",
    "bairro": "bairro",
    "cep": "cep",
    "data de nascimento": "data_nascimento",
    "nascimento": "data_nascimento",
    "cidade e estado": "cidade_estado",
    "cidade/estado": "cidade_estado",
    "nacionalidade": "nacionalidade",
    "local": "local_assinatura",
    "data": "data_assinatura",
}


def identificar_campo(texto_anterior: str) -> str | None:
    texto = texto_anterior.lower().rstrip(",:;–—-— ")
    for chave, campo in MAPEAMENTO_CAMPOS.items():
        if chave in texto:
            return campo
    if texto in ("n", "no."):
        return "numero"
    return None


def parecer_dado(texto: str) -> bool:
    t = texto.strip().lower()

    if "?" in t:
        return False

    exatos = {"olá", "ola", "oi", "bom dia", "boa tarde", "boa noite",
              "ok", "sim", "não", "nao", "obrigado", "obrigada",
              "entendi", "ah", "hm", "hum", "tá", "ta", "blz",
              "beleza", "certinho", "okay", "tudo bem", "tudo bom"}
    if t.strip(".,! ") in exatos:
        return False

    padroes_humano = [
        "pode me enviar", "me enviar", "manda", "mande", "encaminha",
        "qual seu", "qual sua", "qual o", "qual a", "quais",
        "preciso que", "você pode", "voce pode", "pode me",
        "já vou", "vou preparar", "preparei", "recebi",
        "aguarde", "um momento", "só um", "só mais",
        "pronto", "tudo certo", "está pronto", "esta pronto",
        "vou encaminhar", "segue", "segue em", "em anexo",
    ]
    for p in padroes_humano:
        if p in t:
            return False

    if len(t) > 100:
        return False

    if not re.search(r'[a-zA-Z0-9\u00C0-\u00FF]{2,}', t):
        return False

    return True


def detectar_campo(texto: str, campos_faltando: list[str]) -> str | None:
    t = texto.strip().lower()

    if normalizar_data(texto) and "data_nascimento" in campos_faltando:
        return "data_nascimento"

    if re.match(r"^[a-zA-Z]{2}$", t) and "uf" in campos_faltando:
        return "uf"

    uf_norm = normalizar_uf(texto)
    if uf_norm != texto.strip() and "uf" in campos_faltando:
        return "uf"

    if t.startswith(PREFIXOS_RUA) and "logradouro" in campos_faltando:
        return "logradouro"

    if any(n in t for n in NACIONALIDADES) and "nacionalidade" in campos_faltando:
        return "nacionalidade"

    tel_digits = re.sub(r"\D", "", t)
    if re.match(r"^\d{11}$", tel_digits) and "cpf" in campos_faltando:
        if validar_cpf_digitos(tel_digits):
            return "cpf"

    if re.match(r"^\d{1,2}\.\d{3}\.\d{3}[\-\s]?[\d\w]$", t) and "rg" in campos_faltando:
        return "rg"

    rg_clean = re.sub(r"\D", "", t)
    if 7 <= len(rg_clean) <= 10 and "rg" in campos_faltando:
        return "rg"

    if re.match(r"^\d+$", t) and "numero" in campos_faltando:
        return "numero"

    if 10 <= len(tel_digits) <= 11 and "telefone" in campos_faltando:
        return "telefone"

    if "estado_civil" in campos_faltando:
        palavras = set(t.split())
        if palavras & CIVIS:
            return "estado_civil"

    if "profissao" in campos_faltando:
        palavras = set(t.split())
        if palavras & PROF_PALAVRAS:
            return "profissao"

    return None


def extrair_e_salvar_campo(texto: str, sessao: SessionState):
    for campo, padrao in PADROES_CAMPO.items():
        match = padrao.search(texto)
        if match and campo not in sessao.dados_cliente:
            valor = match.group(1).strip()
            if campo == "data_nascimento":
                data_norm = normalizar_data(valor)
                if data_norm:
                    valor = data_norm
            if campo == "cpf":
                valor = re.sub(r"\D", "", valor)
            sessao.dados_cliente[campo] = valor

    field_pairs = list(re.finditer(
        r"(?:^|\s)([\w\sçãõáéíóúàâêôñü]+?)\s*[:=−]\s*"
        r"(.+?)(?=\s+[\w\sçãõáéíóúàâêôñü]+\s*[:=−]|$)",
        texto, re.I,
    ))
    if field_pairs:
        for match in field_pairs:
            label = match.group(1).strip().lower().rstrip(".")
            valor = match.group(2).strip()
            campo = identificar_campo(label)
            if campo and campo not in sessao.dados_cliente:
                if campo == "data_nascimento":
                    data_norm = normalizar_data(valor)
                    if data_norm:
                        valor = data_norm
                elif campo == "uf":
                    valor = normalizar_uf(valor)
                sessao.dados_cliente[campo] = valor
        return

    from src.agents.tools.validar import validar_dados
    campos_faltando = []
    if sessao.tipo_beneficio:
        resultado = validar_dados(sessao.dados_cliente, sessao.tipo_beneficio)
        campos_faltando = resultado["campos_faltantes"]
    if campos_faltando:
        texto_limpo = texto.strip().rstrip(".,!?;:")
        prox = detectar_campo(texto_limpo, campos_faltando)
        if prox is None:
            if not parecer_dado(texto_limpo):
                return
            prox = campos_faltando[0]

        if prox in sessao.dados_cliente:
            return

        if prox == "numero" and not re.match(r"^\d+\s*$", texto_limpo):
            return

        if prox == "nome":
            texto_limpo = extrair_nome(texto_limpo)
            if len(texto_limpo) < 2:
                return
        elif prox == "data_nascimento":
            data_norm = normalizar_data(texto_limpo)
            if data_norm:
                texto_limpo = data_norm
            else:
                return
        elif prox == "uf":
            texto_limpo = normalizar_uf(texto_limpo)
        elif prox in VALIDAR_CAMPO:
            validador = VALIDAR_CAMPO[prox]
            if not validador(texto_limpo):
                return

        sessao.dados_cliente[prox] = texto_limpo
