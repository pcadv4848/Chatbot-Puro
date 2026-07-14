"""Persistência de sessões — dual mode: PostgreSQL (primário) + JSON (fallback).

Fluxo:
  1. Tenta DB primeiro (async via SQLAlchemy)
  2. Se DB indisponível, cai no JSON file storage (sync)
  3. Sessões são mantidas em memória (sessoes_ativas) para acesso rápido
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, and_
from src.conversation.state import SessionState, SessionStatus
from src.config import settings
from src.engine.crypto import encrypt_dict, decrypt_dict, encrypt_json, decrypt_json, _get_fernet
from src.db.models import Sessao as SessaoModel
from src.db.session import async_session

logger = logging.getLogger(__name__)

# ── Fallback JSON ──
STORAGE_DIR = Path(__file__).parent.parent.parent / "data" / "sessoes"
LOCK = threading.Lock()
ARCHIVE_AFTER_SECONDS = (settings.session_archive_days or 7) * 24 * 3600


def _caminho_sessao(whatsapp_id: str) -> Path:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = whatsapp_id.split("@")[0] if "@" in whatsapp_id else whatsapp_id
    return STORAGE_DIR / f"sessao_{normalized}.json"


def _serializar_json(sessao: SessionState) -> dict:
    dados = {
        "whatsapp_id": sessao.whatsapp_id,
        "status": sessao.status.value,
        "tipo_beneficio": sessao.tipo_beneficio,
        "esfera": sessao.esfera,
        "dados_cliente": sessao.dados_cliente,
        "documentos_recebidos": sessao.documentos_recebidos,
        "documentos_faltantes": sessao.documentos_faltantes,
        "conversa": sessao.conversa,
        "step": sessao.step,
        "ultima_atividade": sessao.ultima_atividade,
        "ocr_retry_count": sessao.ocr_retry_count,
        "motivo_pausa": sessao.motivo_pausa,
        "signing_url": sessao.signing_url,
        "zapsign_documento_id": sessao.zapsign_documento_id,
        "documentos_gerados": sessao.documentos_gerados,
        "assinado_em": sessao.assinado_em,
        "rascunho_rural_text": sessao.rascunho_rural_text,
        "periodos_trabalho_rural": sessao.periodos_trabalho_rural,
        "processed_message_ids": sessao.processed_message_ids,
        "processed_zapsign_events": sessao.processed_zapsign_events,
        "trafego_pago": sessao.trafego_pago,
        "resumo_caso": sessao.resumo_caso,
        "historico_perguntas": sessao.historico_perguntas,
        "simplify_mode": sessao.simplify_mode,
        "human_attending": sessao.human_attending,
        "reminder_count": sessao.reminder_count,
    }
    if dados["dados_cliente"]:
        dados["dados_cliente"] = encrypt_dict(dados["dados_cliente"])
    f = _get_fernet()
    if f is not None and sessao.conversa:
        dados["conversa"] = encrypt_json(sessao.conversa)
    return dados


def _desserializar_json(dados: dict) -> SessionState:
    dados_cliente = dados.get("dados_cliente", {})
    if dados_cliente:
        dados_cliente = decrypt_dict(dados_cliente)
    conversa_raw = dados.get("conversa", [])
    if isinstance(conversa_raw, str) and conversa_raw.startswith("gAAAAA"):
        conversa = decrypt_json(conversa_raw)
    else:
        conversa = conversa_raw or []
    status_raw = dados.get("status", "classificando")
    try:
        status = SessionStatus(status_raw)
    except ValueError:
        status = SessionStatus.CLASSIFICANDO
    return SessionState(
        whatsapp_id=dados.get("whatsapp_id", ""),
        status=status,
        tipo_beneficio=dados.get("tipo_beneficio"),
        esfera=dados.get("esfera"),
        dados_cliente=dados_cliente,
        documentos_recebidos=dados.get("documentos_recebidos", []),
        documentos_faltantes=dados.get("documentos_faltantes", []),
        conversa=conversa,
        step=dados.get("step", 0),
        ultima_atividade=dados.get("ultima_atividade", ""),
        ocr_retry_count=dados.get("ocr_retry_count", 0),
        motivo_pausa=dados.get("motivo_pausa"),
        signing_url=dados.get("signing_url"),
        zapsign_documento_id=dados.get("zapsign_documento_id"),
        documentos_gerados=dados.get("documentos_gerados", []),
        assinado_em=dados.get("assinado_em"),
        rascunho_rural_text=dados.get("rascunho_rural_text"),
        periodos_trabalho_rural=dados.get("periodos_trabalho_rural", []),
        processed_message_ids=dados.get("processed_message_ids", []),
        processed_zapsign_events=dados.get("processed_zapsign_events", []),
        trafego_pago=dados.get("trafego_pago", True),
        resumo_caso=dados.get("resumo_caso", ""),
        historico_perguntas=dados.get("historico_perguntas", []),
        simplify_mode=dados.get("simplify_mode", False),
        human_attending=dados.get("human_attending", False),
        reminder_count=dados.get("reminder_count", 0),
    )


# ── Conversão SessionState ↔ SessaoModel ──

def _sessao_para_model(sessao: SessionState) -> dict:
    """Converte SessionState para dict do modelo Sessao."""
    return {
        "whatsapp_id": sessao.whatsapp_id,
        "status": sessao.status.value,
        "tipo_beneficio": sessao.tipo_beneficio,
        "esfera": sessao.esfera,
        "ultima_atividade": (
            datetime.fromisoformat(sessao.ultima_atividade)
            if sessao.ultima_atividade else None
        ),
        "estado_json": _serializar_json(sessao),
    }


def _model_para_sessao(model: SessaoModel) -> SessionState:
    """Converte SessaoModel para SessionState."""
    estado = model.estado_json or {}
    sessao = _desserializar_json(estado)
    sessao.whatsapp_id = model.whatsapp_id
    try:
        sessao.status = SessionStatus(model.status)
    except ValueError:
        sessao.status = SessionStatus.CLASSIFICANDO
    sessao.tipo_beneficio = model.tipo_beneficio
    sessao.esfera = model.esfera
    if model.ultima_atividade:
        sessao.ultima_atividade = model.ultima_atividade.isoformat()
    return sessao


# ── Operações DB (async) ──

async def _salvar_db(sessao: SessionState) -> bool:
    """Salva sessão no PostgreSQL. Retorna True se ok, False se falhou."""
    try:
        dados = _sessao_para_model(sessao)
        async with async_session() as db:
            result = await db.execute(
                select(SessaoModel).where(SessaoModel.whatsapp_id == sessao.whatsapp_id)
            )
            modelo = result.scalar_one_or_none()
            if modelo:
                for k, v in dados.items():
                    setattr(modelo, k, v)
            else:
                db.add(SessaoModel(**dados))
            await db.commit()
        return True
    except Exception as e:
        logger.warning("DB save failed for %s: %s — falling back to JSON", sessao.whatsapp_id, e)
        return False


async def _carregar_db(whatsapp_id: str) -> Optional[SessionState]:
    """Carrega sessão do PostgreSQL. Retorna None se não existir ou falhar."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(SessaoModel).where(SessaoModel.whatsapp_id == whatsapp_id)
            )
            modelo = result.scalar_one_or_none()
            if modelo:
                return _model_para_sessao(modelo)
        return None
    except Exception as e:
        logger.warning("DB load failed for %s: %s — falling back to JSON", whatsapp_id, e)
        return None


async def _listar_arquivaveis_db() -> tuple[list[str], bool]:
    """Lista WhatsApp IDs de sessões inativas no DB.
    Retorna (ids, sucesso) — sucesso=False indica falha de conexão."""
    try:
        agora = datetime.now(timezone.utc)
        limite = agora.timestamp() - ARCHIVE_AFTER_SECONDS
        async with async_session() as db:
            result = await db.execute(
                select(SessaoModel).where(
                    and_(
                        SessaoModel.status != SessionStatus.ARQUIVADO.value,
                        SessaoModel.ultima_atividade.isnot(None),
                    )
                )
            )
            sessoes = result.scalars().all()
            arquivaveis = []
            for s in sessoes:
                if s.ultima_atividade and s.ultima_atividade.timestamp() < limite:
                    arquivaveis.append(s.whatsapp_id)
            return arquivaveis, True
    except Exception as e:
        logger.warning("DB list_archivable failed: %s — falling back to JSON", e)
        return [], False


async def _carregar_todas_db() -> tuple[dict[str, SessionState], bool]:
    """Carrega todas as sessões não-arquivadas do DB.
    Retorna (sessoes, sucesso) — sucesso=False indica falha de conexão."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(SessaoModel).where(SessaoModel.status != SessionStatus.ARQUIVADO.value)
            )
            sessoes = {}
            for modelo in result.scalars().all():
                sessao = _model_para_sessao(modelo)
                sessoes[sessao.whatsapp_id] = sessao
            return sessoes, True
    except Exception as e:
        logger.warning("DB load_all failed: %s — falling back to JSON", e)
        return {}, False


# ── Operações JSON (sync fallback) ──

def _salvar_json(sessao: SessionState) -> None:
    try:
        caminho = _caminho_sessao(sessao.whatsapp_id)
        with LOCK:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(_serializar_json(sessao), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("JSON save failed for %s: %s", sessao.whatsapp_id, e)


def _carregar_json(whatsapp_id: str) -> Optional[SessionState]:
    caminho = _caminho_sessao(whatsapp_id)
    if not caminho.exists():
        return None
    try:
        with LOCK:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
        return _desserializar_json(dados)
    except Exception as e:
        logger.error("JSON load failed for %s: %s", whatsapp_id, e)
        return None


def _listar_arquivaveis_json() -> list[str]:
    if not STORAGE_DIR.exists():
        return []
    agora = datetime.now(timezone.utc).timestamp()
    arquivaveis = []
    with LOCK:
        for arquivo in STORAGE_DIR.glob("sessao_*.json"):
            try:
                with open(arquivo, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                status = dados.get("status", "")
                if status == SessionStatus.ARQUIVADO.value:
                    continue
                ultima = dados.get("ultima_atividade", "")
                if not ultima:
                    continue
                ts = datetime.fromisoformat(ultima).timestamp()
                if agora - ts > ARCHIVE_AFTER_SECONDS:
                    whatsapp_id = dados.get("whatsapp_id", "")
                    if whatsapp_id:
                        key = whatsapp_id.split("@")[0] if "@" in whatsapp_id else whatsapp_id
                        arquivaveis.append(key)
            except Exception:
                continue
    return arquivaveis


def _carregar_todas_json() -> dict[str, SessionState]:
    sessoes = {}
    if not STORAGE_DIR.exists():
        return sessoes
    with LOCK:
        for arquivo in STORAGE_DIR.glob("sessao_*.json"):
            try:
                with open(arquivo, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                sessao = _desserializar_json(dados)
                if sessao.status is not SessionStatus.ARQUIVADO:
                    key = sessao.whatsapp_id.split("@")[0] if "@" in sessao.whatsapp_id else sessao.whatsapp_id
                    sessoes[key] = sessao
            except Exception:
                continue
    return sessoes


# ── API pública (async — DB primário, JSON fallback) ──

async def salvar_sessao(sessao: SessionState) -> None:
    """Salva sessão: tenta DB, fallback JSON."""
    sessao.ultima_atividade = datetime.now(timezone.utc).isoformat()
    ok = await _salvar_db(sessao)
    if not ok:
        _salvar_json(sessao)


async def carregar_sessao(whatsapp_id: str) -> Optional[SessionState]:
    """Carrega sessão: tenta DB, fallback JSON."""
    sessao = await _carregar_db(whatsapp_id)
    if sessao is None:
        sessao = _carregar_json(whatsapp_id)
    return sessao


async def listar_sessoes_arquivaveis() -> list[str]:
    """Lista IDs de sessões inativas: tenta DB, fallback JSON."""
    ids, db_ok = await _listar_arquivaveis_db()
    if not db_ok:
        ids = _listar_arquivaveis_json()
    return ids


async def arquivar_sessoes_inativas(sessoes_ativas: dict[str, SessionState]) -> None:
    """Arquiva sessões inativas tanto em memória quanto no DB/JSON."""
    for whatsapp_id in await listar_sessoes_arquivaveis():
        if whatsapp_id in sessoes_ativas:
            sessao = sessoes_ativas[whatsapp_id]
            if sessao.status is not SessionStatus.ARQUIVADO:
                sessao.status = SessionStatus.ARQUIVADO
                sessao.motivo_pausa = "inatividade"
                await salvar_sessao(sessao)
        else:
            sessao = await carregar_sessao(whatsapp_id)
            if sessao and sessao.status is not SessionStatus.ARQUIVADO:
                sessao.status = SessionStatus.ARQUIVADO
                sessao.motivo_pausa = "inatividade"
                await salvar_sessao(sessao)


async def carregar_todas_sessoes() -> dict[str, SessionState]:
    """Carrega todas as sessões não-arquivadas: tenta DB, fallback JSON."""
    sessoes, db_ok = await _carregar_todas_db()
    if not db_ok:
        sessoes = _carregar_todas_json()
    return sessoes


# ── Aliases backward-compat para testes ──
_serializar = _serializar_json
_desserializar = _desserializar_json
