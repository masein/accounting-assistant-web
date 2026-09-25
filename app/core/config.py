from __future__ import annotations

import logging
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)

_INSECURE_DEFAULTS = {"change-this-in-production", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@db:5432/accounting"
    # Restricted role the web server connects as (app/db/roles.py). Empty
    # password = off: everything runs as DATABASE_URL's user, as before.
    app_db_user: str = "aa_app"
    app_db_password: str = ""
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
    # Chat / tool-calling model. Benchmarked on our own agent scenarios
    # (scripts/model_eval.py chat, 2026-09-24): gpt-4o-mini never raised the
    # proposal card for a Persian Toman expense or for a statement finding
    # (0/3 each); gpt-4.1-mini did, at ~9 s a turn — the fastest of the
    # candidates — for about $0.015 a turn. gpt-5-mini/nano were 2-5× slower
    # (reasoning), gpt-4.1-nano unreliable. gpt-5.6-luna is a cheaper
    # alternative ($0.008/turn) but 2× slower per turn.
    metis_model: str = "gpt-4.1-mini"
    metis_api_key: str | None = None
    # OCR/document extraction uses a vision model separate from the chat
    # model. Measured on a real 5-page Mellat statement (2026-09-24,
    # scripts/model_eval.py ocr): gemini-3.7-flash read all 36 rows with a
    # fully consistent running balance in 22 s for $0.03; gemini-2.5-pro (the
    # previous default) took 96 s, $0.20, and mis-read four rows (a dropped
    # zero, a flipped direction). gemini-2.5-flash / flash-lite returned
    # non-JSON; gpt-4o misreads Persian numerals (3→2, 1404→1401) and
    # gpt-4o-mini concatenates digits. Chain: primary Gemini → secondary
    # Gemini → OpenAI-compatible gpt-4o.
    ocr_model: str = "gemini-3.7-flash"
    ocr_gemini_fallback_model: str = "gemini-2.5-pro"
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
    # --- Outgoing mail (DirectAdmin on netixsystem.com, or any SMTP host) ---
    # Mail is OFF whenever smtp_host is unset, which is also what keeps the
    # test suite from ever touching the network.
    smtp_host: str | None = None
    # 587 = STARTTLS (the usual DirectAdmin submission port); 465 = implicit
    # SSL, which needs smtp_use_ssl=true since TLS is negotiated on connect.
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_ssl: bool = False
    smtp_starttls: bool = True
    smtp_timeout: int = 20
    # Envelope sender. Defaults to smtp_user, which on DirectAdmin is the full
    # mailbox address; set it explicitly when sending as a different address.
    smtp_from: str | None = None
    smtp_from_name: str = "Accounting Assistant"
    # Absolute base URL used in links we email out (verification, etc.).
    # A relative link is useless in an inbox, so this must be set for mail
    # to be worth sending.
    app_public_url: str = "http://localhost:8000"
    # Fixed recipient for the operator alert channel (/notifications/check).
    # Per-user mail (verification, digests) addresses the user instead.
    smtp_to: str | None = None
    auth_secret: str = "change-this-in-production"
    # PBKDF2-HMAC-SHA256 work factor. 600k is the current OWASP figure;
    # existing hashes carry their own count and are upgraded on next login
    # (see app/core/auth.py), so this can be raised safely over time.
    # Tests lower it — hashing cost otherwise dominates the suite.
    password_hash_iterations: int = 600_000
    # Public account creation. OFF by default and deliberately so: this app
    # is deployed for firms, and a deployment that silently started accepting
    # strangers' signups would be a security regression. Operators opt in.
    allow_self_signup: bool = False
    # Ask the LLM to place statement rows the deterministic categorizer
    # can't. Costs one batched call per import and sends narrations to the
    # configured backend, so it self-disables when no backend is set — and
    # the test suite turns it off outright to stay hermetic.
    statement_llm_categorization: bool = True
    auth_cookie_name: str = "aa_session"
    auth_session_hours: int = 24
    # Whether the session cookie carries the `Secure` flag. Unset (None) →
    # follow the request scheme (Secure only over HTTPS), so plain-HTTP access
    # (e.g. http://SERVER_IP:8000 before a TLS proxy is in front) still works.
    # Set AUTH_COOKIE_SECURE=true to force it on behind a TLS-terminating proxy
    # that forwards to the app over HTTP.
    auth_cookie_secure: bool | None = None
    # In-process background jobs (recurring postings, feed refresh, digest).
    # See app/jobs/scheduler.py. Off → nothing runs unless a browser triggers it.
    # Read the client IP from X-Forwarded-For (audit rows, per-IP limits). Only
    # for deployments whose proxy is NOT declared to uvicorn via
    # --forwarded-allow-ips; otherwise anyone can forge the header.
    trust_proxy_headers: bool = False
    scheduler_enabled: bool = True
    scheduler_digest_hour: int = 8  # server local time, 0-23
    # Other workers pick up an AI provider change saved by the super-admin
    # within this many seconds (roadmap §2.2). 0 disables the check.
    ai_config_refresh_seconds: int = 15
    # --- Observability (roadmap §2.4) ---
    # json: one JSON object per line (ship to Loki/ELK); text: human-readable.
    # Default: json in prod, text elsewhere.
    log_format: str | None = None
    log_level: str = "INFO"
    # Error reporting (Sentry or a compatible GlitchTip). Empty = off.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    # Bearer token a Prometheus scraper must send to GET /metrics. Empty =
    # the endpoint answers 404.
    metrics_token: str | None = None


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
