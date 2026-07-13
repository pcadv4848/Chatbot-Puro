"""Utilitários de processamento de texto: normalização, detecção de sim/não.

Extraído de supervisor.py para reduzir acoplamento.
"""
import re

from src.agents.constants import (
    MESES_PT, UF_MAP, PREFIXOS_NOME,
    PALAVRAS_SIM, PALAVRAS_NAO, SIM_NGRAMAS, NAO_NGRAMAS,
    PALAVRAS_NAO_AFIRMATIVAS,
)
from src.utils.text import expandir_girias, remover_acentos


def extrair_nome(texto: str) -> str:
    """Remove prefixos comuns e retorna apenas o nome."""
    t = texto.strip()
    t = PREFIXOS_NOME.sub("", t).strip()
    t = re.sub(r"\s*[,;].*$", "", t).strip()
    return t


def normalizar_data(texto: str) -> str | None:
    """Converte datas em diversos formatos para dd/mm/aaaa."""
    t = texto.strip()

    m = re.search(r"(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{2,4})", t)
    if m:
        d, mes, a = m.groups()
        if len(a) == 2:
            a = "20" + a if int(a) < 30 else "19" + a
        return f"{int(d):02d}/{int(mes):02d}/{a}"

    meses_alt = "|".join(MESES_PT)
    m = re.search(
        r"(\d{1,2})\s+de\s+(" + meses_alt + r")\s+de\s+(\d{2,4})",
        t, re.I,
    )
    if m:
        d, mes_txt, a = m.groups()
        mes = MESES_PT.get(mes_txt.lower())
        if mes:
            if len(a) == 2:
                a = "20" + a if int(a) < 30 else "19" + a
            return f"{int(d):02d}/{mes}/{a}"

    return None


def validar_cpf_digitos(digitos: str) -> bool:
    """Valida CPF pelo algoritmo dos dígitos verificadores (mod 11)."""
    if not re.match(r"^\d{11}$", digitos) or digitos == digitos[0] * 11:
        return False
    for j in (9, 10):
        soma = sum(int(digitos[i]) * (j + 1 - i) for i in range(j))
        resto = soma % 11
        esperado = 0 if resto < 2 else 11 - resto
        if int(digitos[j]) != esperado:
            return False
    return True


def normalizar_uf(texto: str) -> str:
    """Normaliza estado para sigla de 2 letras."""
    t = texto.strip().lower()
    if re.match(r"^[a-z]{2}$", t):
        return t.upper()
    if t in UF_MAP:
        return UF_MAP[t]
    for nome, sigla in UF_MAP.items():
        nome_curto = nome.split()[0]
        if t == nome_curto or nome == t:
            return sigla
    return texto.strip()


def verificar_sim(texto: str) -> bool:
    """Verifica se o texto é uma afirmação."""
    t_raw = texto.strip().lower()

    palavras_raw = t_raw.split()
    if len(palavras_raw) <= 2:
        for p in palavras_raw:
            if p in ("ta", "tá", "ok", "okay"):
                return True

    t = expandir_girias(t_raw)
    t = remover_acentos(t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()

    if t in SIM_NGRAMAS:
        return True

    palavras = t.split()
    for p in palavras:
        if p in ("sim", "ss", "s", "si", "y", "yes"):
            return True

    for p in palavras:
        if p in ("claro", "certamente", "confirmo", "afirmativo", "exato", "correto"):
            return True

    if "pode" in palavras:
        if PALAVRAS_NAO_AFIRMATIVAS & set(palavras):
            return False
        if any(p in ("sim", "ss") for p in palavras):
            return True
        if not any(p in ("nao", "não") for p in palavras):
            if len(palavras) <= 3:
                return True

    for termo in ("ok", "okay", "esta"):
        if termo in palavras:
            return True

    return False


def verificar_nao(texto: str) -> bool:
    """Verifica se o texto é uma negação."""
    t = texto.strip().lower()
    t = expandir_girias(t)
    t = remover_acentos(t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()

    if t in NAO_NGRAMAS:
        return True

    palavras = t.split()
    for p in palavras:
        if p in ("nao", "não"):
            return True

    if t in ("n", "no", "nope"):
        return True

    for p in palavras:
        if p in ("errado", "negativo", "nada", "nenhum", "nops"):
            return True

    return False


def sanitizar_id(id_: str) -> str:
    """Remove caracteres que poderiam causar path traversal."""
    return re.sub(r"[^\w\s\-]", "_", id_)
