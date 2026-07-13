"""Utilitários de criptografia para proteção de dados sensíveis em repouso.

Usa Fernet (AES-128-CBC + HMAC-SHA256) via cryptography.
Chave deve ser gerada com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import json
import logging

import base64

from cryptography.fernet import Fernet, InvalidToken
from src.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_warned_no_key = False


def _hex_to_fernet_key(hex_key: str) -> bytes:
    """Converte uma chave hex de 32 bytes para o formato base64 Fernet."""
    raw = bytes.fromhex(hex_key.strip())
    return base64.urlsafe_b64encode(raw)

CAMPOS_SENSIVEIS = {
    "nome", "cpf", "rg", "telefone", "email",
    "logradouro", "numero", "complemento", "bairro",
    "cep", "cidade", "uf", "data_nascimento",
}


def _get_fernet() -> Fernet | None:
    global _fernet, _warned_no_key
    if _fernet is None:
        if not settings.encrypt_key:
            if not _warned_no_key:
                logger.warning("ENCRYPT_KEY não configurada — dados sensíveis NÃO serão criptografados!")
                _warned_no_key = True
            return None
        try:
            key = settings.encrypt_key.encode() if isinstance(settings.encrypt_key, str) else settings.encrypt_key
            if not key.startswith(b"gAAAAA"):
                key = _hex_to_fernet_key(settings.encrypt_key)
            _fernet = Fernet(key)
        except Exception as e:
            logger.error("Falha ao inicializar Fernet (chave inválida?): %s", e)
            _fernet = None
    return _fernet


def encrypt_dict(data: dict) -> dict:
    """Criptografa campos sensíveis de um dicionário.

    Campos não sensíveis permanecem inalterados.
    Se a chave não estiver configurada, retorna os dados originais (fallback seguro).
    """
    f = _get_fernet()
    if f is None:
        return data

    encrypted = {}
    for k, v in data.items():
        if k in CAMPOS_SENSIVEIS and isinstance(v, str) and v:
            try:
                encrypted[k] = f.encrypt(v.encode()).decode()
            except Exception as e:
                logger.error("Erro ao criptografar campo %s: %s", k, e)
                encrypted[k] = v
        else:
            encrypted[k] = v
    return encrypted


def encrypt_value(valor: str) -> str:
    """Criptografa um valor string individual com Fernet.
    Retorna o valor original se a chave não estiver configurada."""
    f = _get_fernet()
    if f is None or not valor:
        return valor
    return f.encrypt(valor.encode()).decode()


def decrypt_value(valor: str) -> str:
    """Descriptografa um valor string individual com Fernet.
    Detecta automaticamente valores criptografados pelo prefixo 'gAAAAA'.
    Retorna o valor original se não estiver criptografado ou a chave não estiver configurada."""
    if not isinstance(valor, str) or not valor.startswith("gAAAAA"):
        return valor
    f = _get_fernet()
    if f is None:
        return valor
    try:
        return f.decrypt(valor.encode()).decode()
    except InvalidToken:
        logger.error("Token inválido ao descriptografar valor")
        return valor
    except Exception as e:
        logger.error("Erro ao descriptografar valor: %s", e)
        return valor


def encrypt_json(valor: list | dict) -> str:
    """Criptografa uma estrutura JSON (dict ou list) como string.
    Retorna o JSON original codificado se a chave não estiver configurada."""
    f = _get_fernet()
    raw = json.dumps(valor, ensure_ascii=False, default=str)
    if f is None:
        return raw
    return f.encrypt(raw.encode()).decode()


def decrypt_json(valor: str) -> list | dict:
    """Descriptografa uma string JSON previamente criptografada.
    Retorna o valor decodificado, ou estrutura vazia em caso de erro."""
    if not isinstance(valor, str) or not valor.startswith("gAAAAA"):
        return valor
    f = _get_fernet()
    if f is None:
        return valor
    try:
        raw = f.decrypt(valor.encode()).decode()
        return json.loads(raw)
    except (InvalidToken, json.JSONDecodeError) as e:
        logger.error("Erro ao descriptografar JSON: %s", e)
        return {}
    except Exception as e:
        logger.error("Erro ao descriptografar JSON: %s", e)
        return {}


def decrypt_dict(data: dict) -> dict:
    """Descriptografa campos sensíveis de um dicionário.

    Detecta automaticamente valores criptografados pelo prefixo 'gAAAAA' do Fernet.
    Se a chave não estiver configurada, retorna os dados originais.
    """
    f = _get_fernet()
    if f is None:
        return data

    decrypted = {}
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("gAAAAA"):
            try:
                decrypted[k] = f.decrypt(v.encode()).decode()
            except InvalidToken:
                logger.error("Token inválido ao descriptografar campo %s", k)
                decrypted[k] = v
            except Exception as e:
                logger.error("Erro ao descriptografar campo %s: %s", k, e)
                decrypted[k] = v
        else:
            decrypted[k] = v
    return decrypted
