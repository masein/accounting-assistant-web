"""Provider-neutral LLM protocol for the AI accountant.

The AI accountant's orchestrator never speaks directly to any LLM provider.
It works with a small normalized vocabulary — ``ChatMessage`` for turn
history, ``LLMResponse`` for a model reply — and dispatches to an
``LLMClient`` implementation that translates those into the wire format
of whatever provider is active (Anthropic, OpenAI-compatible, etc.).

This makes it cheap to add new providers later: a new file with a
hundred-ish lines of translation + httpx (or SDK) glue, no orchestrator
changes.

The normalized message shape also doubles as our **storage** format —
``ai_chat_messages.content`` columns hold ``ChatMessage.to_dict()`` JSON.
Sessions are portable across providers as long as the message shape
doesn't carry provider-specific blobs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Normalized message + tool-call shapes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """One model-requested tool invocation. The orchestrator runs the
    matching tool, then sends back a ``ToolResult`` with the same ``id``."""
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "input": self.input}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolCall":
        return cls(id=str(d["id"]), name=str(d["name"]), input=dict(d.get("input") or {}))


@dataclass
class ChatMessage:
    """One turn in the conversation. Role-tagged with optional text + tool
    payloads. Every field that isn't set is silently dropped during wire
    translation — adapters only emit what their provider expects.

    Role conventions:
        ``user``      — typed by the human. Carries ``text``.
        ``assistant`` — model output. May carry ``text`` and/or ``tool_calls``.
        ``tool``      — orchestrator-supplied tool result. Carries
                        ``tool_call_id``, ``text`` (the JSON-stringified
                        result), and optional ``is_error``.
    """
    role: str
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role}
        if self.text is not None:
            out["text"] = self.text
        if self.tool_calls:
            out["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.is_error:
            out["is_error"] = True
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatMessage":
        return cls(
            role=str(d["role"]),
            text=d.get("text"),
            tool_calls=[ToolCall.from_dict(tc) for tc in (d.get("tool_calls") or [])],
            tool_call_id=d.get("tool_call_id"),
            is_error=bool(d.get("is_error", False)),
        )


# ---------------------------------------------------------------------------
# Normalized response
# ---------------------------------------------------------------------------


@dataclass
class LLMUsage:
    """Token counts. Fields beyond input/output are best-effort — not every
    provider reports cache metrics."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LLMResponse:
    """One model reply, normalized.

    ``stop_reason`` values used by the orchestrator:
        ``end_turn``  — model finished naturally; orchestrator exits the loop
        ``tool_use``  — model wants to call ``tool_calls``; orchestrator runs
                        them and loops
        ``max_tokens``— hit the output cap; surfaces as the final answer
        ``pause_turn``— server-side iteration cap (Anthropic only); resume
        ``refusal``   — model refused for safety reasons
    """
    message: ChatMessage  # role='assistant'
    stop_reason: str
    usage: LLMUsage = field(default_factory=LLMUsage)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Single-method async LLM client. Implementations live in
    ``anthropic_client.py`` and ``openai_client.py``."""

    shape: str  # 'anthropic' or 'openai' — purely for logging/diagnostics

    async def chat(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]],
        messages: list[ChatMessage],
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Tool-schema helpers (shape-specific)
# ---------------------------------------------------------------------------


def tool_to_anthropic(tool_def: dict[str, Any]) -> dict[str, Any]:
    """``BaseTool.to_anthropic()`` already emits Anthropic shape:
    ``{name, description, input_schema}``. This is a pass-through that lets
    the Anthropic adapter add ``cache_control`` markers on top.
    """
    return dict(tool_def)


def tool_to_openai(tool_def: dict[str, Any]) -> dict[str, Any]:
    """Wrap a tool def in OpenAI's function-calling envelope:
    ``{"type": "function", "function": {"name", "description", "parameters"}}``.

    The inner ``parameters`` is the same JSON Schema we generate from the
    Pydantic input model — no shape change, just a different wrapper.
    """
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def.get("description", ""),
            "parameters": tool_def["input_schema"],
        },
    }


class LLMClientError(Exception):
    """Raised by any client when the provider fails irrecoverably (bad
    credentials, network down, malformed response). The orchestrator maps
    this to ``AIAccountantError`` for the API layer."""
