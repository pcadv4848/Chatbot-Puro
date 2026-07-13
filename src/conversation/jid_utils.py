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
