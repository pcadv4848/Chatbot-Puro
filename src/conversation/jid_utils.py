import re


def session_key(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


def extrair_whatsapp_id(from_field: str) -> str:
    return from_field.replace("@c.us", "").replace("@s.whatsapp.net", "")


def normalizar_id(whatsapp_id: str) -> str:
    if "@" in whatsapp_id:
        return whatsapp_id
    return f"{whatsapp_id}@c.us"


def raw_number(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


def mesmo_telefone(a: str, b: str) -> bool:
    """Compara dois números de telefone ignorando código de país (55) e formatação.
    
    Extrai apenas dígitos de ambos e verifica se o menor é sufixo do maior.
    Ex: mesmo_telefone("75999903859", "5575999903859") → True
    """
    dig_a = re.sub(r"\D", "", a)
    dig_b = re.sub(r"\D", "", b)
    if not dig_a or not dig_b:
        return False
    if len(dig_a) >= len(dig_b):
        return dig_a.endswith(dig_b)
    return dig_b.endswith(dig_a)
