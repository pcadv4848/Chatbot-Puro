from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ChatBot Puro"
    debug: bool = False

    # ── Provider WhatsApp ──
    whatsapp_provider: str = "openwa"

    # ── WhatsApp via Meta Cloud API ──
    whatsapp_token: str = ""
    whatsapp_api_version: str = "v25.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""

    # ── WhatsApp via OpenWA ──
    openwa_api_url: str = "http://openwa:2785/api"
    openwa_api_key: str = ""
    openwa_session_id: str = "chatbot-puro"
    webhook_verify_token: str = "PCADV"

    # ── DeepSeek ──
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # ── Verboo (fallback) ──
    verboo_api_key: str = ""
    verboo_endpoint: str = "https://code.verboo.ai/router/v1"
    verboo_model: str = "verboo-pro"

    # ── Google Gemini (opcional) ──
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── Anthropic / Claude (opcional) ──
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    # ── Banco de Dados ──
    database_url: str = "sqlite+aiosqlite:///./data/chatbot.db"

    # ── Advogado Responsável ──
    advogado_nome: str = "Escritório Jurídico"
    advogado_email: str = ""
    advogado_whatsapp: str = ""

    # ── Retry / Tolerância a falhas ──
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    # ── App / Domínio público ──
    app_url: str = "https://chatbot-puro.up.railway.app"

    # ── CORS ──
    cors_origins: str = "*"

    # ── Rate limiting ──
    rate_limit_webhook: str = "10/minute"

    # ── Admin Panel ──
    admin_username: str = "admin"
    admin_password: str = ""
    admin_whatsapp: str = ""

    # ── Lembretes ──
    reminder_cooldown_days: int = 3
    reminder_max_count: int = 2
    reminder_interval_hours: int = 6

    # ── Segurança ──
    encrypt_key: str = ""
    max_ocr_retries: int = 3
    session_timeout_minutes: int = 60
    session_archive_days: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
