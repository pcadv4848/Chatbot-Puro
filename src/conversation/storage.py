import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.exc import OperationalError, IntegrityError
from src.conversation.state import SessionState, SessionStatus
from src.config import settings
from src.engine.crypto import decrypt_dict, decrypt_json
from src.db.models import Sessao as SessaoModel
from src.db.session import async_session

logger = logging.getLogger(__name__)

# ── Fallback JSON ──
STORAGE_DIR = Path(__file__).parent.parent.parent / "data" / "sessoes"
LOCK = asyncio.Lock()

_save_locks: dict[str, asyncio.Lock] = {}
_save_locks_lock = asyncio.Lock()


async def _get_save_lock(whatsapp_id: str) -> asyncio.Lock:
    async with _save_locks_lock:
        if whatsapp_id not in _save_locks:
            _save_locks[whatsapp_id] = asyncio.Lock()
        return _save_locks[whatsapp_id]

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
        "step": sessao.step,
        "ultima_atividade": sessao.ultima_atividade,
        "motivo_pausa": sessao.motivo_pausa,
        "trafego_pago": sessao.trafego_pago,
        "simplify_mode": sessao.simplify_mode,
        "human_attending": sessao.human_attending,
        "existing_client": sessao.existing_client,
        "reminder_count": sessao.reminder_count,
        "midia_inicial_enviada": sessao.midia_inicial_enviada,
        "sent_messages": sessao.sent_messages[-20:] if sessao.sent_messages else [],
    }
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
        conversa=conversa,
        step=dados.get("step", 0),
        ultima_atividade=dados.get("ultima_atividade", ""),
        motivo_pausa=dados.get("motivo_pausa"),
        documentos_gerados=dados.get("documentos_gerados", []),
        rascunho_rural_text=dados.get("rascunho_rural_text"),
        periodos_trabalho_rural=dados.get("periodos_trabalho_rural", []),
        processed_message_ids=dados.get("processed_message_ids", []),
        trafego_pago=dados.get("trafego_pago", False),
        resumo_caso=dados.get("resumo_caso", ""),
        historico_perguntas=dados.get("historico_perguntas", []),
        simplify_mode=dados.get("simplify_mode", False),
        human_attending=dados.get("human_attending", False),
        existing_client=dados.get("existing_client", False),
        reminder_count=dados.get("reminder_count", 0),
        midia_inicial_enviada=dados.get("midia_inicial_enviada", False),
        sent_messages=dados.get("sent_messages", []),
    )


# ── Conversão SessionState ↔ SessaoModel ──

def _sessao_para_model(sessao: SessionState) -> dict:
    return {
        "whatsapp_id": sessao.whatsapp_id,
        "status": sessao.status.value,
        "human_attending": "true" if sessao.human_attending else "false",
        "tipo_beneficio": sessao.tipo_beneficio,
        "esfera": sessao.esfera,
        "ultima_atividade": (
            datetime.fromisoformat(sessao.ultima_atividade)
            if sessao.ultima_atividade else None
        ),
        "estado_json": _serializar_json(sessao),
    }


def _model_para_sessao(model: SessaoModel) -> SessionState:
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

    if isinstance(model.human_attending, str):
        sessao.human_attending = model.human_attending.lower() == "true"
    else:
        sessao.human_attending = bool(model.human_attending)

    if sessao.status in (
        SessionStatus.AGUARDANDO_ADVOGADO,
        SessionStatus.CONCLUIDO,
    ):
        sessao.human_attending = True

    return sessao


# ── Operações DB (async) ──

async def _salvar_db(sessao: SessionState) -> bool:
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
    except OperationalError as e:
        logger.warning("DB save failed for %s (operational): %s — falling back to JSON", sessao.whatsapp_id, e)
        return False
    except IntegrityError as e:
        logger.warning("DB save failed for %s (integrity): %s — falling back to JSON", sessao.whatsapp_id, e)
        return False
    except Exception as e:
        logger.warning("DB save failed for %s: %s — falling back to JSON", sessao.whatsapp_id, e)
        return False


async def _carregar_db(whatsapp_id: str) -> Optional[SessionState]:
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

async def _salvar_json(sessao: SessionState) -> None:
    try:
        caminho = _caminho_sessao(sessao.whatsapp_id)
        async with LOCK:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(_serializar_json(sessao), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("JSON save failed for %s: %s", sessao.whatsapp_id, e)


async def _carregar_json(whatsapp_id: str) -> Optional[SessionState]:
    caminho = _caminho_sessao(whatsapp_id)
    if not caminho.exists():
        return None
    try:
        async with LOCK:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
        return _desserializar_json(dados)
    except Exception as e:
        logger.error("JSON load failed for %s: %s", whatsapp_id, e)
        return None


async def _listar_arquivaveis_json() -> list[str]:
    if not STORAGE_DIR.exists():
        return []
    agora = datetime.now(timezone.utc).timestamp()
    arquivaveis = []
    async with LOCK:
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


async def _carregar_todas_json() -> dict[str, SessionState]:
    sessoes = {}
    if not STORAGE_DIR.exists():
        return sessoes
    async with LOCK:
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


# ── API pública ──

async def salvar_sessao(sessao: SessionState) -> None:
    lock = await _get_save_lock(sessao.whatsapp_id)
    async with lock:
        if len(sessao.conversa) > 100:
            sessao.conversa = sessao.conversa[-50:]
        sessao.ultima_atividade = datetime.now(timezone.utc).isoformat()
        ok = await _salvar_db(sessao)
        if not ok:
            await _salvar_json(sessao)


async def carregar_sessao(whatsapp_id: str) -> Optional[SessionState]:
    sessao = await _carregar_db(whatsapp_id)
    if sessao is None:
        sessao = await _carregar_json(whatsapp_id)
    return sessao


async def listar_sessoes_arquivaveis() -> list[str]:
    ids, db_ok = await _listar_arquivaveis_db()
    if not db_ok:
        ids = await _listar_arquivaveis_json()
    return ids


async def arquivar_sessoes_inativas(sessoes_ativas: dict[str, SessionState]) -> None:
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


async def deletar_sessao(whatsapp_id: str) -> None:
    """Remove sessão do banco de dados e do arquivo JSON."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(SessaoModel).where(SessaoModel.whatsapp_id == whatsapp_id)
            )
            modelo = result.scalar_one_or_none()
            if modelo:
                await db.delete(modelo)
                await db.commit()
    except Exception as e:
        logger.warning("DB delete failed for %s: %s", whatsapp_id, e)
    caminho = _caminho_sessao(whatsapp_id)
    if caminho.exists():
        caminho.unlink()


async def carregar_todas_sessoes() -> dict[str, SessionState]:
    sessoes, db_ok = await _carregar_todas_db()
    if not db_ok:
        sessoes = await _carregar_todas_json()
    return sessoes


_serializar = _serializar_json
_desserializar = _desserializar_json

__all__ = [
    "salvar_sessao",
    "carregar_sessao",
    "deletar_sessao",
    "listar_sessoes_arquivaveis",
    "arquivar_sessoes_inativas",
    "carregar_todas_sessoes",
    "STORAGE_DIR",
]
