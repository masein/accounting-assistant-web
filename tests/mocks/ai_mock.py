"""Deterministic mock for AI suggest/chat_turn functions.

Returns canned responses for known inputs, so tests don't require
a running LLM server.
"""
from __future__ import annotations

from typing import Any
from datetime import date


PAYMENT_RESPONSE = {
    "date": date.today().isoformat(),
    "description": "Rent expense payment via Melli Bank",
    "lines": [
        {"account_code": "6112", "debit": 5000000, "credit": 0, "line_description": "Rent expense"},
        {"account_code": "1110", "debit": 0, "credit": 5000000, "line_description": "Bank payment"},
    ],
    "entity_mentions": [
        {"role": "bank", "name": "Melli"},
    ],
}

RECEIPT_RESPONSE = {
    "date": date.today().isoformat(),
    "description": "Received payment from client Nikzade",
    "lines": [
        {"account_code": "1110", "debit": 2000000, "credit": 0, "line_description": "Bank deposit"},
        {"account_code": "1112", "debit": 0, "credit": 2000000, "line_description": "Accounts receivable"},
    ],
    "entity_mentions": [
        {"role": "client", "name": "Nikzade"},
    ],
}

CLARIFICATION_RESPONSE = {
    "message": "Which bank did you pay from?",
    "transaction": None,
}


async def mock_suggest_transaction(user_message: str, accounts: list[dict[str, str]]) -> dict[str, Any]:
    low = user_message.lower()
    if any(w in low for w in ("paid", "pay", "expense", "rent", "پرداخت")):
        return PAYMENT_RESPONSE.copy()
    if any(w in low for w in ("received", "receive", "income", "دریافت")):
        return RECEIPT_RESPONSE.copy()
    return PAYMENT_RESPONSE.copy()


async def mock_chat_turn(
    messages: list[dict[str, str]],
    accounts: list[dict[str, str]],
    attachment_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    last_msg = (messages[-1]["content"] if messages else "").lower()
    if any(w in last_msg for w in ("paid", "pay", "expense", "rent")):
        return {"message": "Here's the voucher:", "transaction": PAYMENT_RESPONSE.copy()}
    if any(w in last_msg for w in ("received", "income")):
        return {"message": "Here's the receipt:", "transaction": RECEIPT_RESPONSE.copy()}
    return CLARIFICATION_RESPONSE.copy()
