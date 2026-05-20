"""Async Anthropic client for the AI accountant feature.

Uses the official ``anthropic`` SDK (not raw httpx) so we get:

* typed exception classes (``RateLimitError``, ``OverloadedError``, …)
* automatic retry/backoff for 429 + 5xx (default 2 retries; bumped to 3)
* explicit `messages.create()` schema and response parsing
* ``AsyncAnthropic`` plays nicely with the rest of the app's async I/O

Prompt caching is applied to the **system prompt** and the **last tool
definition** because both are stable across the turns of a single chat
session — only the messages array changes per turn. Caching the prefix
typically cuts input cost ~90% on follow-up turns.

Returns the raw ``Message`` so the orchestrator can:

  * detect ``stop_reason == "tool_use"`` → execute each tool, append the
    results to ``messages``, and call back into ``chat_once``; or
  * detect ``stop_reason == "end_turn"`` → surface the assistant's final
    text to the user.

The orchestrator owns the agentic loop; this function is one round trip.
"""
from __future__ import annotations

import logging
from typing import Any

import anthropic
from anthropic.types import Message

from app.core.ai_runtime import resolve_anthropic_config

logger = logging.getLogger(__name__)


class AIAccountantError(Exception):
    """Wraps Anthropic API failures so the FastAPI layer can return a
    400/502 with a user-readable message instead of leaking provider
    internals to the UI."""


def _client() -> anthropic.AsyncAnthropic:
    cfg = resolve_anthropic_config()
    if not cfg["api_key"]:
        raise AIAccountantError(
            "ANTHROPIC_API_KEY is not configured. Set it in the .env file or via "
            "/admin/ai-config before using the AI accountant."
        )
    return anthropic.AsyncAnthropic(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"] or None,
        # 60 s is enough headroom for adaptive thinking on tool-use turns
        # without making a dead network drag on indefinitely. Bumped retries
        # from the SDK default of 2 to 3 — agentic loops are bursty.
        timeout=60.0,
        max_retries=3,
    )


def _system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    """Wrap the system prompt as a list of cacheable text blocks. The
    single block carries ``cache_control: {type: "ephemeral"}`` so the
    system+tools prefix is reused across subsequent turns at ~0.1× cost."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool with ``cache_control`` so the entire tools array
    is part of the cached prefix. (Tools render before `system` in the
    prefix; a marker on either side caches everything up to that point.)"""
    if not tools:
        return []
    out = [dict(t) for t in tools]
    out[-1]["cache_control"] = {"type": "ephemeral"}
    return out


async def chat_once(
    *,
    system_prompt: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 8192,
    effort: str = "high",
    thinking_enabled: bool = True,
) -> Message:
    """Run one turn of the AI accountant tool-use loop.

    ``messages`` is the full chat history (the API is stateless, so we
    re-send it every turn). Each entry follows the Anthropic schema:
    ``{"role": "user" | "assistant", "content": "..."}`` or, on
    follow-up turns after a tool call, the assistant's previous
    ``response.content`` and a user message of ``tool_result`` blocks.

    Returns the ``Message`` response unmodified — the orchestrator
    decides whether to loop again or finish based on ``stop_reason``.
    """
    cfg = resolve_anthropic_config()
    chosen_model = (model or cfg.get("model") or "claude-opus-4-7").strip()

    request_kwargs: dict[str, Any] = {
        "model": chosen_model,
        "max_tokens": max_tokens,
        "system": _system_blocks(system_prompt),
        "tools": _cached_tools(tools),
        "messages": messages,
        "output_config": {"effort": effort},
    }
    if thinking_enabled:
        # Adaptive thinking lets Claude self-regulate reasoning depth on
        # ambiguous or multi-step accounting requests. Display is left at
        # the default "omitted" — the chat UI surfaces the final answer,
        # not the chain of thought.
        request_kwargs["thinking"] = {"type": "adaptive"}

    client = _client()
    try:
        response = await client.messages.create(**request_kwargs)
    except anthropic.AuthenticationError as e:
        raise AIAccountantError(
            "Anthropic authentication failed — check ANTHROPIC_API_KEY."
        ) from e
    except anthropic.PermissionDeniedError as e:
        raise AIAccountantError(
            f"Anthropic API key has no access to {chosen_model!r}: {e.message}"
        ) from e
    except anthropic.NotFoundError as e:
        raise AIAccountantError(
            f"Unknown Anthropic model {chosen_model!r}: {e.message}"
        ) from e
    except anthropic.RateLimitError as e:
        raise AIAccountantError(
            "Anthropic rate limit reached (after automatic retries). "
            "Wait a minute or upgrade your usage tier."
        ) from e
    except anthropic.OverloadedError as e:
        raise AIAccountantError(
            "Anthropic API is temporarily overloaded. Try again shortly."
        ) from e
    except anthropic.BadRequestError as e:
        raise AIAccountantError(f"Anthropic rejected the request: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AIAccountantError(
            f"Anthropic API error ({e.status_code}): {e.message}"
        ) from e
    except anthropic.APIConnectionError as e:
        raise AIAccountantError(
            "Could not reach Anthropic — check the network / proxy / DNS."
        ) from e

    # Surface cache-effectiveness in the logs so we can tell whether the
    # prefix is actually being reused turn over turn.
    usage = response.usage
    logger.info(
        "ai-accountant turn model=%s stop=%s input=%d cache_read=%d cache_create=%d output=%d",
        chosen_model,
        response.stop_reason,
        usage.input_tokens,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
        usage.output_tokens,
    )
    return response


def extract_tool_uses(response: Message) -> list[dict[str, Any]]:
    """Pull out every ``tool_use`` block from a Claude response.

    Each entry has ``id`` (the ``tool_use_id`` we must echo back in the
    matching ``tool_result``), ``name`` (the tool we should call), and
    ``input`` (already-parsed JSON dict — never re-serialise to compare).
    """
    out: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "tool_use":
            out.append({"id": block.id, "name": block.name, "input": block.input})
    return out


def extract_text(response: Message) -> str:
    """Concatenate every ``text`` block from a Claude response."""
    parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts)


def assistant_message_for_history(response: Message) -> dict[str, Any]:
    """Format Claude's response for re-injection into the next turn.

    Per the Anthropic docs: append ``response.content`` (the full block
    list, not just the extracted text) so that any ``tool_use`` blocks
    are preserved verbatim — the next request must reference them by
    ``id`` in its ``tool_result`` blocks.
    """
    # Pydantic models support .model_dump() / dict-like access; pass content
    # back through unchanged.
    return {"role": "assistant", "content": [b.model_dump() for b in response.content]}
