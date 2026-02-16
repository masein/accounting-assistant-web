from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@db:5432/accounting"
    app_env: str = "dev"
    app_cors_origins: str = "*"
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
    lm_studio_base_url: str = "http://192.168.0.152:1234"
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


settings = Settings()
