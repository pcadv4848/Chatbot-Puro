"""Schemas Pydantic compartilhados entre agentes e tools."""
from pydantic import BaseModel, Field
from typing import Optional, Literal


class Classificacao(BaseModel):
    """Resultado da classificação do tipo de benefício que o cliente precisa."""

    tipo: Literal["incapacidade", "idade_rural", "revisao", "pensao", "outro"]
    esfera: Literal["adm", "judicial"]
    sub_tipo: Optional[str] = None
    docs_necessarios: list[str] = Field(default_factory=list)
    confianca: float = 0.0
