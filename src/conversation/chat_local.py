import asyncio
import logging
import uuid

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from src.agents.supervisor import processar, SILENT
from src.conversation.state import SessionState, SessionStatus
from src.engine.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat-local"])

_chat_sessions: dict[str, SessionState] = {}

_CHAT_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChatBot Puro</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f0f2f5; height: 100vh; display: flex; justify-content: center; }
  #app { width: 100%; max-width: 480px; display: flex; flex-direction: column;
         height: 100vh; background: #fff; box-shadow: 0 0 20px rgba(0,0,0,.1); }
  header { background: #075e54; color: #fff; padding: 16px 20px; text-align: center;
           font-size: 18px; font-weight: 600; }
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex;
              flex-direction: column; gap: 8px; background: #e5ddd5; }
  .msg { max-width: 85%; padding: 8px 14px; border-radius: 8px;
         font-size: 14px; line-height: 1.4; word-wrap: break-word; white-space: pre-wrap; }
  .user { align-self: flex-end; background: #dcf8c6; }
  .bot { align-self: flex-start; background: #fff; }
  .timestamp { font-size: 11px; color: #999; margin-top: 4px; text-align: right; }
  #input-area { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #ddd;
                background: #f0f0f0; align-items: center; }
  #input-area input[type=text] { flex: 1; padding: 10px 14px; border: none; border-radius: 24px;
                                 font-size: 14px; outline: none; }
  #input-area button { width: 44px; height: 44px; border: none; border-radius: 50%;
                       background: #075e54; color: #fff; font-size: 20px; cursor: pointer;
                       display: flex; align-items: center; justify-content: center; }
  #input-area button:disabled { opacity: .5; cursor: not-allowed; }
  #input-area label { width: 44px; height: 44px; border-radius: 50%;
                      background: #999; color: #fff; font-size: 18px; cursor: pointer;
                      display: flex; align-items: center; justify-content: center; }
  #input-area input[type=file] { display: none; }
  .loading::after { content: '...'; animation: dots 1.5s steps(4) infinite; }
  @keyframes dots { 0%,20% { content: ''; } 40% { content: '.'; } 60% { content: '..'; } 80%,100% { content: '...'; } }
  .error { color: #c33; font-size: 12px; text-align: center; padding: 4px; }
</style>
</head>
<body>
<div id="app">
  <header> ChatBot Puro</header>
  <div id="messages"></div>
  <div id="input-area">
    <label for="file-input" title="Enviar imagem"></label>
    <input type="file" id="file-input" accept="image/*" onchange="sendMedia(event)">
    <input type="text" id="msg-input" placeholder="Digite sua mensagem..." autofocus
           onkeydown="if(event.key==='Enter') sendMessage()">
    <button id="send-btn" onclick="sendMessage()"></button>
  </div>
</div>
<script>
const SESSION_KEY = 'chatbot_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || crypto.randomUUID();
localStorage.setItem(SESSION_KEY, sessionId);
let loading = false;

async function sendMessage() {
  const input = document.getElementById('msg-input');
  const text = input.value.trim();
  if (!text || loading) return;
  input.value = '';
  addMessage(text, 'user');
  setLoading(true);
  try {
    const res = await fetch('/chat/api/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({session_id: sessionId, texto: text}),
    });
    const data = await res.json();
    addMessage(data.resposta, 'bot');
  } catch (e) {
    addMessage('Erro de conexão. Tente novamente.', 'bot');
  } finally {
    setLoading(false);
    document.getElementById('msg-input').focus();
  }
}

async function sendMedia(event) {
  const file = event.target.files[0];
  if (!file || loading) return;
  event.target.value = '';
  addMessage(' ' + file.name, 'user');
  setLoading(true);
  const form = new FormData();
  form.append('session_id', sessionId);
  form.append('file', file);
  try {
    const res = await fetch('/chat/api/media', { method: 'POST', body: form });
    const data = await res.json();
    addMessage(data.resposta, 'bot');
  } catch (e) {
    addMessage('Erro ao processar imagem. Tente novamente.', 'bot');
  } finally {
    setLoading(false);
    document.getElementById('file-input').value = '';
  }
}

function addMessage(text, role) {
  const box = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  const time = document.createElement('div');
  time.className = 'timestamp';
  time.textContent = new Date().toLocaleTimeString();
  div.appendChild(time);
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function setLoading(v) {
  loading = v;
  document.getElementById('send-btn').disabled = v;
  document.getElementById('msg-input').disabled = v;
}
</script>
</body>
</html>"""


def _get_or_create_session(session_id: str) -> SessionState:
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = SessionState(whatsapp_id=f"chat_{session_id}")
        logger.info("Nova sessão local: %s", session_id)
    return _chat_sessions[session_id]


def _limpar_sessoes_antigas():
    import time
    from datetime import datetime, timezone

    agora = datetime.now(timezone.utc)
    limite = agora.timestamp() - 3600
    for sid, sessao in list(_chat_sessions.items()):
        try:
            ultima = datetime.fromisoformat(sessao.ultima_atividade).timestamp()
            if ultima < limite:
                del _chat_sessions[sid]
                logger.info("Sessão local expirada removida: %s", sid)
        except (ValueError, TypeError):
            pass


@router.get("", response_class=HTMLResponse)
async def pagina_chat():
    _limpar_sessoes_antigas()
    return _CHAT_HTML


PALAVRAS_ABANDONO = [
    "deixar pra lá", "deixa pra lá", "depois eu vejo", "depois eu falo",
    "agora não", "não quero mais", "cancelar", "desistir", "cansei",
    "depois resolvo", "vou deixar", "deixa quieto", "esquece",
    "não é agora", "outro dia", "sem tempo", "não quero",
]

PALAVRAS_NOVO_ATENDIMENTO = [
    "novo atendimento", "começar de novo", "reiniciar", "resetar",
    "novo cadastro", "do zero", "outro benefício", "outra pessoa",
    "limpar", "zerar",
]


def _detectar(texto: str, lista: list[str]) -> bool:
    texto_lower = texto.lower().strip()
    for frase in lista:
        if frase in texto_lower:
            return True
    return False


@router.post("/api/send")
@limiter.limit("20/minute")
async def enviar_mensagem(request: Request, session_id: str = Form(...), texto: str = Form(...)):
    sessao = _get_or_create_session(session_id)

    if _detectar(texto, PALAVRAS_ABANDONO):
        sessao.status = SessionStatus.PAUSADO
        sessao.motivo_pausa = "abandono voluntário"
        resposta = (
            "Sem problemas. Seu cadastro foi salvo. "
            "Quando quiser retomar, é só me chamar aqui."
        )
        return JSONResponse({"resposta": resposta})

    if _detectar(texto, PALAVRAS_NOVO_ATENDIMENTO):
        _chat_sessions[session_id] = SessionState(whatsapp_id=f"chat_{session_id}")
        resposta = (
            "Pronto. Vamos comecar do zero. "
            "Me conte o que você precisa:"
        )
        return JSONResponse({"resposta": resposta})

    if sessao.status == SessionStatus.PAUSADO:
        sessao.status = (
            SessionStatus.CLASSIFICANDO
            if not sessao.tipo_beneficio
            else SessionStatus.COLETANDO_DADOS
        )
        sessao.motivo_pausa = None
        nome = sessao.dados_cliente.get("nome")
        if nome:
            resposta = f"Bem-vindo de volta, {nome}. Vamos continuar de onde paramos?"
        else:
            resposta = "Bem-vindo de volta. Vamos continuar de onde paramos?"
        return JSONResponse({"resposta": resposta})

    resposta = await processar(texto, sessao)
    if resposta is SILENT:
        return JSONResponse({"resposta": ""})
    return JSONResponse({"resposta": resposta})



