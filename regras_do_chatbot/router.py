"""Rotas webhook do WhatsApp e Zapsign.

POST /webhook/whatsapp → recebimento de mensagens do cliente (OpenWA)
POST /webhook/zapsign → confirmação de assinatura digital

Nota: OpenWA substituiu a Meta Cloud API. O handshake GET não é mais necessário
(OpenWA usa webhook com HMAC, não challenge). Mantido para compatibilidade.

Todas as chamadas HTTP externas são assíncronas para não bloquear o event loop.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from src.services.whatsapp import enviar_mensagem
from src.conversation.state import SessionState, SessionStatus
from src.conversation.storage import (
    salvar_sessao,
    carregar_sessao,
    carregar_todas_sessoes,
    arquivar_sessoes_inativas,
    STORAGE_DIR,
)
from src.agents.supervisor import processar, processar_midia, SILENT
from src.services.signing import (
    processar_webhook as processar_zapsign_webhook,
    verificar_webhook_zapsign,
    verificar_webhook_meta,
)
from src.config import settings
from src.engine.rate_limit import limiter
from src.services.transcricao import transcrever_audio_async, disponivel as whisper_disponivel
from src.services.whatsapp import baixar_midia
from src.services.whatsapp_openwa import _ultimos_envios

from src.conversation.jid_utils import session_key as _session_key, extrair_whatsapp_id as _extrair_whatsapp_id
from src.conversation.admin_commands import processar_admin_commands as _admin_commands, set_storage_dir as _set_cmd_storage_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhooks"])

# Cache em memória do estado das sessões ativas.
sessoes_ativas: dict[str, SessionState] = {}
zapsign_doc_index: dict[str, str] = {}  # doc_id → whatsapp_id

# Número do próprio bot (descoberto da sessão OpenWA)
_bot_phone_number: str | None = None

# Configurar módulo de comandos admin com dependências do router
_set_cmd_storage_dir(STORAGE_DIR)

# Importar _ADMIN_ALIASES e ADMIN_INPUTS do módulo admin_commands para compatibilidade
from src.conversation.admin_commands import ADMIN_ALIASES as _ADMIN_ALIASES, ADMIN_INPUTS as _ADMIN_INPUTS


async def _descobrir_bot_phone() -> str | None:
    """Obtém o número do próprio bot da sessão OpenWA."""
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
_processamento_em_andamento: list[dict] = []  # rastreamento de processamento


def atualizar_indice_zapsign(whatsapp_id: str, doc_id: str | None) -> None:
    """Atualiza o índice reverso doc_id → whatsapp_id para busca O(1) no webhook."""
    if doc_id:
        zapsign_doc_index[doc_id] = whatsapp_id
    # Limpar entradas órfãs do mesmo whatsapp_id (mantém consistência)
    for d, w in list(zapsign_doc_index.items()):
        if w == whatsapp_id and d != doc_id:
            zapsign_doc_index.pop(d, None)

# Palavras que indicam abandono/cancelamento da sessão
PALAVRAS_ABANDONO = [
    "deixar pra lá", "deixa pra lá", "depois eu vejo", "depois eu falo",
    "agora não", "não quero mais", "cancelar", "desistir", "cansei",
    "depois resolvo", "vou deixar", "deixa quieto", "esquece",
    "não é agora", "outro dia", "sem tempo", "não quero",
]

# Tempo de inatividade para pausar automaticamente (minutos)
PAUSA_AUTO_MINUTOS = settings.session_timeout_minutes or 30




def verificar_webhook_openwa(payload: dict, signature: str | None) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook OpenWA.

    OpenWA envia o header 'x-openwa-signature' calculado como:
        HMAC-SHA256(api_key, JSON.stringify(payload))
    O HMAC é calculado sobre o JSON re-serializado (sem espaços extras),
    não sobre o raw body, pois JSON.stringify produz saída canônica.
    Aceita o formato ``sha256=<hex>``.
    """
    if not signature:
        logger.warning("Webhook OpenWA sem assinatura — rejeitado")
        return False
    if not settings.openwa_api_key:
        logger.warning("OPENWA_API_KEY não configurada — não é possível verificar webhook")
        return False
    # OpenWA usa JSON.stringify() que produz sem espaços
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
    """Endpoint de verificação (Meta Cloud API legado / OpenWA Webhook)."""
    if hub_mode == "subscribe" and hub_token == settings.webhook_verify_token:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("ok")





def _parse_openwa_payload(payload: dict) -> list[dict]:
    """Extrai mensagens de um payload do OpenWA.

    OpenWA envia um formato normalizado:
      {"event": "message.received", "data": {"id": "...", "from": "...@c.us",
       "body": "...", "type": "chat|image|...", "hasMedia": bool, "mediaId": "..."}}
    """
    event = payload.get("event", "")
    data = payload.get("data") or payload.get("payload") or {}
    if event not in ("message.received", "messages.upsert"):
        if event == "message.sent":
            body = (data.get("body", "") or "").strip()
            from_jid = data.get("from", "")
            to_jid = data.get("to", "")
            if from_jid and to_jid:
                sender = _extrair_whatsapp_id(from_jid)
                target = _extrair_whatsapp_id(to_jid)
                admin_id = settings.admin_whatsapp or _bot_phone_number or ""
                sender_raw = sender.replace("@lid", "")
                if sender_raw == admin_id:
                    if body in _ADMIN_INPUTS:
                        return [{"id": data.get("id", ""), "from": target,
                                 "type": "text", "body": body, "admin_cmd": True}]
                    # Mensagem não é comando → verificar se é humano (não eco do bot)
                    target_key = target.split("@")[0] if "@" in target else target
                    ultimo = _ultimos_envios.get(target_key, 0)
                    if time.time() - ultimo > 5.0:
                        return [{"id": data.get("id", ""), "from": target,
                                 "type": "text", "body": body, "admin_cmd": True,
                                 "_ativar_silencioso": True}]
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
                   "ptt": "audio", "sticker": "image"}

    if has_media or msg_type in tipos_midia:
        tipo = tipos_midia.get(msg_type, msg_type)
        midia_id = data.get("mediaId", "") or data.get("id", "")
        return [{
            "id": msg_id or midia_id,
            "from": whatsapp_id,
            "type": tipo,
            "body": body,
            "midia_id": midia_id,
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
    """Extrai mensagens de um payload da Meta Cloud API (legado)."""
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
    """Recebe mensagens enviadas pelo cliente (OpenWA ou Meta legado)."""
    global _webhook_counter, _ultimo_webhook
    _webhook_counter += 1

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"status": "error"}

    evento = payload.get("event", "")
    _ultimo_webhook = f"{evento}|{datetime.now(timezone.utc).isoformat()}"

    # Verificar assinatura HMAC baseada no formato do payload
    if "event" in payload:
        sig = request.headers.get("x-openwa-signature")
        if not verificar_webhook_openwa(payload, sig):
            logger.warning("Webhook OpenWA rejeitado: assinatura inválida")
            return {"status": "ok"}
        mensagens = _parse_openwa_payload(payload)
    elif "entry" in payload:
        sig = request.headers.get("x-hub-signature-256")
        if not verificar_webhook_meta(body, sig):
            logger.warning("Webhook Meta rejeitado: assinatura inválida")
            return {"status": "ok"}
        mensagens = _parse_meta_payload(payload)
    else:
        logger.warning("Formato de webhook desconhecido: %s", list(payload.keys())[:3])
        return {"status": "ok"}

    import time as time_module
    task_id = f"{evento}_{time_module.time()}"
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

                    if msg_id and whatsapp_id:
                        sessao = await _obter_ou_criar_sessao(whatsapp_id)
                        if hasattr(sessao, "processed_message_ids") and msg_id in sessao.processed_message_ids:
                            continue

                    if msg_type == "text" and body:
                        admin_cmd = msg.get("admin_cmd", False)
                        ativar_silencioso = msg.get("_ativar_silencioso", False)
                        await asyncio.wait_for(
                            processar_mensagem_texto(whatsapp_id, body, admin_cmd=admin_cmd,
                                                     ativar_silencioso=ativar_silencioso),
                            timeout=120,
                        )
                    elif msg_type in ("image", "document") and midia_id:
                        await asyncio.wait_for(
                            processar_mensagem_midia(whatsapp_id, midia_id),
                            timeout=120,
                        )
                    elif msg_type in ("audio", "video"):
                        if not sessao:
                            sessao = await _obter_ou_criar_sessao(whatsapp_id)
                        if sessao.existing_client:
                            if msg_type == "audio" and midia_id and whisper_disponivel():
                                try:
                                    dados = await baixar_midia(midia_id)
                                    texto = await transcrever_audio_async(dados)
                                    if texto:
                                        await _processar_humano(texto, sessao)
                                except Exception:
                                    logger.exception("Erro ao transcrever áudio de cliente existente")
                            await salvar_sessao(sessao)
                        elif sessao.human_attending:
                            await salvar_sessao(sessao)
                        elif msg_type == "audio" and midia_id and whisper_disponivel():
                            try:
                                dados = await baixar_midia(midia_id)
                                texto = await transcrever_audio_async(dados)
                                if texto:
                                    await processar_mensagem_texto(whatsapp_id, texto)
                            except Exception:
                                logger.exception("Erro ao processar áudio")
                        else:
                            await enviar_mensagem(
                                whatsapp_id,
                                f"Recebi seu {msg_type}! Infelizmente ainda não consigo "
                                f"processar {msg_type}. Pode me enviar por texto?"
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
                                "Desculpe, estou demorando mais que o normal. "
                                "Pode tentar novamente? "
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
        except Exception as e:
            track_entry["status"] = f"erro: {e}"
            track_entry["fim"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_processar_em_background())
    return {"status": "ok"}


def _detectar_abandono(texto: str) -> bool:
    """Verifica se o texto indica abandono ou cancelamento."""
    texto_lower = texto.lower().strip()
    for frase in PALAVRAS_ABANDONO:
        if frase in texto_lower:
            return True
    return False


async def _obter_ou_criar_sessao(whatsapp_id: str) -> SessionState:
    """Recupera sessão existente (memória ou disco) ou cria nova.

    A chave de sessão (sessoes_ativas, arquivo) usa o número cru
    sem sufixos JID (@c.us, @lid, @s.whatsapp.net), mas o objeto
    SessionState mantém o JID original para envio de mensagens.

    Para clientes novos (sem sessão em disco), existing_client=false
    por padrão — sessão em disco é a única fonte de verdade.
    Use PostgreSQL (DATABASE_URL) para persistência entre restarts.
    """
    key = _session_key(whatsapp_id)
    if key in sessoes_ativas:
        old = sessoes_ativas[key]
        if old.whatsapp_id != whatsapp_id:
            old.whatsapp_id = whatsapp_id
        return old

    sessao = await carregar_sessao(key)
    if sessao is None:
        sessao = SessionState(whatsapp_id=whatsapp_id)
    else:
        sessao.whatsapp_id = whatsapp_id
        sessao.existing_client = True
        if sessao.status == SessionStatus.PAUSADO:
            logger.info("Sessão retomada para %s", whatsapp_id)
        elif sessao.status == SessionStatus.ARQUIVADO:
            sessao.status = SessionStatus.CLASSIFICANDO
            sessao.motivo_pausa = None
            logger.info("Sessão arquivada reativada para %s", whatsapp_id)

    sessoes_ativas[key] = sessao
    return sessao


async def _salvar_e_enviar(sessao: SessionState, whatsapp_id: str, resposta: str):
    """Envia resposta e salva sessão em disco."""
    await enviar_mensagem(whatsapp_id, resposta)
    await salvar_sessao(sessao)


async def _verificar_inatividade(sessao: SessionState, whatsapp_id: str) -> Optional[str]:
    """Verifica se a sessão está inativa há muito tempo. Retorna mensagem se precisar avisar."""
    try:
        ultima = datetime.fromisoformat(sessao.ultima_atividade)
        agora = datetime.now(timezone.utc)
        diff_minutos = (agora - ultima).total_seconds() / 60

        if diff_minutos > PAUSA_AUTO_MINUTOS and sessao.status not in (SessionStatus.CONCLUIDO, SessionStatus.ARQUIVADO, SessionStatus.PAUSADO):
            sessao.status = SessionStatus.PAUSADO
            sessao.motivo_pausa = "inatividade"
            await salvar_sessao(sessao)
            return (
                "Olá!  Seu atendimento estava pausado por inatividade.\n\n"
                "Se quiser retomar de onde parou, é só me dizer! "
                "Ou se mudou de ideia, pode me avisar também."
            )
    except (ValueError, TypeError) as e:
        logger.warning("Erro ao verificar inatividade da sessão %s: %s", whatsapp_id, e)
    return None


async def processar_mensagem_texto(whatsapp_id: str, texto: str, admin_cmd: bool = False,
                                   ativar_silencioso: bool = False):
    """Processa uma mensagem de texto usando o agente supervisor."""
    # 1. Recuperar ou criar sessão (com suporte a retomada)
    sessao = await _obter_ou_criar_sessao(whatsapp_id)

    # 1b. Comandos administrativos (apenas do número admin)
    cmd_resposta = await _admin_commands(texto, sessao, admin_cmd=admin_cmd)
    if cmd_resposta is not None:
        sessao.conversa.append({"role": "user", "content": texto})
        sessao.conversa.append({"role": "assistant", "content": cmd_resposta})
        if admin_cmd:
            logger.info("Admin cmd via message.sent: %s na sessão %s", texto, whatsapp_id)
            await salvar_sessao(sessao)
        else:
            await _salvar_e_enviar(sessao, whatsapp_id, cmd_resposta)
        return

    # 1b2. Mensagem do humano para cliente (não eco do bot) → ativar modo silencioso
    if ativar_silencioso:
        sessao.human_attending = True
        sessao.status = SessionStatus.AGUARDANDO_ADVOGADO
        await salvar_sessao(sessao)
        logger.info("Modo silencioso ativado na sessão %s por mensagem do humano", whatsapp_id)
        return

    # 1c. Resetar contagem de lembretes (cliente enviou mensagem)
    if sessao.reminder_count > 0:
        sessao.reminder_count = 0

    # 1d. Cliente existente → extração silenciosa (sem enviar mensagem ao cliente)
    admin_id = settings.admin_whatsapp or _bot_phone_number or ""
    if sessao.existing_client and _session_key(sessao.whatsapp_id) != admin_id:
        from src.agents.supervisor import _processar_humano
        await _processar_humano(texto, sessao)
        await salvar_sessao(sessao)
        return

    # 2. Verificar inatividade
    msg_inatividade = await _verificar_inatividade(sessao, whatsapp_id)
    if msg_inatividade:
        sessao.conversa.append({"role": "assistant", "content": msg_inatividade})
        await _salvar_e_enviar(sessao, whatsapp_id, msg_inatividade)
        return

    # 3. Detectar abandono/cancelamento
    if _detectar_abandono(texto):
        sessao.status = SessionStatus.PAUSADO
        sessao.motivo_pausa = "abandono voluntário"
        resposta = (
            "Sem problemas!  Seu cadastro foi salvo. "
            "Quando quiser retomar, é só me chamar aqui."
        )
        sessao.conversa.append({"role": "user", "content": texto})
        sessao.conversa.append({"role": "assistant", "content": resposta})
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return

    # 4. Sessão pausada → retomar
    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = SessionStatus.CLASSIFICANDO if not sessao.tipo_beneficio else SessionStatus.COLETANDO_DADOS
        sessao.motivo_pausa = None
        nome = sessao.dados_cliente.get("nome")
        if nome:
            resume_msg = f"Bem-vindo de volta, {nome}!  Vamos continuar de onde paramos?"
        else:
            resume_msg = "Bem-vindo de volta!  Vamos continuar de onde paramos?"
        sessao.conversa.append({"role": "assistant", "content": resume_msg})
        await _salvar_e_enviar(sessao, whatsapp_id, resume_msg)
        return

    # 5. Processar normalmente
    sessao.conversa.append({"role": "user", "content": texto})

    resposta = await processar(texto, sessao)
    if resposta is SILENT:
        await salvar_sessao(sessao)
        return
    sessao.conversa.append({"role": "assistant", "content": resposta})
    await _salvar_e_enviar(sessao, whatsapp_id, resposta)


async def processar_mensagem_midia(whatsapp_id: str, midia_id: str):
    """Processa o recebimento de uma imagem ou documento com OCR."""
    sessao = await _obter_ou_criar_sessao(whatsapp_id)

    # Cliente existente → OCR + extração silenciosa (sem enviar mensagem)
    admin_id = settings.admin_whatsapp or _bot_phone_number or ""
    if sessao.existing_client and _session_key(sessao.whatsapp_id) != admin_id:
        await processar_midia(sessao, midia_id)
        await salvar_sessao(sessao)
        return

    msg_inatividade = await _verificar_inatividade(sessao, whatsapp_id)
    if msg_inatividade:
        sessao.conversa.append({"role": "assistant", "content": msg_inatividade})
        await _salvar_e_enviar(sessao, whatsapp_id, msg_inatividade)
        return

    if sessao.status == SessionStatus.AGUARDANDO_ADVOGADO:
        await processar_midia(sessao, midia_id)
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        await salvar_sessao(sessao)
        return

    if sessao.status in (SessionStatus.COLETANDO_DADOS, SessionStatus.CLASSIFICANDO):
        msg_ocr = await processar_midia(sessao, midia_id)
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        sessao.conversa.append({"role": "assistant", "content": msg_ocr})
        await _salvar_e_enviar(sessao, whatsapp_id, msg_ocr)
        return

    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = SessionStatus.CLASSIFICANDO if not sessao.tipo_beneficio else SessionStatus.COLETANDO_DADOS
        sessao.motivo_pausa = None
        msg_ocr = await processar_midia(sessao, midia_id)
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        sessao.conversa.append({"role": "assistant", "content": msg_ocr})
        await _salvar_e_enviar(sessao, whatsapp_id, msg_ocr)
        return

    if sessao.status in (SessionStatus.CONCLUIDO, SessionStatus.ARQUIVADO):
        resposta = "Seu processo já foi concluído. Se precisar de ajuda, é só falar!"
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        sessao.conversa.append({"role": "assistant", "content": resposta})
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return

    if sessao.status == SessionStatus.REVISAO_ADVOGADO:
        resposta = "Seus documentos estão sendo processados. Em breve retornamos o contato."
        sessao.conversa.append({"role": "user", "content": f"[midia: {midia_id}]"})
        sessao.conversa.append({"role": "assistant", "content": resposta})
        await _salvar_e_enviar(sessao, whatsapp_id, resposta)
        return


@router.post("/zapsign")
@limiter.limit(settings.rate_limit_webhook)
async def webhook_zapsign(request: Request):
    """Recebe confirmação de assinatura do Zapsign."""
    body = await request.body()
    sig = request.headers.get("x-zapsign-signature")
    if not verificar_webhook_zapsign(body, sig):
        logger.warning("Webhook Zapsign rejeitado: assinatura inválida")
        raise HTTPException(status_code=403, detail="Assinatura inválida")
    try:
        payload = await request.json()
        evento = processar_zapsign_webhook(payload)
        logger.info("Zapsign webhook: %s", evento)

        if evento.get("evento") == "assinado":
            doc_id = evento.get("documento_id", "")
            signatario = evento.get("signatario", "")
            assinado_em = evento.get("assinado_em")
            event_key = f"{doc_id}_{evento.get('evento')}"

            # Encontrar sessão pelo documento_id (O(1) via índice reverso)
            whatsapp_id = zapsign_doc_index.get(doc_id)
            key = _session_key(whatsapp_id) if whatsapp_id else None
            sessao = sessoes_ativas.get(key) if key else None

            # Fallback: busca linear (sessões carregadas do disco sem índice)
            if not sessao:
                for s in sessoes_ativas.values():
                    if s.zapsign_documento_id == doc_id:
                        sessao = s
                        break

            if sessao and event_key in sessao.processed_zapsign_events:
                logger.info("Evento Zapsign %s já processado, ignorando", event_key)
                return {"status": "ok"}

            if sessao:
                if sessao.assinado_em:
                    logger.info("Sessão %s já assinada em %s, ignorando", sessao.whatsapp_id, sessao.assinado_em)
                    return {"status": "ok"}

                sessao.status = SessionStatus.CONCLUIDO
                sessao.assinado_em = assinado_em

                if event_key not in sessao.processed_zapsign_events:
                    sessao.processed_zapsign_events.append(event_key)
                    if len(sessao.processed_zapsign_events) > 50:
                        sessao.processed_zapsign_events = sessao.processed_zapsign_events[-25:]

                # Montar mensagem de confirmação com link de download se disponível
                mensagem = (
                    f" Documento assinado por {signatario}!\n\n"
                    "Recebemos sua assinatura digital com sucesso."
                )
                if sessao.documentos_gerados:
                    caminhos = [
                        d.get("path", "") for d in sessao.documentos_gerados
                        if d.get("path", "").endswith(".pdf")
                    ]
                    if caminhos:
                        mensagem += "\n\n Seus documentos:\n"
                        for c in caminhos:
                            mensagem += f"• {Path(c).name}\n"

                sessao.conversa.append({"role": "assistant", "content": mensagem})
                await enviar_mensagem(sessao.whatsapp_id, mensagem)
                await salvar_sessao(sessao)

        return {"status": "ok"}

    except Exception as e:
        logger.error("Erro no webhook Zapsign: %s", e)
        return {"status": "ok"}  # Sempre 200 para evitar retentativas


async def tarefa_arquivamento():
    """Tarefa de fundo que arquiva sessões inativas periodicamente."""
    while True:
        try:
            await arquivar_sessoes_inativas(sessoes_ativas)
        except Exception as e:
            logger.error("Erro na tarefa de arquivamento: %s", e)
        await asyncio.sleep(3600)  # Executa a cada hora


async def iniciar_carregamento_sessoes():
    """Carrega sessões do disco na inicialização."""
    sessoes = await carregar_todas_sessoes()
    sessoes_ativas.update(sessoes)
    logger.info("%d sessão(ões) carregada(s) do disco", len(sessoes))


# ── Rota administrativa: QR Code para conectar WhatsApp no OpenWA ──

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
    """Remove a sessão atual do WhatsApp e redireciona para obter novo QR.

    Requer token de administrador (igual ao ADMIN_PASSWORD do .env).
    """
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
    """Diagnóstico: verifica se o webhook está registrado e testa entrega."""
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

            # Testar webhook via API do OpenWA
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
    """Testa o envio de mensagem diretamente via OpenWA (útil para diagnóstico sem processar IA).

    Body: {"whatsapp_id": "557199999999", "texto": "teste"}
    """
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
    """Força o registro do webhook no OpenWA (para diagnóstico)."""
    from src.services.whatsapp import configurar_webhook
    from src.config import settings as cfg

    webhook_url = f"{cfg.app_url}/webhook/whatsapp"
    try:
        await configurar_webhook(webhook_url)
        return JSONResponse({"status": "ok", "webhook_url": webhook_url})
    except Exception as e:
        return JSONResponse({"status": "erro", "erro": str(e)}, status_code=500)


@router.get("/qr")
async def obter_qr_code(token: str = Query("")):
    """Retorna o QR code da sessão OpenWA para escaneamento no WhatsApp."""
    from src.services.whatsapp import obter_qr, criar_sessao, iniciar_sessao, deletar_sessao, configurar_webhook
    from src.services.whatsapp_openwa import extrair_qr_base64, resolver_uuid_sessao
    from src.config import settings as cfg

    token_param = f"?token={token}" if token else ""

    # 1. Garantir que o UUID da sessão está resolvido
    await resolver_uuid_sessao()

    # 2. Tentar obter QR
    qr_data = await obter_qr()
    status = qr_data.get("status", "")
    qr_base64 = extrair_qr_base64(qr_data)

    # 3. Se QR indisponível, tentar iniciar/criar sessão
    if not qr_base64 and status == "error":
        logger.info("QR: QR não disponível (status=error) — recriando sessão")
        # Deletar sessão existente (se houver) para começar do zero
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

        # Poll QR com retries (OpenWA pode demorar a gerar após start)
        for tentativa in range(6):
            await asyncio.sleep(2)
            qr_data = await obter_qr()
            qr_base64 = extrair_qr_base64(qr_data)
            status = qr_data.get("status", "")
            if qr_base64 or status in ("connected", "pending"):
                break
            logger.info("QR: aguardando geração (tentativa %d/6)", tentativa + 1)

    # Registrar webhook sempre (não apenas quando cria sessão)
    webhook_url = f"{cfg.app_url}/webhook/whatsapp"
    try:
        await configurar_webhook(webhook_url)
    except Exception as e_wh:
        logger.warning("QR: falha webhook: %s", e_wh)

    # 4. Renderizar página conforme estado
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
