"""Estado de sessão de cada cliente durante a conversa.

Usa dataclass para simplicidade inicial.
Futuramente será substituído por modelos SQLAlchemy para persistência em PostgreSQL.

Ciclo de vida do status:
  TRAFEGO_PAGO ──→ AGUARDANDO_ADVOGADO (human_attending=True)
       ↓
  CLASSIFICANDO ──→ AGUARDANDO_ADVOGADO (human_attending=True)
       ↓
  (FORA_ESCOPO) ──→ AGUARDANDO_ADVOGADO (human_attending=True)

  human_attending=True:
    - Bot fica MUDO (nao responde mais mensagens)
    - Apenas le a conversa para extrair dados (CPF, RG, etc.)
    - Quando tiver dados suficientes, gera documentos automaticamente
    - Humano e cliente conversam no mesmo numero sem interferencia do bot

Exemplo de uso:
  >>> sessao = SessionState(whatsapp_id="5511999999999")
  >>> sessao.status
  <SessionStatus.CLASSIFICANDO: 'classificando'>
  >>> sessao.status == SessionStatus.CLASSIFICANDO
  True
  >>> sessao.dados_cliente["nome"] = "João"
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SessionStatus(Enum):
    """Possíveis estados de uma sessão de atendimento."""

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
    """Representa o estado atual da conversa de um cliente no WhatsApp.

    Attributes:
        whatsapp_id: Identificador único do cliente no WhatsApp.
        status: Etapa atual do fluxo de atendimento.
        tipo_beneficio: Categoria do benefício (ex: incapacidade, revisao).
        esfera: Esfera de atuação — "adm" (administrativo) ou "judicial".
        dados_cliente: Dicionário com campos coletados (nome, cpf, rg, etc.).
        documentos_recebidos: IDs das mídias já enviadas pelo cliente.
        documentos_faltantes: Lista de documentos que ainda precisa enviar.
        conversa: Histórico da conversa [{role, content}, ...].
        step: Contador de interações (incrementado a cada mensagem).
        ultima_atividade: Timestamp da última interação (ISO format).
        ocr_retry_count: Quantas vezes o OCR foi tentado para a mídia atual.
        motivo_pausa: Razão pela qual a sessão foi pausada, se aplicável.
    """

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
    signing_url: Optional[str] = None
    zapsign_documento_id: Optional[str] = None
    documentos_gerados: list = field(default_factory=list)
    assinado_em: Optional[str] = None
    rascunho_rural_text: Optional[str] = None
    periodos_trabalho_rural: list = field(default_factory=list)
    processed_message_ids: list = field(default_factory=list)
    processed_zapsign_events: list = field(default_factory=list)
    trafego_pago: bool = True
    resumo_caso: str = ""
    historico_perguntas: list = field(default_factory=list)
    simplify_mode: bool = False
    human_attending: bool = False
    existing_client: bool = False
    """True se a sessão foi carregada do disco (cliente com histórico).
    Clientes existentes não recebem nenhuma mensagem do bot,
    a menos que um admin use RESETAR. para limpar o histórico."""
    reminder_count: int = 0
    """Quantas vezes o bot já enviou lembrete de abandono para esta sessão.
    Zera quando o cliente envia uma nova mensagem."""
