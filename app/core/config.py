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
    # Default to Metis (hosted, OpenAI-compatible) so a fresh deployment has a
    # working AI out of the box (needs METIS_API_KEY). LM Studio remains an
    # explicit opt-in for local dev — it points at a local server that doesn't
    # exist on a hosted box.
    ai_provider: str = "metis"  # metis | lmstudio | anthropic | custom
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
    # OCR/document extraction uses a stronger vision model than the
    # conversational chat. Tested on real Persian invoices: gpt-4o-mini
    # concatenates digits into garbage, gpt-4o misreads Persian numerals
    # (3→2, 1404→1401), but gemini-2.5-pro reads them exactly. So OCR
    # defaults to Gemini (via Metis's Google-format wrapper) and falls back
    # to the OpenAI-compatible gpt-4o path if Gemini is unavailable.
    ocr_model: str = "gemini-2.5-pro"
    ocr_fallback_model: str = "gpt-4o"
    # Metis exposes Gemini at Google's native generateContent endpoint
    # (x-goog-api-key header), separate from the OpenAI-compatible path.
    gemini_base_url: str = "https://api.metisai.ir/v1beta"
    # Backward-compatible LM Studio defaults
    lm_studio_base_url: str = "http://host.docker.internal:1234"
    # Model name as shown in LM Studio (e.g. qwen/qwen3-4b, lmstudio-community/granite-4-7b). Use non-"thinking" for speed on 16GB Mac.
    lm_studio_model: str = "qwen/qwen3-4b-thinking-2507"
    # Anthropic (Claude) — separate code path because the API is not OpenAI-compatible.
    # Default to Opus 4.6 for the AI accountant: correctness matters more than
    # token cost on bookkeeping writes. Override per-deployment via env var.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-6"
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_max_tokens: int = 8192
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
    # Whether the session cookie carries the `Secure` flag. Unset (None) →
    # follow the request scheme (Secure only over HTTPS), so plain-HTTP access
    # (e.g. http://SERVER_IP:8000 before a TLS proxy is in front) still works.
    # Set AUTH_COOKIE_SECURE=true to force it on behind a TLS-terminating proxy
    # that forwards to the app over HTTP.
    auth_cookie_secure: bool | None = None


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
