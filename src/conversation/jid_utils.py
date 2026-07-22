import re


def session_key(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


def extrair_whatsapp_id(from_field: str) -> str:
    return from_field.replace("@c.us", "").replace("@s.whatsapp.net", "").replace("@lid", "")


def normalizar_id(whatsapp_id: str) -> str:
    if "@" in whatsapp_id:
        return whatsapp_id
    return f"{whatsapp_id}@c.us"


def raw_number(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


def normalizar_br(numero: str) -> str:
    """Remove o digito 9 movel extra de numeros brasileiros (55 + DDD + 9).

    Ex: normalizar_br('5575999903859') -> '557599903859'
        normalizar_br('557599903859') -> '557599903859'
    """
    dig = re.sub(r"\D", "", numero)
    if len(dig) == 13 and dig.startswith("55"):
        dig = dig[:4] + dig[5:]
    return dig


def mesmo_telefone(a: str, b: str) -> bool:
    """Compara dois números de telefone ignorando código de país (55) e formatação.

    Normaliza ambos (remove 9 móvel extra BR) e depois remove o prefixo 55
    de cada um para comparação exata do DDD + número.
    Ex: mesmo_telefone("7599903859", "557599903859") → True
    """
    dig_a = normalizar_br(a)
    dig_b = normalizar_br(b)
    if not dig_a or not dig_b:
        return False
    if dig_a.startswith("55") and len(dig_a) > 2:
        dig_a = dig_a[2:]
    if dig_b.startswith("55") and len(dig_b) > 2:
        dig_b = dig_b[2:]
    return dig_a == dig_b
