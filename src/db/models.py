"""Modelos SQLAlchemy para persistência de dados.

Cliente, Sessao, Documento e ArquivoUpload.
"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Date, DateTime, JSON, ForeignKey, Uuid, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

from src.engine.crypto import _get_fernet


class EncryptedString(TypeDecorator):
    """Tipo SQLAlchemy que criptografa strings automaticamente.

    Usa Fernet (AES-128-CBC). Se ENCRYPT_KEY não estiver configurada,
    armazena em plaintext (fallback seguro com aviso em log).
    """
    impl = String

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        f = _get_fernet()
        if f is None:
            return value
        return f.encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("gAAAAA"):
            f = _get_fernet()
            if f is None:
                return value
            try:
                return f.decrypt(value.encode()).decode()
            except Exception:
                return value
        return value


class Base(DeclarativeBase):
    """Classe base para todos os modelos."""
    pass


class Cliente(Base):
    """Dados cadastrais do cliente.

    ATENÇÃO: Esta tabela não é mais populada ativamente.
    Os dados do cliente são armazenados em sessoes.estado_json
    com criptografia Fernet. Esta tabela existe para compatibilidade
    com migrações futuras.
    """
    __tablename__ = "clientes"

    id = Column(Uuid(), primary_key=True, default=uuid4)
    whatsapp_id = Column(String, unique=True, nullable=False, index=True)
    nome = Column(EncryptedString(255), nullable=False)
    estado_civil = Column(EncryptedString(50), nullable=True)
    profissao = Column(EncryptedString(100), nullable=True)
    cpf = Column(EncryptedString(255), nullable=False, index=True)
    rg = Column(EncryptedString(255), nullable=False)
    orgao_emissor = Column(EncryptedString(100), nullable=True)
    logradouro = Column(EncryptedString(255), nullable=True)
    numero = Column(EncryptedString(20), nullable=True)
    complemento = Column(EncryptedString(100), nullable=True)
    bairro = Column(EncryptedString(100), nullable=True)
    cep = Column(EncryptedString(20), nullable=True)
    cidade = Column(EncryptedString(100), nullable=True)
    uf = Column(EncryptedString(10), nullable=True)
    telefone = Column(EncryptedString(50), nullable=True)
    email = Column(EncryptedString(255), nullable=True)
    data_nascimento = Column(Date, nullable=True)
    nacionalidade = Column(EncryptedString(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Sessao(Base):
    """Estado atual da conversa de um cliente.

    Campos principais (queryáveis) são colunas dedicadas.
    O estado completo do SessionState é serializado em estado_json.
    """
    __tablename__ = "sessoes"

    id = Column(Uuid(), primary_key=True, default=uuid4)
    whatsapp_id = Column(String, unique=True, nullable=False, index=True)
    cliente_id = Column(
        Uuid(), ForeignKey("clientes.id"), nullable=True, index=True
    )
    status = Column(String, nullable=False, default="classificando", index=True)
    tipo_beneficio = Column(String, nullable=True)
    esfera = Column(String, nullable=True)
    ultima_atividade = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    estado_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Documento(Base):
    """Documento gerado (contrato, procuração, etc.)."""
    __tablename__ = "documentos"

    id = Column(Uuid(), primary_key=True, default=uuid4)
    cliente_id = Column(
        Uuid(), ForeignKey("clientes.id"), nullable=True, index=True
    )
    sessao_id = Column(
        Uuid(), ForeignKey("sessoes.id"), nullable=True, index=True
    )
    tipo_template = Column(String, nullable=False)
    status = Column(String, nullable=False, default="gerado", index=True)
    path_docx = Column(String, nullable=True)
    path_pdf = Column(String, nullable=True)
    zapsign_id = Column(String, nullable=True)
    assinado_em = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ArquivoUpload(Base):
    """Uploads de documentos enviados pelo cliente (fotos de RG, exames, etc.)."""
    __tablename__ = "arquivos_upload"

    id = Column(Uuid(), primary_key=True, default=uuid4)
    cliente_id = Column(
        Uuid(), ForeignKey("clientes.id"), nullable=True, index=True
    )
    sessao_id = Column(
        Uuid(), ForeignKey("sessoes.id"), nullable=True, index=True
    )
    tipo = Column(String, nullable=False)
    midia_id = Column(String, nullable=True)
    path_s3 = Column(String, nullable=True)
    dados_extraidos = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
