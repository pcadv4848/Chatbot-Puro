from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SessionStatus(Enum):
    CLASSIFICANDO = "classificando"
    CONFIRMANDO = "confirmando"
    COLETANDO_DADOS = "coletando_dados"
    AGUARDANDO_DOC = "aguardando_doc"
    GERANDO = "gerando"
    CONCLUIDO = "concluido"
    PAUSADO = "pausado"
    FORA_ESCOPO = "fora_escopo"
    ARQUIVADO = "arquivado"
    REVISAO_ADVOGADO = "revisao_advogado"
    TRAFEGO_PAGO = "trafego_pago"
    AGUARDANDO_ADVOGADO = "aguardando_advogado"


@dataclass
class SessionState:
    whatsapp_id: str
    status: SessionStatus = SessionStatus.CLASSIFICANDO
    tipo_beneficio: Optional[str] = None
    esfera: Optional[str] = None
    dados_cliente: dict = field(default_factory=dict)
    documentos_recebidos: list = field(default_factory=list)
    documentos_faltantes: list = field(default_factory=list)
    conversa: list = field(default_factory=list)
    step: int = 0
    ultima_atividade: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ocr_retry_count: int = 0
    motivo_pausa: Optional[str] = None
    documentos_gerados: list = field(default_factory=list)
    rascunho_rural_text: Optional[str] = None
    periodos_trabalho_rural: list = field(default_factory=list)
    processed_message_ids: list = field(default_factory=list)
    trafego_pago: bool = False
    resumo_caso: str = ""
    historico_perguntas: list = field(default_factory=list)
    simplify_mode: bool = False
    human_attending: bool = False
    existing_client: bool = False
    reminder_count: int = 0
