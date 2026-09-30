"""
Call OpenAI-compatible backends (LM Studio, MetisAI, …): the backend's URL,
model and headers, and one metered POST with retries. Used by the statement
categoriser's model tier (app/services/statement_llm_categorizer.py).

The old voucher chat (POST /transactions/chat, /transactions/suggest) that
this module was written for is gone — the AI accountant (app/services/
ai_accountant) replaced it.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from app.services.ai_usage import metered_llm, usage_from_openai

from app.core.ai_runtime import resolve_active_ai_backend
from app.core.config import settings


# Thinking models can take 90+ seconds; allow longer and retry on timeout
LM_STUDIO_TIMEOUT = 180.0
LM_STUDIO_MAX_ATTEMPTS = 3
LM_STUDIO_RETRY_DELAY = 3.0


class AISuggestError(Exception):
    """Raised when LM Studio is unreachable or returns invalid data."""
    pass


def _resolve_ai_base_model() -> tuple[str, str]:
    cfg = resolve_active_ai_backend()
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    model = (cfg.get("model") or settings.lm_studio_model or "qwen/qwen3-4b-thinking-2507").strip()
    return base, model


def _chat_completions_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if "/openai/" in b or "/wrapper/" in b or re.search(r"/v\d+$", b):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def _resolve_ai_headers() -> dict[str, str]:
    cfg = resolve_active_ai_backend()
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return {}
    header = (cfg.get("api_key_header") or "Authorization").strip() or "Authorization"
    prefix = (cfg.get("api_key_prefix") or "").strip()
    if header.lower() == "authorization" and prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    value = f"{prefix}{key}" if prefix else key
    return {header: value}


async def _post_lm_studio(url: str, payload: dict[str, Any], base: str, headers: dict[str, str] | None = None,
                          *, purpose: str = "suggest") -> dict[str, Any]:
    """
    POST to LM Studio with retries on timeout, connection errors, and 503/429.
    Raises AISuggestError with a clear message after retries are exhausted.
    Every attempt is metered (app/services/ai_usage.py) under ``purpose``.
    """
    last_error: Exception | None = None
    provider = resolve_active_ai_backend().get("provider") or "openai-compatible"
    model = str(payload.get("model") or "")
    for attempt in range(LM_STUDIO_MAX_ATTEMPTS):
        try:
            async with metered_llm(provider, model, purpose) as meter, \
                    httpx.AsyncClient(timeout=LM_STUDIO_TIMEOUT) as client:
                r = await client.post(url, json=payload, headers=headers or None)
                r.raise_for_status()
                body = r.json()
                meter.ok(usage_from_openai(body), prompt=payload.get("messages"),
                         output=((body.get("choices") or [{}])[0].get("message") or {}).get("content"))
                return body
        except httpx.TimeoutException as e:
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                "AI backend did not respond in time. The model may be busy or slow; try again."
            ) from e
        except httpx.ConnectError as e:
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                f"Cannot reach AI backend at {base}. Check AI_BASE_URL / LM_STUDIO_BASE_URL."
            ) from e
        except httpx.HTTPStatusError as e:
            if (e.response.status_code in (429, 500, 502, 503, 504)) and attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                last_error = e
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                f"AI backend returned {e.response.status_code} at {url}. Check model name/API key."
            ) from e
        except httpx.TransportError as e:
            # Covers transient upstream issues such as connection resets / channel errors.
            last_error = e
            if attempt < LM_STUDIO_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LM_STUDIO_RETRY_DELAY)
                continue
            raise AISuggestError(
                "Connection to AI backend was interrupted. Please try again."
            ) from e
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            raise AISuggestError(f"Request to AI backend failed: {e!s}") from e
    raise AISuggestError(
        "AI backend did not respond after retries. Try again later."
    ) from last_error
