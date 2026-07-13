"""Validação de dados do cliente: CPF, CEP, telefone, e-mail, RG e campos obrigatórios.

Usada pelo agente validador antes de gerar documentos.
"""
import re
from datetime import datetime

# Campos obrigatórios por tipo de benefício
CAMPOS_OBRIGATORIOS = {
    "incapacidade": ["nome", "cpf", "rg", "logradouro", "cidade", "uf"],
    "idade_rural": [
        "nome", "cpf", "rg", "logradouro", "cidade", "uf",
        "data_nascimento", "nacionalidade",
    ],
    "revisao": ["nome", "cpf", "rg", "logradouro", "cidade", "uf"],
    "pensao": ["nome", "cpf", "rg", "logradouro", "cidade", "uf", "data_nascimento"],
}


def validar_cpf(cpf: str) -> bool:
    """Valida CPF verificando dígitos verificadores (algoritmo oficial).

    O CPF deve ter exatamente 11 dígitos numéricos (sem formatação).
    Use validar_cpf_formatado() para CPFs com pontuação.

    Algoritmo:
      1. Multiplica cada um dos 9 primeiros dígitos por pesos 10..2
      2. Soma tudo, tira resto da divisão por 11
      3. Se resto < 2, dígito = 0; senão dígito = 11 - resto
      4. Repete para o segundo dígito com pesos 11..2
    """
    if not cpf or not cpf.isdigit():
        return False
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False
    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(cpf[i]) != digito:
            return False
    return True


def validar_cpf_formatado(cpf: str) -> bool:
    """Valida CPF removendo formatação (pontos e traços)."""
    return validar_cpf(re.sub(r"\D", "", cpf))


def validar_cep(cep: str) -> bool:
    """CEP deve ter 8 dígitos numéricos."""
    if not cep:
        return False
    return len(re.sub(r"\D", "", cep)) == 8


def validar_telefone(telefone: str) -> bool:
    """Telefone deve ter entre 10 e 11 dígitos (DDD + número)."""
    if not telefone:
        return False
    return 10 <= len(re.sub(r"\D", "", telefone)) <= 11


def validar_email(email: str) -> bool:
    """Valida formato básico de e-mail (user@domínio.tld)."""
    if not email:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def validar_data(data: str, fmt: str = "%d/%m/%Y") -> bool:
    """Verifica se a string corresponde a uma data válida.

    Tenta múltiplos formatos: dd/mm/aaaa, dd-mm-aaaa, dd/mm/aa, dd-mm-aa.
    """
    if not data:
        return False
    formatos = [fmt, "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"]
    for f in formatos:
        try:
            datetime.strptime(data, f)
            return True
        except ValueError:
            continue
    return False


def validar_rg(rg: str) -> bool:
    """RG deve ter ao menos 4 caracteres (validação mínima)."""
    return bool(rg and rg.strip() and len(rg.strip()) >= 4)


def validar_dados(dados: dict, tipo_beneficio: str = "outro") -> dict:
    """Valida todos os dados do cliente e retorna resultado estruturado.

    Args:
        dados: dicionário com campos do cliente.
        tipo_beneficio: usado para determinar campos obrigatórios.

    Returns:
        dict com chaves: valido, inconsistencias, campos_faltantes.
    """
    inconsistencias: list[str] = []
    campos_faltantes: list[str] = []

    # Valida campos obrigatórios para o tipo de benefício
    campos_obrig = CAMPOS_OBRIGATORIOS.get(tipo_beneficio, ["nome", "cpf", "rg"])
    for campo in campos_obrig:
        if campo == "uf" and dados.get("cidade"):
            continue
        valor = dados.get(campo)
        if not valor or (isinstance(valor, str) and not valor.strip()):
            campos_faltantes.append(campo)

    # Valida CPF (só verifica formato se preenchido; campo faltante já capturado acima)
    cpf = dados.get("cpf", "")
    if cpf and not validar_cpf_formatado(cpf):
        inconsistencias.append(f"CPF inválido: {cpf}")

    # Validações condicionais (só falham se o campo foi preenchido)
    validacoes_condicionais = [
        ("cep", validar_cep, "CEP inválido: {}"),
        ("telefone", validar_telefone, "Telefone inválido: {}"),
        ("email", validar_email, "E-mail inválido: {}"),
        ("rg", validar_rg, "RG inválido: {}"),
    ]
    for campo, func, msg in validacoes_condicionais:
        valor = dados.get(campo, "")
        if valor and not func(valor):
            inconsistencias.append(msg.format(valor))

    # Valida data de nascimento se for string
    data_nasc = dados.get("data_nascimento", "")
    if data_nasc and isinstance(data_nasc, str) and not validar_data(data_nasc):
        inconsistencias.append(f"Data de nascimento inválida: {data_nasc}")

    return {
        "valido": len(inconsistencias) == 0 and len(campos_faltantes) == 0,
        "inconsistencias": inconsistencias,
        "campos_faltantes": campos_faltantes,
    }
