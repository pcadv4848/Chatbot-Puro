"""Schemas Pydantic para dados extraídos por OCR de documentos brasileiros.

Suporta três tipos de documento:
  - RG (frente/verso): nome, CPF, RG, filiação, data de nascimento
  - CPF: nome, CPF, data de nascimento
  - Comprovante de residência: endereço completo
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TipoDocumento(str, Enum):
    rg = "rg"
    cpf = "cpf"
    comprovante_endereco = "comprovante_endereco"
    desconhecido = "desconhecido"


class DadosRG(BaseModel):
    nome: Optional[str] = None
    cpf: Optional[str] = None
    rg: Optional[str] = None
    orgao_emissor: Optional[str] = None
    uf_rg: Optional[str] = None
    data_nascimento: Optional[str] = None
    filiacao_pai: Optional[str] = None
    filiacao_mae: Optional[str] = None
    data_emissao: Optional[str] = None


class DadosCPF(BaseModel):
    nome: Optional[str] = None
    cpf: Optional[str] = None
    data_nascimento: Optional[str] = None


class DadosEndereco(BaseModel):
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cep: Optional[str] = None
    cidade: Optional[str] = None
    uf: Optional[str] = None


class ResultadoOCR(BaseModel):
    tipo_documento: TipoDocumento = TipoDocumento.desconhecido
    confianca_media: float = 0.0
    texto_bruto: str = ""
    dados_rg: Optional[DadosRG] = None
    dados_cpf: Optional[DadosCPF] = None
    dados_endereco: Optional[DadosEndereco] = None

    def para_dados_cliente(self) -> dict:
        """Converte os dados extraídos para o formato do dicionário dados_cliente."""
        dados = {}
        if self.dados_rg:
            if self.dados_rg.nome:
                dados["nome"] = self.dados_rg.nome
            if self.dados_rg.cpf:
                dados["cpf"] = self.dados_rg.cpf
            if self.dados_rg.rg:
                dados["rg"] = self.dados_rg.rg
            if self.dados_rg.data_nascimento:
                dados["data_nascimento"] = self.dados_rg.data_nascimento
        if self.dados_cpf:
            if self.dados_cpf.nome and "nome" not in dados:
                dados["nome"] = self.dados_cpf.nome
            if self.dados_cpf.cpf and "cpf" not in dados:
                dados["cpf"] = self.dados_cpf.cpf
            if self.dados_cpf.data_nascimento and "data_nascimento" not in dados:
                dados["data_nascimento"] = self.dados_cpf.data_nascimento
        if self.dados_endereco:
            if self.dados_endereco.logradouro:
                dados["logradouro"] = self.dados_endereco.logradouro
            if self.dados_endereco.numero:
                dados["numero"] = self.dados_endereco.numero
            if self.dados_endereco.bairro:
                dados["bairro"] = self.dados_endereco.bairro
            if self.dados_endereco.cep:
                dados["cep"] = self.dados_endereco.cep
            if self.dados_endereco.cidade:
                dados["cidade"] = self.dados_endereco.cidade
            if self.dados_endereco.uf:
                dados["uf"] = self.dados_endereco.uf
        return dados
