"""Filtro de logging que redaciona tokens de API e dados sensíveis.

Previne vazamento de credenciais e PII em logs de erro.
Aplicado ao root logger na inicialização do app.
"""
import logging
import re


class DadosSensiveisFilter(logging.Filter):
    """Redaciona tokens de API e dados sensíveis em mensagens de log."""

    PADROES: list[tuple[re.Pattern, str]] = [
        (
            re.compile(r"(Authorization|Bearer|X-API-Key|api_key)[:\s]+\S+", re.I),
            r"\1: [REDACTED]",
        ),
        (
            re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
            "[CPF REDACTED]",
        ),
        (
            re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}\b"),
            "[TELEFONE REDACTED]",
        ),
    ]

    def _sanitizar_str(self, texto: str) -> str:
        for padrao, substituicao in self.PADROES:
            texto = padrao.sub(substituicao, texto)
        return texto

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitizar_str(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._sanitizar_str(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitizar_str(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True
