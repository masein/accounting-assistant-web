"""Base abstractions for AI accountant tools.

Every tool exposed to Claude must:

1. Declare a unique ``name`` (referenced in ``tool_use`` blocks)
2. Provide a ``description`` Claude reads to decide when to use it
3. Define a Pydantic ``InputSchema`` so the input dict from Claude is
   validated *before* it touches the database (defence in depth — Claude
   may emit malformed args under prompt-injection or model-error
   conditions)
4. Implement ``run()`` which receives a validated input and returns
   a JSON-serialisable result

Tools fall into two categories:

* **Read tools** — pure queries; safe to execute as soon as Claude
  requests them. No confirmation, no audit-log entry (the read itself
  is non-mutating).
* **Proposal tools** — register a pending action in ``ai_proposals``
  and return its ``confirmation_token`` plus a human-readable summary.
  The actual write happens later via the ``/ai-accountant/execute``
  HTTP endpoint, which the frontend calls after the user confirms.
  Proposal tools never mutate the ledger.

The single execute / undo entry points are HTTP endpoints (not Claude
tools) so the server can enforce server-side authorization, idempotency
and audit-log writing in one place.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.orm import Session


@dataclass
class ToolContext:
    """Per-request context threaded into every tool ``run()`` call.

    The orchestrator builds this once per agent turn and passes it
    through unchanged.
    """

    db: Session
    user_id: str
    username: str | None = None
    is_admin: bool = False
    chat_session_id: str | None = None  # UUID of the AIChatSession, if any
    user_message: str | None = None     # The original user message, for audit
    ip_address: str | None = None
    # Attachment IDs uploaded with this chat turn (invoice/receipt files).
    # propose_create_transaction links these onto the transaction it
    # registers so the file follows the entry through to execute.
    attachment_ids: list[str] = field(default_factory=list)
    # Amounts present in the turn's source material — the OCR'd document
    # total(s) and any numbers in the user's message. propose_create_transaction
    # cross-checks the proposed total against these so a mis-scaled or garbled
    # amount can't be silently proposed for one-click confirmation.
    source_amounts: list[int] = field(default_factory=list)


class BaseTool(ABC):
    """Anthropic-compatible tool. Subclasses define metadata + ``run``."""

    name: ClassVar[str]
    description: ClassVar[str]
    InputSchema: ClassVar[type[BaseModel]]

    # ``proposal`` tools return a confirmation_token and never mutate;
    # ``read`` tools are pure queries. The orchestrator uses this to
    # decide whether to write the call into the audit log.
    category: ClassVar[str] = "read"  # "read" | "proposal"

    def to_anthropic(self) -> dict[str, Any]:
        """Return the dict shape Anthropic's Messages API expects for
        ``tools=[...]``. The JSON Schema is generated from the Pydantic
        InputSchema so the two can't drift."""
        schema = self.InputSchema.model_json_schema()
        # Strip $defs / $ref expansion that Anthropic doesn't accept.
        # Pydantic emits these for nested models; flatten when present.
        schema = _resolve_refs(schema)
        # Anthropic requires the schema to be a JSON Schema object with
        # `type: "object"` at the top level — Pydantic already produces
        # that, but normalise just in case.
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}, "additionalProperties": False}
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def run(self, ctx: ToolContext, args: BaseModel) -> dict[str, Any]:
        """Execute the tool. Receives a validated Pydantic instance,
        returns a JSON-serialisable dict that becomes the
        ``tool_result.content`` Claude sees on the next turn."""


def _resolve_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$ref`` pointers against ``$defs`` so Anthropic accepts
    the schema. The Messages API rejects schemas with ``$defs`` /
    ``$ref`` — they're a JSON Schema feature, not a Claude-tool one."""
    defs = schema.pop("$defs", None) or schema.pop("definitions", None)
    if not defs:
        return schema

    def _inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                # ``#/$defs/Foo`` → look up "Foo" in defs
                name = ref.rsplit("/", 1)[-1]
                target = defs.get(name)
                if target is None:
                    return node
                return _inline(target)
            return {k: _inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_inline(item) for item in node]
        return node

    return _inline(schema)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Holds one instance of each registered tool and exposes them in
    the shape Anthropic expects."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> BaseTool:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def to_anthropic(self) -> list[dict[str, Any]]:
        """All registered tools as Anthropic ``tools=[...]`` payload."""
        return [t.to_anthropic() for t in self._tools.values()]


class ToolError(Exception):
    """Raised by a tool when its preconditions fail (bad input that
    Pydantic couldn't catch, missing account, permission denied, …).

    The orchestrator catches this, formats it as a ``tool_result`` with
    ``is_error=True``, and lets Claude reason about the failure instead
    of crashing the chat turn.
    """

    def __init__(self, message: str, *, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
