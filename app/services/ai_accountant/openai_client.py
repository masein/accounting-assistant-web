"""OpenAI-compatible LLM adapter.

Targets ``/v1/chat/completions`` — the de-facto OpenAI Chat Completions
shape spoken by OpenAI itself, MetisAI's ``/openai/v1`` endpoint,
LM Studio, Together, OpenRouter, and most other gateways. Uses async
``httpx`` directly (no SDK) so we work against any conformant endpoint
without per-vendor SDK quirks.

Tool calling follows OpenAI's `tools` / `tool_calls` / `role: "tool"`
conventions:

* Tool definitions are wrapped in ``{type: "function", function: {…}}``
* Assistant turns with tool calls carry ``message.tool_calls`` (a list
  of ``{id, type, function: {name, arguments}}`` — arguments is a JSON
  *string*, not a parsed object)
* Tool results come back as separate ``{role: "tool", tool_call_id,
  content}`` messages

The orchestrator never sees any of this — it works in the normalized
``ChatMessage`` / ``LLMResponse`` shapes from ``llm_protocol``.

**Important caveat for local LM Studio users:** tool calling on local
models is hit-and-miss. Pick a tool-call-capable model (Qwen2.5-Coder,
Llama 3.1+ Instruct, Mistral Small 3, Hermes 3, …). Models without
trained tool-calling support will either silently never call a tool or
hallucinate calls with malformed arguments.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.ai_runtime import resolve_active_ai_backend

from .llm_protocol import (
    ChatMessage,
    LLMClient,
    LLMClientError,
    LLMResponse,
    LLMUsage,
    ToolCall,
    tool_to_openai,
)

logger = logging.getLogger(__name__)

# OpenAI's spec lets servers stream very long completions; for our
# tool-use loop we keep individual turns short so a 90 s ceiling is
# comfortable. Bumps if your local LM Studio model is slow.
DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Wire translation: ChatMessage list → OpenAI messages
# ---------------------------------------------------------------------------


def _chat_messages_to_openai_wire(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Build the OpenAI ``messages`` array from normalized ``ChatMessage``s.

    Mapping:
        user   → {"role": "user", "content": text}
        tool   → {"role": "tool", "tool_call_id": id, "content": text}
        assistant text only → {"role": "assistant", "content": text}
        assistant + tool_calls → {"role": "assistant", "content": text|null,
                                  "tool_calls": [{id, type:"function",
                                                  function:{name, arguments}}]}
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text or ""})
        elif m.role == "tool":
            # OpenAI expects ``content`` as a string for tool messages.
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.text or "",
            })
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant"}
            # OpenAI allows null content when tool_calls is present.
            entry["content"] = m.text if m.text else None
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
    "function_call": "tool_use",  # legacy
}


def _parse_response(body: dict[str, Any]) -> LLMResponse:
    """Pick the first choice out of an OpenAI Chat Completions response
    and turn it into our normalized ``LLMResponse``."""
    choices = body.get("choices") or []
    if not choices:
        raise LLMClientError("OpenAI response had no choices")
    choice = choices[0]
    msg = choice.get("message") or {}
    text = msg.get("content")

    tool_calls: list[ToolCall] = []
    for tc in (msg.get("tool_calls") or []):
        if tc.get("type") not in (None, "function"):
            # Spec leaves room for non-function tool types in the future;
            # ignore anything we don't understand rather than crashing.
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                # Some models (especially weaker locals) return malformed
                # JSON. Surface the raw string so the orchestrator can mark
                # it as a tool error.
                args = {"_raw_arguments": raw_args, "_parse_error": True}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        tool_calls.append(ToolCall(id=str(tc.get("id") or ""), name=str(name), input=args))

    assistant_msg = ChatMessage(
        role="assistant",
        text=text if text else None,
        tool_calls=tool_calls,
    )
    raw_reason = (choice.get("finish_reason") or "stop").lower()
    stop_reason = _FINISH_REASON_MAP.get(raw_reason, raw_reason)

    usage_dict = body.get("usage") or {}
    usage = LLMUsage(
        input_tokens=int(usage_dict.get("prompt_tokens") or 0),
        output_tokens=int(usage_dict.get("completion_tokens") or 0),
        # OpenAI's automatic prompt caching surfaces cached tokens via
        # ``prompt_tokens_details.cached_tokens`` when available.
        cache_read_input_tokens=int(
            (usage_dict.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        ),
        cache_creation_input_tokens=0,  # OpenAI doesn't expose cache writes
    )
    return LLMResponse(message=assistant_msg, stop_reason=stop_reason, usage=usage)


# ---------------------------------------------------------------------------
# Endpoint resolution + auth header
# ---------------------------------------------------------------------------


def _chat_completions_url(base_url: str) -> str:
    """Normalize a base URL to the chat/completions endpoint. Handles:
        https://api.openai.com           → /v1/chat/completions
        https://api.metisai.ir/openai/v1 → /chat/completions
        http://host.docker.internal:1234 → /v1/chat/completions  (LM Studio)
        https://x.example/chat/completions → no-op
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return "https://api.openai.com/v1/chat/completions"
    if base.endswith("/chat/completions"):
        return base
    # Heuristic: if the base already ends with ``/v<N>`` or contains ``/openai/`` we
    # append only ``/chat/completions``; otherwise we add the canonical ``/v1`` segment.
    if base.endswith(("/v1", "/v2")) or "/openai/" in base or "/wrapper/" in base:
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _auth_headers(cfg: dict[str, str]) -> dict[str, str]:
    """Build the auth header from the active provider's config. Empty key
    → no header (LM Studio out of the box, for instance)."""
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return {}
    header = (cfg.get("api_key_header") or "Authorization").strip() or "Authorization"
    prefix = (cfg.get("api_key_prefix") or "").strip()
    if header.lower() == "authorization" and prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    return {header: f"{prefix}{key}" if prefix else key}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenAILLMClient(LLMClient):
    """OpenAI-compatible chat client. Reads its provider config from
    ``resolve_active_ai_backend()`` so it picks up whatever the user has
    configured in the OpenAI-shape slot of the Settings page (Metis,
    LM Studio, Custom, OpenAI direct)."""

    shape = "openai"

    async def chat(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]],
        messages: list[ChatMessage],
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> LLMResponse:
        cfg = resolve_active_ai_backend()
        # If the active backend isn't an OpenAI-shape one (it can also be
        # 'anthropic'), the caller is misconfigured — fail with a clear
        # message rather than sending a bogus request.
        if cfg.get("provider") == "anthropic":
            raise LLMClientError(
                "AI Chat is set to OpenAI shape but the active default provider "
                "is 'anthropic'. Either switch the AI provider to an OpenAI-shape "
                "profile (Metis, LM Studio, Custom) or set the chat shape back to "
                "Anthropic in Settings."
            )
        api_key = (cfg.get("api_key") or "").strip()
        # LM Studio does not require a key, so missing-key is only fatal
        # when the URL is clearly a hosted provider.
        base_url = (cfg.get("base_url") or "").strip()
        if not api_key and ("openai.com" in base_url or "metisai.ir" in base_url):
            raise LLMClientError(
                f"No API key configured for the OpenAI-shape provider ({cfg.get('provider')}). "
                f"Set it in Settings → AI providers."
            )

        chosen_model = (model or cfg.get("model") or "gpt-4o-mini").strip()
        url = _chat_completions_url(base_url)
        headers = {"Content-Type": "application/json"}
        headers.update(_auth_headers(cfg))

        wire_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        wire_messages.extend(_chat_messages_to_openai_wire(messages))

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [tool_to_openai(t) for t in tools]
            payload["tool_choice"] = "auto"

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
                    r = await client.post(url, headers=headers, json=payload)
            except httpx.ConnectError as e:
                last_error = e
                logger.warning("openai-shape: connection error (attempt %d/%d): %s",
                               attempt, MAX_RETRIES, e)
                continue
            except httpx.ReadTimeout as e:
                last_error = e
                logger.warning("openai-shape: read timeout (attempt %d/%d)",
                               attempt, MAX_RETRIES)
                continue

            if r.status_code == 401:
                raise LLMClientError(
                    f"OpenAI-shape provider rejected the API key (401). "
                    f"URL: {url}"
                )
            if r.status_code == 403:
                raise LLMClientError(
                    f"OpenAI-shape provider denied access (403) — check that the "
                    f"key has permission for model {chosen_model!r}."
                )
            if r.status_code == 404:
                raise LLMClientError(
                    f"OpenAI-shape endpoint not found (404) at {url}. Verify the "
                    f"Base URL in Settings — should look like "
                    f"'https://.../openai/v1' or 'https://api.openai.com'."
                )
            if r.status_code == 429:
                logger.warning("openai-shape: rate limited (attempt %d/%d)",
                               attempt, MAX_RETRIES)
                last_error = LLMClientError(
                    "Rate limit reached on the OpenAI-shape provider. Try again shortly."
                )
                continue
            if r.status_code >= 500:
                logger.warning("openai-shape: %d server error (attempt %d/%d)",
                               r.status_code, attempt, MAX_RETRIES)
                last_error = LLMClientError(
                    f"OpenAI-shape provider returned {r.status_code}. Try again shortly."
                )
                continue
            if r.status_code != 200:
                try:
                    body = r.json()
                    msg = (body.get("error") or {}).get("message") or r.text[:200]
                except Exception:
                    msg = r.text[:200]
                raise LLMClientError(f"OpenAI-shape error {r.status_code}: {msg}")

            try:
                body = r.json()
            except json.JSONDecodeError as e:
                raise LLMClientError(f"OpenAI-shape returned non-JSON: {r.text[:200]}") from e

            response = _parse_response(body)
            logger.info(
                "ai-accountant turn shape=openai model=%s stop=%s input=%d cache_read=%d output=%d",
                chosen_model, response.stop_reason, response.usage.input_tokens,
                response.usage.cache_read_input_tokens, response.usage.output_tokens,
            )
            return response

        # Exhausted retries.
        raise LLMClientError(
            f"OpenAI-shape provider unreachable after {MAX_RETRIES} attempts: {last_error}"
        )
