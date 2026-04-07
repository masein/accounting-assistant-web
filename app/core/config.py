from __future__ import annotations

import logging
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)

_INSECURE_DEFAULTS = {"change-this-in-production", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@db:5432/accounting"
    app_env: str = "dev"
    app_cors_origins: str = "http://localhost:8000"
    ai_provider: str = "lmstudio"  # lmstudio | metis | custom
    # AI backend (OpenAI-compatible): LM Studio / Metis / others
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = None
    ai_api_key_header: str = "Authorization"
    ai_api_key_prefix: str = "Bearer"
    # Metis defaults
    metis_base_url: str = "https://api.metisai.ir/openai/v1"
    metis_model: str = "gpt-4o-mini"
    metis_api_key: str | None = None
    # Backward-compatible LM Studio defaults
    lm_studio_base_url: str = "http://host.docker.internal:1234"
    # Model name as shown in LM Studio (e.g. qwen/qwen3-4b, lmstudio-community/granite-4-7b). Use non-"thinking" for speed on 16GB Mac.
    lm_studio_model: str = "qwen/qwen3-4b-thinking-2507"
    slack_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_to: str | None = None
    auth_secret: str = "change-this-in-production"
    auth_cookie_name: str = "aa_session"
    auth_session_hours: int = 24


settings = Settings()

# --- Enforce CORS safety in production ------------------------------------------
if settings.app_env == "prod" and settings.app_cors_origins.strip() == "*":
    raise RuntimeError(
        "APP_CORS_ORIGINS must not be '*' in production. "
        "Set it to your frontend domain(s), e.g. APP_CORS_ORIGINS=https://accounting.example.com"
    )

# --- Enforce database credential safety -----------------------------------------
if settings.app_env == "prod" and "postgres:postgres@" in settings.database_url:
    raise RuntimeError(
        "Default database credentials detected in production. "
        "Set DATABASE_URL with strong credentials in .env."
    )

# --- Enforce auth_secret safety -------------------------------------------------
if settings.auth_secret in _INSECURE_DEFAULTS:
    if settings.app_env in ("dev", "test"):
        _generated = secrets.token_urlsafe(32)
        _logger.warning(
            "AUTH_SECRET is not set — generated a random ephemeral secret. "
            "Sessions will NOT survive restarts. Set AUTH_SECRET in .env for persistence."
        )
        settings.auth_secret = _generated
    else:
        raise RuntimeError(
            "AUTH_SECRET must be set to a strong random value in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
