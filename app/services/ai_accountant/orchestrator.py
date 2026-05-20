"""Agent orchestrator — drives the AI accountant tool-use loop.

The loop:

1. Append the user's message to the chat history.
2. Send history + system prompt + tool catalogue to Claude via
   ``anthropic_client.chat_once``.
3. If the response has any ``tool_use`` blocks, execute each tool
   (read-tools answer immediately; proposal-tools persist a pending
   row and return a ``confirmation_token``), append the results as a
   user message, and loop back to step 2.
4. When the response is ``stop_reason == "end_turn"``, persist the
   assistant message to ``ai_chat_messages`` and return.

Hard safeguards:

* ``MAX_TURNS`` — caps tool-use iterations per user message. Above
  this we return the partial response and raise a warning rather than
  rolling forever.
* ``PAUSE_TURN_RETRIES`` — handles Claude's `pause_turn` stop reason
  (when an internal server-side loop hits its limit) by re-sending,
  per the SDK docs.
* All tool exceptions are caught and surfaced to Claude as
  ``tool_result(is_error=True)`` so the model can recover or ask
  for clarification.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession

from .anthropic_client import (
    AIAccountantError,
    assistant_message_for_history,
    chat_once,
    extract_text,
    extract_tool_uses,
)
from .base import BaseTool, ToolContext, ToolError, ToolRegistry
from .proposal_tools import register_proposal_tools
from .read_tools import register_read_tools

logger = logging.getLogger(__name__)

# Per-message safety cap. A well-behaved chat hits the network ~3 times
# (resolve entities → propose → wrap up). 8 is comfortable headroom; the
# brief mandates this kind of cap as part of the rate-limit / spend
# controls in §4.7.
MAX_TURNS = 8
PAUSE_TURN_RETRIES = 3


SYSTEM_PROMPT = """You are the AI accountant for this company — a careful junior bookkeeper that helps the manager record transactions, look up balances, and answer accounting questions.

You have a fixed catalogue of tools. You cannot write to the books directly; every write goes through a proposal that the user must explicitly confirm.

# Behavioural rules — non-negotiable

1. **Never invent entities.** When the user mentions a person or organisation by name, ALWAYS call ``find_entity`` first. Don't guess which Kim they mean.
2. **Never propose without resolving accounts.** Use ``query_ledger`` or ``get_account_balance`` to confirm the right account_code before proposing a transaction.
3. **Always confirm ambiguity before proposing.** If the user said "Monday" and it could mean this Monday or last Monday, ask. If currency is unclear, ask. If the entity matches multiple records, list them and ask which.
4. **Never split bulk requests into a single proposal.** "Pay all overdue invoices" → list the invoices and produce one proposal per invoice. Never aggregate.
5. **Never silently round, swap currencies, or 'fix' obvious typos in amounts.** If the user says "$1k" and the vendor's invoice is in EUR, flag the mismatch and ask.
6. **Reject future-dated expenses (>1 day ahead) unless the user explicitly says it's scheduled.**

# Resolution loop — every time the user could cause a write

a. Parse intent (record / query / invoice / etc.) and extract: amount, currency, date, entity, account, memo.
b. Resolve each entity with ``find_entity``. If confidence ≥ 0.95 use it. Otherwise list candidates and ask the user to pick.
c. Resolve accounts via ``query_ledger`` with a code prefix when unsure.
d. Fill defaults from ``get_company_defaults`` (currency, today's date, locale). Always call this once at the start of a session to anchor everything.
e. Draft the proposal via ``propose_create_transaction``. The tool registers a pending row and returns a ``confirmation_token`` — DO NOT re-paste the full summary text afterwards; the chat UI renders the action card automatically. Just briefly tell the user what you proposed and end your turn.

# When the user asks a pure question (read-only)

Answer with ``query_ledger`` / ``get_account_balance`` / ``list_entities``. No proposal needed. No confirmation needed.

# Style

* Be concise. Don't apologise. Don't lecture about accounting basics.
* Use the user's language when possible — Persian if they're writing in Persian.
* Match the company's currency by default (returned by ``get_company_defaults``).
"""


# ---------------------------------------------------------------------------
# Registry building
# ---------------------------------------------------------------------------


def build_default_registry() -> ToolRegistry:
    """Return a fully-populated tool registry: read tools + proposal tools."""
    reg = ToolRegistry()
    register_read_tools(reg)
    register_proposal_tools(reg)
    return reg


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _get_or_create_session(
    db: Session, *, user_id: str, session_id: str | None
) -> AIChatSession:
    """Look up the chat session or create a fresh one."""
    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            sid = None
        if sid:
            row = db.execute(
                select(AIChatSession).where(AIChatSession.id == sid)
            ).scalar_one_or_none()
            if row is not None:
                if row.user_id != user_id:
                    raise PermissionError("This chat session belongs to a different user.")
                return row
    row = AIChatSession(user_id=user_id, title=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _persist_message(
    db: Session, *, session_id: uuid.UUID, role: str, content: dict[str, Any]
) -> None:
    db.add(
        AIChatMessage(session_id=session_id, role=role, content=content)
    )
    db.commit()


def _replay_history(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Rebuild the Anthropic ``messages`` array from saved chat history."""
    rows = (
        db.execute(
            select(AIChatMessage)
            .where(AIChatMessage.session_id == session_id)
            .order_by(AIChatMessage.created_at, AIChatMessage.id)
        )
        .scalars()
        .all()
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        if row.role == "user":
            history.append({"role": "user", "content": row.content.get("content")})
        elif row.role == "assistant":
            # Persisted as the full content-block list (see _persist_message).
            history.append({"role": "assistant", "content": row.content.get("content")})
        elif row.role == "tool":
            history.append({"role": "user", "content": row.content.get("content")})
    return history


# ---------------------------------------------------------------------------
# Chat result
# ---------------------------------------------------------------------------


@dataclass
class ChatResult:
    session_id: str
    text: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    turns: int = 1


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_chat_turn(
    db: Session,
    *,
    user_id: str,
    username: str | None = None,
    user_message: str,
    session_id: str | None = None,
    ip_address: str | None = None,
    registry: ToolRegistry | None = None,
) -> ChatResult:
    """Drive one user-message turn through the AI accountant agent loop.

    Returns when Claude says ``end_turn`` (or we hit ``MAX_TURNS``). The
    return value lists every proposal Claude registered during the turn
    so the frontend can render the action cards.
    """
    reg = registry or build_default_registry()
    tools = reg.to_anthropic()

    chat_session = _get_or_create_session(db, user_id=user_id, session_id=session_id)
    session_uuid_str = str(chat_session.id)

    history = _replay_history(db, chat_session.id)
    history.append({"role": "user", "content": user_message})
    _persist_message(
        db,
        session_id=chat_session.id,
        role="user",
        content={"content": user_message},
    )

    tool_ctx = ToolContext(
        db=db,
        user_id=user_id,
        username=username,
        chat_session_id=session_uuid_str,
        user_message=user_message,
        ip_address=ip_address,
    )

    proposals: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    final_text = ""
    stop_reason: str | None = None
    pause_attempts = 0

    for turn in range(1, MAX_TURNS + 1):
        response = await chat_once(
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            messages=history,
        )
        stop_reason = response.stop_reason

        # Always append the assistant turn to history (including any
        # tool_use blocks — Claude needs them for the next round).
        assistant_entry = assistant_message_for_history(response)
        history.append(assistant_entry)
        _persist_message(
            db,
            session_id=chat_session.id,
            role="assistant",
            content=assistant_entry,
        )

        if stop_reason == "pause_turn":
            # Server-side iteration cap — resume per the SDK docs.
            pause_attempts += 1
            if pause_attempts > PAUSE_TURN_RETRIES:
                logger.warning(
                    "ai-accountant: pause_turn retries exhausted (%d) — bailing",
                    pause_attempts,
                )
                break
            continue

        tool_uses = extract_tool_uses(response)
        if stop_reason == "end_turn" or not tool_uses:
            final_text = extract_text(response)
            return ChatResult(
                session_id=session_uuid_str,
                text=final_text,
                proposals=proposals,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
                turns=turn,
            )

        # Execute every tool the model asked for and feed results back.
        tool_results: list[dict[str, Any]] = []
        for call in tool_uses:
            tool_call_record = {
                "tool_use_id": call["id"],
                "name": call["name"],
                "input": call["input"],
            }
            tool_calls.append(tool_call_record)
            tool = reg.get(call["name"])
            if tool is None:
                tool_results.append(
                    _tool_result(call["id"], f"Unknown tool: {call['name']!r}", is_error=True)
                )
                continue

            try:
                args = tool.InputSchema.model_validate(call["input"])
            except ValidationError as e:
                tool_results.append(
                    _tool_result(call["id"], f"Invalid input: {e}", is_error=True)
                )
                continue

            try:
                result = await tool.run(tool_ctx, args)
            except ToolError as e:
                tool_results.append(
                    _tool_result(call["id"], f"Tool error ({e.code}): {e.message}", is_error=True)
                )
                continue
            except Exception as e:
                logger.exception("ai-accountant: tool %s crashed", call["name"])
                tool_results.append(
                    _tool_result(
                        call["id"],
                        f"Internal tool error: {type(e).__name__}: {e}",
                        is_error=True,
                    )
                )
                continue

            tool_results.append(_tool_result(call["id"], result))
            tool_call_record["result"] = result
            if tool.category == "proposal" and isinstance(result, dict) and "confirmation_token" in result:
                proposals.append(result)

        user_tool_entry = {"role": "user", "content": tool_results}
        history.append(user_tool_entry)
        _persist_message(
            db,
            session_id=chat_session.id,
            role="tool",
            content=user_tool_entry,
        )

    # Hit MAX_TURNS without an end_turn — return what we have.
    logger.warning(
        "ai-accountant: hit MAX_TURNS=%d for session %s — returning partial result",
        MAX_TURNS, session_uuid_str,
    )
    return ChatResult(
        session_id=session_uuid_str,
        text=final_text or "(agent reached max turns — please simplify your request)",
        proposals=proposals,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        turns=MAX_TURNS,
    )


def _tool_result(tool_use_id: str, content: Any, *, is_error: bool = False) -> dict[str, Any]:
    """Wrap a tool's return value in the ``tool_result`` shape Claude expects.

    ``content`` may be a dict (JSON-stringified) or a plain string."""
    if isinstance(content, str):
        text = content
    else:
        import json
        text = json.dumps(content, default=str)
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": text,
    }
    if is_error:
        block["is_error"] = True
    return block
