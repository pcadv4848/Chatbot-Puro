import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from src.services.whatsapp import enviar_mensagem, baixar_midia
from src.conversation.state import SessionState, SessionStatus
from src.conversation.storage import (
    salvar_sessao,
    carregar_sessao,
    carregar_todas_sessoes,
    arquivar_sessoes_inativas,
    STORAGE_DIR,
)
from src.agents.supervisor import processar, SILENT
from src.agents.text_utils import eh_mensagem_duplicada as _eh_mensagem_duplicada
from src.config import settings
from src.engine.rate_limit import limiter
from src.services.transcricao import transcrever_audio_async, disponivel as whisper_disponivel
from src.services.whatsapp_openwa import _ultimos_envios

from src.conversation.jid_utils import session_key as _session_key, extrair_whatsapp_id as _extrair_whatsapp_id, mesmo_telefone
from src.conversation.admin_commands import processar_admin_commands as _admin_commands, set_storage_dir as _set_cmd_storage_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhooks"])

# Cache em memória do estado das sessões ativas.
sessoes_ativas: dict[str, SessionState] = {}
_sessoes_lock: asyncio.Lock = asyncio.Lock()

# Debounce de mensagens: agrupa mensagens do cliente antes de processar
_debounce_tasks: dict[str, asyncio.Task] = {}
_MESSAGE_DEBOUNCE_SECONDS = 1.5

# Número do próprio bot (descoberto da sessão OpenWA)
_bot_phone_number: str | None = None

# Configurar módulo de comandos admin com dependências do router
_set_cmd_storage_dir(STORAGE_DIR)

from src.conversation.admin_commands import ADMIN_ALIASES as _ADMIN_ALIASES, ADMIN_INPUTS as _ADMIN_INPUTS


def _is_admin(whatsapp_id: str) -> bool:
    admin_id = settings.admin_whatsapp or ""
    if admin_id and mesmo_telefone(whatsapp_id, admin_id):
        return True
    if _bot_phone_number and mesmo_telefone(whatsapp_id, _bot_phone_number):
        return True
    return False


async def _descobrir_bot_phone() -> str | None:
    global _bot_phone_number
    if _bot_phone_number:
        return _bot_phone_number
    try:
        from src.services.whatsapp_openwa import obter_status_sessao
        status = await obter_status_sessao()
        me = status.get("me") or {}
        jid = me.get("id", "")
        if jid:
            _bot_phone_number = _extrair_whatsapp_id(jid)
            logger.info("Número do bot descoberto: %s (JID: %s)", _bot_phone_number, jid)
            return _bot_phone_number
    except Exception as e:
        logger.debug("Não foi possível descobrir número do bot: %s", e)
    return None

# Contador de webhooks recebidos (diagnóstico)
_webhook_counter: int = 0
_ultimo_webhook: str | None = None
_processamento_em_andamento: list[dict] = []


PALAVRAS_ABANDONO = [
    "deixar pra lá", "deixa pra lá", "depois eu vejo", "depois eu falo",
    "agora não", "não quero mais", "cancelar", "desistir", "cansei",
    "depois resolvo", "vou deixar", "deixa quieto", "esquece",
    "não é agora", "outro dia", "sem tempo", "não quero",
]

PAUSA_AUTO_MINUTOS = settings.session_timeout_minutes or 30


def verificar_webhook_openwa(payload: dict, signature: str | None) -> bool:
    if not signature:
        logger.warning("Webhook OpenWA sem assinatura — rejeitado")
        return False
    if not settings.openwa_api_key:
        logger.warning("OPENWA_API_KEY não configurada — não é possível verificar webhook")
        return False
    body_normalizado = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    expected = hmac.new(
        settings.openwa_api_key.encode(),
        body_normalizado,
        hashlib.sha256,
    ).hexdigest()
    provided = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@router.get("/whatsapp")
async def verificar_webhook_get(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == settings.webhook_verify_token:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("ok")


def _parse_openwa_payload(payload: dict) -> list[dict]:
    global _bot_phone_number
    event = payload.get("event", "")
    data = payload.get("data") or payload.get("payload") or {}
    if event == "message.sent":
        body = (data.get("body", "") or "").strip()
        from_jid = data.get("from", "")
        to_jid = data.get("to", "")
        if from_jid and to_jid:
            sender = _extrair_whatsapp_id(from_jid)
            target = _extrair_whatsapp_id(to_jid)
            if not _bot_phone_number:
                _bot_phone_number = sender
                logger.info("Número do bot descoberto via message.sent: %s", _bot_phone_number)

            if _bot_phone_number and mesmo_telefone(sender, _bot_phone_number):
                if any(body.startswith(cmd) for cmd in _ADMIN_INPUTS):
                    return [{"id": data.get("id", ""), "from": target,
                             "type": "text", "body": body, "admin_cmd": True}]
                target_key = target.split("@")[0] if "@" in target else target
                last_bot_send = _ultimos_envios.get(target_key, 0)
                if time.time() - last_bot_send > 10.0:
                    logger.info("Humano detectado respondendo para %s via message.sent", target)
                    return [{"id": data.get("id", ""), "from": target,
                             "type": "text", "body": body, "_human_reply": True}]
                return []
        return []
    if event not in ("message.received", "messages.upsert"):
        return []

    from_jid = data.get("from", "")
    if not from_jid:
        return []
    whatsapp_id = _extrair_whatsapp_id(from_jid)

    msg_id = data.get("id", "")
    body = data.get("body", "") or ""
    msg_type = data.get("type", "")
    has_media = data.get("hasMedia", False)

    tipos_midia = {"image": "image", "video": "video", "document": "document", "audio": "audio",
                   "ptt": "audio", "voice": "audio", "sticker": "image"}

    if has_media or msg_type in tipos_midia:
        tipo = tipos_midia.get(msg_type, msg_type)
        midia_id = data.get("mediaId", "") or data.get("id", "")
        raw_media = data.get("media") or {}
        midia_data = raw_media.get("data", "") if isinstance(raw_media, dict) else ""
        return [{
            "id": msg_id or midia_id,
            "from": whatsapp_id,
            "type": tipo,
            "body": body,
            "midia_id": midia_id,
            "midia_data": midia_data,
        }]

    if body:
        return [{
            "id": msg_id,
            "from": whatsapp_id,
            "type": "text",
            "body": body,
        }]

    return []


def _parse_meta_payload(payload: dict) -> list[dict]:
    msgs = []
    entry = payload.get("entry", [])
    if not entry:
        return msgs
    for change in entry[0].get("changes", []):
        value = change.get("value", {})
        for msg in value.get("messages", []):
            msg_id = msg.get("id", "")
            whatsapp_id = _extrair_whatsapp_id(msg.get("from", ""))
            msg_type = msg.get("type", "")
            body = ""
            midia_id = None
            if msg_type == "text":
                body = msg.get("text", {}).get("body", "")
            elif msg_type in ("image", "document", "audio", "video"):
                midia_id = msg.get(msg_type, {}).get("id", "")
                body = msg.get(msg_type, {}).get("caption", "")
            msgs.append({
                "id": msg_id,
                "from": whatsapp_id,
                "type": msg_type,
                "body": body,
                "midia_id": midia_id,
            })
    return msgs


@router.post("/whatsapp")
@limiter.limit(settings.rate_limit_webhook)
async def webhook_whatsapp(request: Request):
    global _webhook_counter, _ultimo_webhook
    _webhook_counter += 1

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"status": "error"}

    evento = payload.get("event", "")
    _ultimo_webhook = f"{evento}|{datetime.now(timezone.utc).isoformat()}"

    if "event" in payload:
        sig = request.headers.get("x-openwa-signature")
        if not verificar_webhook_openwa(payload, sig):
            logger.warning("Webhook OpenWA rejeitado: assinatura inválida")
            return {"status": "ok"}
        mensagens = _parse_openwa_payload(payload)
        if not mensagens:
            logger.info("Webhook OpenWA ignorado: event=%s tipo=%s", payload.get("event"), payload.get("data", {}).get("type"))
    elif "entry" in payload:
        from src.services.signing import verificar_webhook_meta
        sig = request.headers.get("x-hub-signature-256")
        if not verificar_webhook_meta(body, sig):
            logger.warning("Webhook Meta rejeitado: assinatura inválida")
            return {"status": "ok"}
        mensagens = _parse_meta_payload(payload)
    else:
        logger.warning("Formato de webhook desconhecido: %s", list(payload.keys())[:3])
        return {"status": "ok"}

    task_id = f"{evento}_{time.time()}"
    track_entry = {"id": task_id, "inicio": datetime.now(timezone.utc).isoformat(),
                   "mensagens": len(mensagens), "status": "iniciado"}
    _processamento_em_andamento.append(track_entry)
    if len(_processamento_em_andamento) > 20:
        _processamento_em_andamento[:] = _processamento_em_andamento[-10:]

    async def _processar_em_background():
        try:
            for i, msg in enumerate(mensagens):
                sessao = None
                track_entry["status"] = f"processando_msg_{i+1}/{len(mensagens)}"
                try:
                    msg_id = msg.get("id", "")
                    whatsapp_id = msg["from"]
                    msg_type = msg["type"]
                    body = msg.get("body", "")
                    midia_id = msg.get("midia_id")
                    midia_data = msg.get("midia_data", "")
                    admin_cmd = msg.get("admin_cmd", False)

                    if msg_id and whatsapp_id:
                        sessao = await _obter_ou_criar_sessao(whatsapp_id)
                        if hasattr(sessao, "processed_message_ids") and msg_id in sessao.processed_message_ids:
                            continue

                    if msg.get("_human_reply"):
                        if not sessao:
                            sessao = await _obter_ou_criar_sessao(whatsapp_id)
                        sessao.human_attending = True
                        sessao.existing_client = True
                        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
                        sessao.conversa.append({"role": "user", "content": f"[humano respondeu: {body[:150]}]"})
                        await salvar_sessao(sessao)
                        if msg_id and msg_id not in sessao.processed_message_ids:
                            sessao.processed_message_ids.append(msg_id)
                        track_entry["status"] = f"human_detected_{whatsapp_id}"
                        logger.info("Resposta humana detectada — sessão %s marcada como human_attending", whatsapp_id)
                        continue

                    if sessao and not sessao.existing_client:
                        from src.services.attended_clients import is_attended
                        if await is_attended(whatsapp_id):
                            sessao.existing_client = True
                            logger.info("Contato %s marcado como existing_client via attended_clients", whatsapp_id)

                    is_admin = _is_admin(whatsapp_id)
                    logger.debug("MSG %s: existing_client=%s, is_admin=%s, admin_cmd=%s, type=%s, body='%s'",
                                 whatsapp_id, sessao.existing_client if sessao else None, is_admin, admin_cmd, msg_type, body[:50] if body else "")
                    if sessao and sessao.existing_client and not is_admin and not admin_cmd:
                        logger.info("BLOQUEADO existing_client: %s (body='%s')", whatsapp_id, body[:80] if body else "")
                        if sessao.status in (SessionStatus.CONCLUIDO, SessionStatus.ARQUIVADO):
                            sessao.conversa.append({"role": "user", "content": body or f"[{msg_type}: {midia_id}]"})
                            if msg_id:
                                if msg_id not in sessao.processed_message_ids:
                                    sessao.processed_message_ids.append(msg_id)
                                    if len(sessao.processed_message_ids) > 100:
                                        sessao.processed_message_ids = sessao.processed_message_ids[-50:]
                            await salvar_sessao(sessao)
                            continue
                        if msg_type == "text" and body:
                            if body.upper().strip(".!?") == "RESETAR":
                                await processar_mensagem_texto(whatsapp_id, body)
                            else:
                                sessao.conversa.append({"role": "user", "content": body})
                                if _detectar_abandono(body):
                                    sessao.status = SessionStatus.PAUSADO
                                    sessao.motivo_pausa = "abandono voluntário"
                                else:
                                    from src.agents.supervisor import _processar_humano
                                    await _processar_humano(body, sessao)
                        elif msg_type in ("image", "document") and midia_id:
                            await salvar_sessao(sessao)
                            sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
                        elif msg_type == "audio" and midia_id and whisper_disponivel():
                            try:
                                if midia_data:
                                    dados = base64.b64decode(midia_data)
                                else:
                                    dados = await asyncio.wait_for(baixar_midia(midia_id), timeout=30)
                                texto = await asyncio.wait_for(transcrever_audio_async(dados), timeout=120)
                                if texto:
                                    sessao.conversa.append({"role": "user", "content": f"[áudio transcrito]: {texto}"})
                                    logger.info("Áudio de existing_client %s transcrito e arquivado (sem resposta automática)", whatsapp_id)
                                else:
                                    logger.warning("Transcrição retornou vazia para %s (atendimento)", whatsapp_id)
                            except asyncio.TimeoutError:
                                logger.error("Timeout ao processar áudio de %s (atendimento)", whatsapp_id)
                            except Exception:
                                logger.exception("Erro ao processar áudio durante atendimento")
                        elif msg_type == "audio" and midia_id:
                            logger.info("Whisper não disponível — áudio de %s ignorado (atendimento)", whatsapp_id)
                        if msg_id:
                            if msg_id not in sessao.processed_message_ids:
                                sessao.processed_message_ids.append(msg_id)
                                if len(sessao.processed_message_ids) > 100:
                                    sessao.processed_message_ids = sessao.processed_message_ids[-50:]
                        await salvar_sessao(sessao)
                        continue

                    if msg_type == "text" and body:
                        ativar_silencioso = msg.get("_ativar_silencioso", False)
                        if admin_cmd or is_admin:
                            wa_id = whatsapp_id
                            await asyncio.wait_for(
                                processar_mensagem_texto(wa_id, body, admin_cmd=admin_cmd,
                                                         ativar_silencioso=ativar_silencioso),
                                timeout=120,
                            )
                        else:
                            if not sessao:
                                sessao = await _obter_ou_criar_sessao(whatsapp_id)
                            sessao.pending_messages.append(body)
                            if msg_id and msg_id not in sessao.processed_message_ids:
                                sessao.processed_message_ids.append(msg_id)
                            if whatsapp_id not in _debounce_tasks or _debounce_tasks[whatsapp_id].done():
                                _debounce_tasks[whatsapp_id] = asyncio.create_task(
                                    _processar_com_debounce(whatsapp_id)
                                )
                            continue
                    elif msg_type in ("image", "document") and midia_id:
                        await asyncio.wait_for(
                            processar_mensagem_midia(whatsapp_id, midia_id),
                            timeout=120,
                        )
                    elif msg_type in ("audio", "video"):
                        if not sessao:
                            sessao = await _obter_ou_criar_sessao(whatsapp_id)
                        if sessao.existing_client and not is_admin and not admin_cmd:
                            await salvar_sessao(sessao)
                            continue
                        if sessao.human_attending:
                            logger.info("human_attending ativo — áudio de %s ignorado pelo bot", whatsapp_id)
                            await salvar_sessao(sessao)
                            continue
                        elif msg_type == "audio" and midia_id and whisper_disponivel():
                            try:
                                if midia_data:
                                    dados = base64.b64decode(midia_data)
                                else:
                                    dados = await asyncio.wait_for(baixar_midia(midia_id), timeout=30)
                                texto = await asyncio.wait_for(transcrever_audio_async(dados), timeout=120)
                                if texto:
                                    await asyncio.wait_for(
                                        processar_mensagem_texto(whatsapp_id, texto, content_label="[áudio]"),
                                        timeout=120,
                                    )
                                else:
                                    logger.warning("Transcrição retornou vazia para %s", whatsapp_id)
                            except asyncio.TimeoutError:
                                logger.error("Timeout ao processar áudio de %s", whatsapp_id)
                            except Exception:
                                logger.exception("Erro ao processar áudio")
                        else:
                            await enviar_mensagem(
                                whatsapp_id,
                                f"Não consegui ouvir direito, pode me enviar por texto?"
                            )

                    if sessao and msg_id:
                        if msg_id not in sessao.processed_message_ids:
                            sessao.processed_message_ids.append(msg_id)
                            if len(sessao.processed_message_ids) > 100:
                                sessao.processed_message_ids = sessao.processed_message_ids[-50:]
                            await salvar_sessao(sessao)

                except asyncio.TimeoutError:
                    logger.error("TIMEOUT processando mensagem de %s", msg.get("from", "?"), exc_info=True)
                    track_entry["erro"] = f"timeout_msg_{i}"
                    try:
                        timeout_sessao = await _obter_ou_criar_sessao(msg.get("from", ""))
                        if not timeout_sessao.existing_client:
                            await enviar_mensagem(
                                msg["from"],
                                "Desculpe, estou demorando mais que o normal."
                                " Pode me enviar a mensagem novamente?"
                            )
                    except Exception as e:
                        track_entry["erro_envio_timeout"] = str(e)
                        logger.error("Falha ao enviar msg de timeout: %s", e, exc_info=True)
                except Exception as e:
                    err_msg = str(e)[:200]
                    logger.error("Erro processando msg %s: %s", msg.get("id", "?"), err_msg, exc_info=True)
                    track_entry["erro"] = f"msg_{i}: {err_msg}"
            track_entry["status"] = "concluido"
            track_entry["fim"] = datetime.now(timezone.utc).isoformat()
        except asyncio.CancelledError:
            track_entry["status"] = "cancelado"
            track_entry["fim"] = datetime.now(timezone.utc).isoformat()
            raise
        except Exception as e:
            track_entry["status"] = f"erro: {e}"
            track_entry["fim"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_processar_em_background())
    return {"status": "ok"}


def _detectar_abandono(texto: str) -> bool:
    texto_lower = texto.lower().strip()
    for frase in PALAVRAS_ABANDONO:
        if frase in texto_lower:
            return True
    return False


async def _obter_ou_criar_sessao(whatsapp_id: str) -> SessionState:
    key = _session_key(whatsapp_id)
    logger.debug("_obter_ou_criar_sessao(%s): key=%s", whatsapp_id, key)
    async with _sessoes_lock:
        if key in sessoes_ativas:
            old = sessoes_ativas[key]
            if old.whatsapp_id != whatsapp_id:
                old.whatsapp_id = whatsapp_id
            logger.debug("_obter_ou_criar_sessao(%s): CACHE HIT, existing_client=%s", whatsapp_id, old.existing_client)
            return old

    sessao = await carregar_sessao(key)
    if sessao is None:
        logger.debug("_obter_ou_criar_sessao(%s): NOVA sessao", whatsapp_id)
        sessao = SessionState(whatsapp_id=whatsapp_id)
    else:
        logger.debug("_obter_ou_criar_sessao(%s): carregada do disco, status=%s, existing_client=%s",
                     whatsapp_id, sessao.status.value, sessao.existing_client)
        sessao.whatsapp_id = whatsapp_id
        sessao.existing_client = True
        logger.debug("_obter_ou_criar_sessao(%s): sessao existente no disco → existing_client=True", whatsapp_id)
        if sessao.status == SessionStatus.PAUSADO:
            logger.info("Sessão retomada para %s", whatsapp_id)
        elif sessao.status == SessionStatus.ARQUIVADO:
            logger.info("Sessão arquivada reativada para %s", whatsapp_id)

    async with _sessoes_lock:
        if key not in sessoes_ativas:
            sessoes_ativas[key] = sessao
        return sessoes_ativas[key]


async def _salvar_e_enviar(sessao: SessionState, whatsapp_id: str, resposta: str):
    if _eh_mensagem_duplicada(resposta, sessao.sent_messages):
        logger.info("Mensagem duplicada detectada para %s — pulando envio: '%s'", whatsapp_id, resposta[:80])
        await salvar_sessao(sessao)
        return
    sessao.sent_messages.append(resposta)
    if len(sessao.sent_messages) > 50:
        sessao.sent_messages = sessao.sent_messages[-50:]
    await enviar_mensagem(whatsapp_id, resposta)
    await salvar_sessao(sessao)


async def _verificar_inatividade(sessao: SessionState, whatsapp_id: str) -> Optional[str]:
    try:
        ultima = datetime.fromisoformat(sessao.ultima_atividade)
        if ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=timezone.utc)
        agora = datetime.now(timezone.utc)
        diff_minutos = (agora - ultima).total_seconds() / 60

        if diff_minutos > PAUSA_AUTO_MINUTOS and sessao.status not in (SessionStatus.CONCLUIDO, SessionStatus.ARQUIVADO, SessionStatus.PAUSADO):
            sessao.status = SessionStatus.PAUSADO
            sessao.motivo_pausa = "inatividade"
            await salvar_sessao(sessao)
            return (
                "Olá, tudo bem? Ficou um tempo sem responder."
                " Se quiser continuar de onde paramos, é só me falar."
            )
    except (ValueError, TypeError) as e:
        logger.warning("Erro ao verificar inatividade da sessão %s: %s", whatsapp_id, e)
    return None


async def _processar_com_debounce(whatsapp_id: str):
    await asyncio.sleep(_MESSAGE_DEBOUNCE_SECONDS)
    key = _session_key(whatsapp_id)
    async with _sessoes_lock:
        sessao = sessoes_ativas.get(key)
    if not sessao or not sessao.pending_messages:
        return
    combined = "\n\n".join(sessao.pending_messages)
    sessao.pending_messages.clear()
    await salvar_sessao(sessao)
    await processar_mensagem_texto(whatsapp_id, combined)
    if sessao.pending_messages:
        _debounce_tasks[whatsapp_id] = asyncio.create_task(
            _processar_com_debounce(whatsapp_id)
        )


async def processar_mensagem_texto(whatsapp_id: str, texto: str, admin_cmd: bool = False,
                                   ativar_silencioso: bool = False,
                                   content_label: str | None = None):
    sessao = await _obter_ou_criar_sessao(whatsapp_id)
    _user_content = content_label or texto

    is_admin = _is_admin(whatsapp_id)

    if not admin_cmd and is_admin:
        admin_cmd = True

    cmd_resposta = await _admin_commands(texto, sessao, admin_cmd=admin_cmd, cache=sessoes_ativas)
    if cmd_resposta is not None:
        sessao.conversa.append({"role": "user", "content": _user_content})
        sessao.conversa.append({"role": "assistant", "content": cmd_resposta})
        if admin_cmd and not is_admin:
            logger.info("Admin cmd via message.sent: %s na sessão %s", texto, whatsapp_id)
            await salvar_sessao(sessao)
        else:
            await _salvar_e_enviar(sessao, whatsapp_id, cmd_resposta)
        return

    if ativar_silencioso:
        from src.services.attended_clients import mark_attended
        await mark_attended(whatsapp_id)
        sessao.human_attending = True
        sessao.existing_client = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        await salvar_sessao(sessao)
        logger.info("Modo silencioso ativado na sessão %s por mensagem do humano", whatsapp_id)
        return

    if sessao.reminder_count > 0:
        sessao.reminder_count = 0

    if is_admin and sessao.existing_client:
        sessao.existing_client = False

    if not is_admin:
        msg_inatividade = await _verificar_inatividade(sessao, whatsapp_id)
        if msg_inatividade:
            sessao.conversa.append({"role": "assistant", "content": msg_inatividade})
            await _salvar_e_enviar(sessao, whatsapp_id, msg_inatividade)
            return

    if _detectar_abandono(texto):
        sessao.status = SessionStatus.PAUSADO
        sessao.motivo_pausa = "abandono voluntário"
        resposta = (
            "Sem problemas, quando quiser retomar, é só me chamar."
        )
        sessao.conversa.append({"role": "user", "content": _user_content})
        sessao.conversa.append({"role": "assistant", "content": resposta})
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return

    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = SessionStatus.CLASSIFICANDO if not sessao.tipo_beneficio else SessionStatus.AGUARDANDO_ADVOGADO
        sessao.motivo_pausa = None
        nome = sessao.dados_cliente.get("nome")
        if nome:
            resume_msg = f"{nome}, que bom que você voltou. Vamos continuar?"
        else:
            resume_msg = "Que bom que você voltou. Vamos continuar?"
        sessao.conversa.append({"role": "assistant", "content": resume_msg})
        await _salvar_e_enviar(sessao, whatsapp_id, resume_msg)
        return

    sessao.conversa.append({"role": "user", "content": _user_content})

    logger.info("DEBUG pre-processar: step=%s, midia=%s, human=%s, existing=%s",
                 sessao.step, sessao.midia_inicial_enviada, sessao.human_attending, sessao.existing_client)
    resposta = await processar(texto, sessao)
    if resposta is SILENT:
        logger.info("DEBUG processar retornou SILENT")
        await salvar_sessao(sessao)
        return
    sessao.conversa.append({"role": "assistant", "content": resposta})
    await _salvar_e_enviar(sessao, whatsapp_id, resposta)


async def processar_mensagem_midia(whatsapp_id: str, midia_id: str):
    sessao = await _obter_ou_criar_sessao(whatsapp_id)

    msg_inatividade = await _verificar_inatividade(sessao, whatsapp_id)
    if msg_inatividade:
        sessao.conversa.append({"role": "assistant", "content": msg_inatividade})
        await _salvar_e_enviar(sessao, whatsapp_id, msg_inatividade)
        return

    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = SessionStatus.CLASSIFICANDO if not sessao.tipo_beneficio else SessionStatus.AGUARDANDO_ADVOGADO
        sessao.motivo_pausa = None
        nome = sessao.dados_cliente.get("nome")
        resume_msg = f"{nome}, que bom que você voltou." if nome else "Que bom que você voltou."
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        sessao.conversa.append({"role": "assistant", "content": resume_msg})
        await _salvar_e_enviar(sessao, whatsapp_id, resume_msg)
        return

    sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
    await salvar_sessao(sessao)


async def tarefa_arquivamento():
    while True:
        try:
            await arquivar_sessoes_inativas(sessoes_ativas)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Erro na tarefa de arquivamento: %s", e)
        await asyncio.sleep(3600)


async def iniciar_carregamento_sessoes():
    sessoes = await carregar_todas_sessoes()
    sessoes_ativas.update(sessoes)
    logger.info("%d sessão(ões) carregada(s) do disco", len(sessoes))


# ── Rota administrativa: QR Code ──

_QR_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><title>Conectar WhatsApp</title>
<style>
  body {{ font-family: sans-serif; display: flex; justify-content: center; align-items: center;
         height: 100vh; margin: 0; background: #f0f2f5; }}
  .card {{ background: #fff; padding: 40px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
           text-align: center; max-width: 420px; }}
  h2 {{ color: #075e54; margin-bottom: 8px; }}
  p {{ color: #666; font-size: 14px; line-height: 1.5; }}
  img {{ width: 300px; height: 300px; image-rendering: pixelated; margin: 16px 0; }}
  .btn {{ margin-top: 12px; padding: 10px 24px; background: #075e54; color: #fff;
          border: none; border-radius: 8px; cursor: pointer; font-size: 14px; text-decoration: none;
          display: inline-block; }}
  .btn:hover {{ background: #054d44; }}
  .btn-danger {{ background: #c33; }}
  .btn-danger:hover {{ background: #a33; }}
  .msg {{ color: #666; font-size: 14px; margin: 20px 0; }}
  .spinner {{ display: inline-block; width: 40px; height: 40px; border: 4px solid #ddd;
              border-top-color: #075e54; border-radius: 50%; animation: spin .8s linear infinite; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style></head>
<body>
<div class="card">
  <h2> Conectar WhatsApp</h2>
  {content}
</div>
<script>
setTimeout(function(){{ location.reload(); }}, {reload_seconds}000);
</script>
</body>
</html>"""


@router.get("/qr/desconectar")
async def desconectar_whatsapp(token: str = Query("")):
    token_valido = settings.admin_password
    if not token_valido or token != token_valido:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h2>Acesso negado</h2><p>Informe <code>?token=...</code> na URL.</p>",
            status_code=401,
        )

    from src.services.whatsapp import deletar_sessao, obter_status_sessao
    from src.services.whatsapp import criar_sessao, iniciar_sessao

    logger.info("Desconectando sessão WhatsApp...")
    from src.services.whatsapp_openwa import desconectar_sessao
    try:
        await desconectar_sessao()
        logger.info("Sessão desconectada via API")
    except Exception as e:
        logger.warning("Falha ao desconectar via API: %s", e)
    await asyncio.sleep(1)

    try:
        await deletar_sessao()
        logger.info("Sessão deletada")
    except Exception as e:
        logger.warning("Falha ao deletar sessão: %s", e)
    await asyncio.sleep(2)

    await criar_sessao()
    await asyncio.sleep(1)
    await iniciar_sessao()
    logger.info("Sessão recriada — redirecionando para QR")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/webhook/qr?token={token}", status_code=303)


@router.get("/diag")
async def diagnosticar_webhook():
    import httpx
    from src.services.whatsapp_openwa import (
        _get_base_url, _get_session_id, _get_headers, resolver_uuid_sessao,
        _session_id_override, _ultimo_erro_envio,
    )

    await resolver_uuid_sessao()
    session_id = _get_session_id()
    base = _get_base_url()
    headers = _get_headers()
    resultado = {
        "session_id": session_id, "session_id_is_uuid": session_id != settings.openwa_session_id,
        "session_id_override": _session_id_override, "base_url": base,
        "webhooks": [], "sessions": [], "erros": [],
        "teste_webhook": None,
        "ultimo_erro_envio_openwa": _ultimo_erro_envio,
        "chatbot": {
            "webhooks_recebidos": _webhook_counter,
            "ultimo_webhook": _ultimo_webhook,
            "processamento": _processamento_em_andamento[-5:] if _processamento_em_andamento else [],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{base}/sessions", headers=headers)
            if resp.status_code < 400:
                resultado["sessions"] = resp.json()
            else:
                resultado["erros"].append(f"list sessions: {resp.status_code}")

            resp2 = await c.get(f"{base}/sessions/{session_id}/webhooks", headers=headers)
            if resp2.status_code < 400:
                resultado["webhooks"] = resp2.json()
            else:
                resultado["erros"].append(f"list webhooks: {resp2.status_code} {resp2.text[:200]}")

            try:
                resp3 = await c.get(f"{base}/sessions/{session_id}", headers=headers)
                if resp3.status_code < 400:
                    resultado["session_status"] = resp3.json()
            except Exception as e:
                resultado["erros"].append(f"get session: {e}")

            wh_id = resultado["webhooks"][0]["id"] if resultado["webhooks"] else None
            if wh_id:
                try:
                    resp4 = await c.post(
                        f"{base}/sessions/{session_id}/webhooks/{wh_id}/test",
                        headers=headers,
                    )
                    resultado["teste_webhook"] = {
                        "status": resp4.status_code,
                        "body": resp4.text[:300],
                    }
                except Exception as e:
                    resultado["erros"].append(f"test webhook: {e}")
    except Exception as e:
        resultado["erros"].append(str(e))

    return JSONResponse(resultado)


@router.post("/send-test")
async def testar_envio_direto(request: Request):
    from src.services.whatsapp_openwa import _enviar_mensagem_com_recovery, _ultimo_erro_envio
    try:
        body = await request.json()
        wa_id = body.get("whatsapp_id", "")
        texto = body.get("texto", "teste de diagnóstico")
        if not wa_id:
            return JSONResponse({"status": "error", "message": "whatsapp_id é obrigatório"}, status_code=400)
        resultado = await _enviar_mensagem_com_recovery(wa_id, texto)
        return JSONResponse({
            "status": "ok",
            "resultado": resultado,
            "ultimo_erro": _ultimo_erro_envio,
        })
    except Exception as e:
        return JSONResponse({
            "status": "erro",
            "erro": str(e)[:500],
            "ultimo_erro": _ultimo_erro_envio,
        }, status_code=500)


@router.post("/registrar-webhook")
async def forcar_registro_webhook():
    from src.services.whatsapp import configurar_webhook
    from src.config import settings as cfg

    webhook_url = f"{cfg.app_url}/webhook/whatsapp"
    try:
        await configurar_webhook(webhook_url, force=True)
        return JSONResponse({"status": "ok", "webhook_url": webhook_url})
    except Exception as e:
        return JSONResponse({"status": "erro", "erro": str(e)}, status_code=500)


@router.get("/qr")
async def obter_qr_code(token: str = Query("")):
    from src.services.whatsapp import (
        obter_qr, criar_sessao, iniciar_sessao, deletar_sessao,
        configurar_webhook, obter_status_sessao,
    )
    from src.services.whatsapp_openwa import extrair_qr_base64, resolver_uuid_sessao
    from src.config import settings as cfg

    token_param = f"?token={token}" if token else ""

    await resolver_uuid_sessao()

    # Verificar status da sessão antes de tentar QR
    try:
        status_data = await obter_status_sessao()
        if status_data.get("status") == "failed":
            logger.info("QR: sessão em failed — recriando...")
            try:
                await deletar_sessao()
            except Exception:
                pass
            await asyncio.sleep(1)
            await criar_sessao()
            await asyncio.sleep(2)
            await iniciar_sessao()
            await asyncio.sleep(3)
    except Exception:
        pass

    qr_data = await obter_qr()
    status = qr_data.get("status", "")
    qr_base64 = extrair_qr_base64(qr_data)

    if not qr_base64 and status == "error":
        logger.info("QR: QR não disponível (status=error) — recriando sessão")
        try:
            await deletar_sessao()
            logger.info("QR: sessão deletada")
        except Exception:
            pass
        await asyncio.sleep(1)

        try:
            await criar_sessao()
            await asyncio.sleep(2)
            await iniciar_sessao()
        except Exception as e:
            logger.error("QR: falha ao criar/iniciar: %s", e)

        for tentativa in range(6):
            await asyncio.sleep(2)
            qr_data = await obter_qr()
            qr_base64 = extrair_qr_base64(qr_data)
            status = qr_data.get("status", "")
            if qr_base64 or status in ("connected", "pending"):
                break
            logger.info("QR: aguardando geração (tentativa %d/6)", tentativa + 1)

    webhook_url = f"{cfg.app_url}/webhook/whatsapp"
    try:
        await configurar_webhook(webhook_url)
    except Exception as e_wh:
        logger.warning("QR: falha webhook: %s", e_wh)

    if qr_base64:
        content = (
            '<p>Abra o WhatsApp no celular →<br>'
            '<strong>Menu</strong> → <strong>Dispositivos Conectados</strong> → '
            '<strong>Conectar um dispositivo</strong><br>E escaneie o QR code abaixo:</p>'
            f'<img src="data:image/png;base64,{qr_base64}" alt="QR Code WhatsApp">'
            '<br><a class="btn" href="/webhook/qr">↻ Atualizar</a>'
            f'<br><br><a class="btn btn-danger" href="/webhook/qr/desconectar{token_param}">'
            ' Desconectar e gerar novo QR</a>'
        )
        reload_seconds = 30
    elif status == "connected":
        content = (
            '<p> WhatsApp já conectado!</p>'
            '<p class="msg">A sessão já está ativa. '
            'Se precisar reconectar com outro número, use o botão abaixo.</p>'
            f'<a class="btn btn-danger" href="/webhook/qr/desconectar{token_param}">'
            ' Desconectar e conectar outro número</a>'
        )
        reload_seconds = 30
    elif status == "error":
        erro_msg = qr_data.get("message", "Erro desconhecido")
        content = (
            '<p> Erro ao conectar com OpenWA</p>'
            f'<p class="msg">{erro_msg}</p>'
            '<a class="btn" href="/webhook/qr">↻ Tentar novamente</a>'
            f'<br><br><a class="btn btn-danger" href="/webhook/qr/desconectar{token_param}">'
            ' Desconectar e gerar novo QR</a>'
        )
        reload_seconds = 15
    else:
        content = (
            '<div class="spinner"></div>'
            '<p class="msg">Aguardando QR Code...<br>'
            'A página será atualizada automaticamente em alguns segundos.</p>'
            '<a class="btn" href="/webhook/qr">↻ Tentar novamente</a>'
            f'<br><br><a class="btn btn-danger" href="/webhook/qr/desconectar{token_param}">'
            ' Desconectar e gerar novo QR</a>'
        )
        reload_seconds = 5

    from fastapi.responses import HTMLResponse
    return HTMLResponse(_QR_HTML.format(content=content, reload_seconds=reload_seconds))
