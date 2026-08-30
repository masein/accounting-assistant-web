"""Last-resort LLM tier for statement rows the deterministic categorizer can't place.

The rules in ``statement_categorizer`` cover the merchants a user actually
repeats — which, after a month or two of history, is most of them. What's left
is the long tail: an unfamiliar shop, a one-off transfer, a narration in a
format the keyword table doesn't know.

This asks a model to place *only* those leftovers, in ONE batched call per
import rather than one per row. Deliberately built to be optional:

* the deterministic tiers run first and are never overridden
* the model may only choose from account codes actually in this chart, and only
  ones of the right nature (a debit can't land on income)
* anything unparseable, unknown or wrong-natured is dropped, not guessed at
* every failure path returns "no suggestion" — a model being down, unreachable
  or unconfigured must never break an import, which still works fully offline

Confidence is reported below the deterministic tiers so the review UI can sort
uncertain rows to the user's attention.

The "a wrong guess is worse than no guess" instruction is load-bearing, not
decoration. Measured against gpt-4o-mini on unfamiliar Persian narrations, it
declines roughly half of them; a variant that pushed for coverage instead
("prefer the closest category, fall back to miscellaneous") placed every row
but filed a barber and a dry cleaner under Housing & Rent. A blank cell makes a
user think; a confident wrong category gets accepted. Don't soften it.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
from app.services.statement_categorizer import CategorySuggestion

logger = logging.getLogger(__name__)

CONFIDENCE_LLM = 0.5
# Prompt-size bound. A personal chart has ~30 postable accounts and an SME one
# a few hundred; past this the prompt costs more than the answer is worth.
MAX_CANDIDATE_ACCOUNTS = 80
# Rows per request. Keeps one bad batch from poisoning a whole large import.
MAX_ROWS_PER_CALL = 40

_SYSTEM = (
    "You categorise bank statement lines for a bookkeeping system. "
    "You will be given a list of allowed accounts and a numbered list of statement "
    "narrations. For each narration choose the single best account CODE from the "
    "allowed list. If nothing fits well, use null — a wrong guess is worse than no "
    "guess. Reply with a JSON object only, mapping each number to a code or null, "
    "e.g. {\"1\": \"6130\", \"2\": null}. No prose, no markdown."
)


def _candidates(db: Session, nature: str) -> list[Account]:
    accounts = db.execute(
        select(Account).where(Account.level != AccountLevel.GROUP)
    ).scalars().all()
    picked = [a for a in accounts if classify_account_code(a.code) == nature]
    return picked[:MAX_CANDIDATE_ACCOUNTS]


def _extract_json_object(content: str) -> dict:
    """Pull a JSON object out of a model reply.

    Mirrors the defensive handling in ai_suggest: thinking models wrap output in
    <think> blocks, some fence it in markdown, some prepend prose.
    """
    content = re.sub(r"<think>[\s\S]*?</think>", "", content or "", flags=re.IGNORECASE).strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if fenced:
        content = fenced.group(1).strip()
    if not content.startswith("{"):
        brace = content.find("{")
        if brace < 0:
            return {}
        depth, end = 0, -1
        for i in range(brace, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return {}
        content = content[brace : end + 1]
    try:
        out = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


def _build_prompt(accounts: list[Account], narrations: list[str]) -> str:
    account_lines = "\n".join(f"- {a.code}: {a.name}" for a in accounts)
    numbered = "\n".join(f"{i}. {n}" for i, n in enumerate(narrations, start=1))
    return (
        f"Allowed accounts:\n{account_lines}\n\n"
        f"Statement narrations:\n{numbered}\n\n"
        f"Return JSON mapping each number (1-{len(narrations)}) to an account code or null."
    )


async def suggest_unknown(
    db: Session, items: list[tuple[object, str, bool]]
) -> dict[object, CategorySuggestion]:
    """Place rows the deterministic tiers left unresolved.

    ``items`` are ``(key, narration, is_debit)``. Returns only the keys the model
    placed on a valid account; everything else is simply absent.
    """
    if not items:
        return {}

    # Debits and credits have disjoint candidate sets, so they're asked separately
    # — that alone makes it impossible for the model to file a payment as income.
    out: dict[object, CategorySuggestion] = {}
    for is_debit in (True, False):
        group = [(k, n) for k, n, d in items if d == is_debit and (n or "").strip()]
        if not group:
            continue
        nature = EXPENSE if is_debit else REVENUE
        accounts = _candidates(db, nature)
        if not accounts:
            continue
        by_code = {a.code: a for a in accounts}
        for start in range(0, len(group), MAX_ROWS_PER_CALL):
            chunk = group[start : start + MAX_ROWS_PER_CALL]
            try:
                answers = await _ask(accounts, [n for _k, n in chunk])
            except Exception as e:  # noqa: BLE001 - never break an import
                logger.warning("LLM categorization unavailable: %s", e)
                return out
            for idx, (key, _narration) in enumerate(chunk, start=1):
                code = answers.get(str(idx))
                if not isinstance(code, str):
                    continue
                acc = by_code.get(code.strip())
                if acc is None:
                    continue  # hallucinated or out-of-chart code
                out[key] = CategorySuggestion(
                    account_code=acc.code, account_name=acc.name,
                    category=acc.name, confidence=CONFIDENCE_LLM, source="llm",
                )
    return out


async def _ask(accounts: list[Account], narrations: list[str]) -> dict:
    from app.services.ai_suggest import (
        _chat_completions_url,
        _post_lm_studio,
        _resolve_ai_base_model,
        _resolve_ai_headers,
    )

    base, model = _resolve_ai_base_model()
    if not base:
        raise RuntimeError("No AI backend configured")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_prompt(accounts, narrations)},
        ],
        "temperature": 0,
        "max_tokens": 1000,
    }
    data = await _post_lm_studio(
        _chat_completions_url(base), payload, base, _resolve_ai_headers()
    )
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return _extract_json_object(content)
