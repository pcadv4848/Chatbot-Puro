"""Configurações centralizadas do projeto via variáveis de ambiente.

Usa pydantic-settings para carregar configurações de .env automaticamente.
"""
from pydantic_settings import BaseSettings # Warning: BaseSettings could not be resolved


class Settings(BaseSettings):
    """Configurações carregadas de .env com defaults seguros."""

    # ── App ──
    app_name: str = "ChatBot Previdenciário"
    debug: bool = False

    # ── Provider WhatsApp ──
    # "meta"   → WhatsApp Cloud API oficial (requer WHATSAPP_TOKEN)
    # "openwa" → OpenWA self-hosted (requer OPENWA_API_KEY + QR scan)
    whatsapp_provider: str = "openwa"

    # ── WhatsApp via Meta Cloud API (usado quando whatsapp_provider="meta") ──
    whatsapp_token: str = ""
    whatsapp_api_version: str = "v25.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""
    """WhatsApp Business Account ID (WABA). Necessário para consultar etiquetas."""

    # ── WhatsApp via OpenWA (usado quando whatsapp_provider="openwa") ──
    openwa_api_url: str = "http://openwa:2785/api"
    openwa_api_key: str = ""
    openwa_session_id: str = "chatbot-prev"
    webhook_verify_token: str = "PCADV"

    # ── DeepSeek (primário) ──
    # API nativa DeepSeek: https://api.deepseek.com
    # Modelos: deepseek-chat, deepseek-reasoner
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # ── Verboo (fallback) ──
    # API compatível com OpenAI: https://code.verboo.ai/router/v1
    verboo_api_key: str = ""
    verboo_endpoint: str = "https://code.verboo.ai/router/v1"
    verboo_model: str = "verboo-pro"

    # ── Google Gemini (opcional, desativado por padrão) ──
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── Anthropic / Claude (opcional, desativado por padrão) ──
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    # ── Banco de Dados ──
    # SQLite (local, padrão): sqlite+aiosqlite:///./data/chatbot.db
    # PostgreSQL (produção): postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str = "sqlite+aiosqlite:///./data/chatbot.db"

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── Zapsign (assinatura digital) ──
    zapsign_api_key: str = ""

    # ── Advogado Responsável ──
    advogado_nome: str = "Escritório Jurídico"
    advogado_email: str = ""
    advogado_whatsapp: str = ""

    # ── S3 / Storage (MinIO ou Cloudflare R2) ──
    storage_endpoint: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "chatbot-docs"

    # ── Retry / Tolerância a falhas ──
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    # ── App / Domínio público ──
    app_url: str = "https://chatbot-previdenciaro-production-7569.up.railway.app"

    # ── CORS ──
    cors_origins: str = "*"

    # ── Webhooks ──
    meta_webhook_secret: str = ""
    zapsign_webhook_secret: str = ""

    # ── Rate limiting ──
    rate_limit_webhook: str = "10/minute"

    # ── Admin Panel ──
    admin_username: str = "admin"
    admin_password: str = ""
    admin_whatsapp: str = ""
    """Número WhatsApp autorizado a usar comandos administrativos.
    Se vazio, usa o número do próprio bot (descoberto da sessão OpenWA)."""

    # ── Google Drive (upload automático de documentos) ──
    gdrive_credentials_file: str = ""
    """Caminho para o arquivo .json da service account do Google Drive.
    Alternativa ao GDRIVE_CREDENTIALS_JSON para ambientes onde o arquivo
    está disponível no sistema de arquivos (ex: Railway volume mount)."""
    gdrive_credentials_json: str = ""
    """JSON da service account do Google Drive.
    Deve ser copiado diretamente do arquivo .json baixado do Google Cloud Console.
    Se GDRIVE_CREDENTIALS_FILE estiver configurado, este é ignorado."""
    gdrive_folder_id: str = ""
    """ID da pasta no Google Drive onde os documentos serão armazenados.
    Se vazio, os arquivos são salvos na raiz do Drive da service account."""

    # ── Lembretes para conversas abandonadas ──
    reminder_cooldown_days: int = 3
    """Dias de inatividade antes de enviar o primeiro lembrete."""
    reminder_max_count: int = 2
    """Máximo de lembretes enviados para a mesma sessão."""
    reminder_interval_hours: int = 6
    """Intervalo entre verificações da tarefa de lembretes (em horas)."""

    # ── Segurança ──
    encrypt_key: str = ""
    max_ocr_retries: int = 3
    session_timeout_minutes: int = 60
    session_archive_days: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
