"""A message sent in reply to an earlier one (scenarios AC4–AC7).

The phone and the web send what was quoted: whose message it was, its words,
and the card's token when it was a card. The words are kept beside the
user's own, so the thread shows the quote when it is opened again, and go
to the model ahead of the user's words, so «و به دلار؟» is read as being
about the answer it replies to. A card's token is only kept when the card
is this user's: a reply that names someone else's card brings its words,
never the card.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

# enough of a long answer to know which one it was
QUOTE_CHARS = 400


def quoted(db: Session, *, user_id: str, role: str | None, text: str | None, token: str | None) -> dict | None:
    """The quote as kept on the user's message: ``{role, text[, token]}``, or None when it says nothing."""
    words = " ".join((text or "").split())
    if len(words) > QUOTE_CHARS:
        words = words[:QUOTE_CHARS].rstrip() + "…"
    card = _own_card(db, user_id, token)
    if not words and card is None:
        return None
    quote = {"role": "user" if role == "user" else "assistant", "text": words}
    if card is not None:
        quote["token"] = str(card.confirmation_token)
    return quote


def _own_card(db: Session, user_id: str, token: str | None):
    if not token:
        return None
    from app.models.ai_accountant import AIProposal
    try:
        key = uuid.UUID(str(token).strip())
    except (ValueError, TypeError):
        return None
    return db.execute(select(AIProposal).where(AIProposal.confirmation_token == key,
                                               AIProposal.user_id == str(user_id))).scalars().first()


def for_model(db: Session, quote: dict | None) -> str:
    """The line the model reads before the user's words."""
    if not quote:
        return ""
    whose = "the user's own earlier message" if quote.get("role") == "user" else "your earlier message"
    line = f"[The user is replying to {whose}: «{quote.get('text') or ''}»"
    token = quote.get("token")
    if token:
        from app.models.ai_accountant import AIProposal
        card = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().first()
        status = card.status if card is not None else "gone"
        line += f" — the card with confirmation_token {token}, {status}"
        if status == "pending":
            line += f'; a change to it is the corrected card with replaces="{token}"'
        elif status == "executed":
            line += "; it is already posted: a change to it is a correction of that entry, never a second copy"
    return line + "]"
