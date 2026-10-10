"""One contract for the phone, the web chat and the bots (roadmap
ROADMAP_ANDROID_CHAT P0.10; scenario N29).

* Every block type, built by the real builders from a set of recorded
  conversations, matches ``docs/contracts/mobile-blocks.schema.json``.
* The recorded replies are kept in ``docs/contracts/conversations/``: a change
  to what the builders send shows here as a diff to review (rewrite them with
  ``CONTRACT_REWRITE=1``), and the Android tests parse the same files, so the
  app is checked against what the server really sends.
* Every route of ``/api/mobile/v1`` is in the table below with the roles that
  may call it: a new route, or a changed right, is a deliberate edit here.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest

from app.core.auth import SessionUser
from app.core.permissions import ROLE_PERMISSIONS, ROUTE_PERMISSIONS, role_can
from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant import blocks as B
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs/contracts/mobile-blocks.schema.json").read_text(encoding="utf-8"))
RECORDED = ROOT / "docs/contracts/conversations"


# --- a validator for the subset of JSON Schema the contract uses --------------------------------

_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list, "null": type(None)}


def _check(value, schema: dict, path: str, errors: list[str]) -> None:
    if "$ref" in schema:
        schema = SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    if "anyOf" in schema:
        trials = []
        for option in schema["anyOf"]:
            sub: list[str] = []
            _check(value, option, path, sub)
            trials.append(sub)
        if all(trials):
            errors.append(f"{path}: matches none of {schema['anyOf']}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: {value!r} is not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        ok = any(isinstance(value, _TYPES[k]) and not (k == "integer" and isinstance(value, bool)) for k in kinds)
        if not ok:
            errors.append(f"{path}: {type(value).__name__} is not {kinds}")
            return
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: {value!r} doesn't match {schema['pattern']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}", errors)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i}]", errors)


def contract_errors(block: dict) -> list[str]:
    kind = block.get("type")
    ref = SCHEMA["discriminator"]["mapping"].get(kind)
    if ref is None:
        return [f"unknown block type {kind!r}"]
    errors: list[str] = []
    _check(block, {"$ref": ref}, kind, errors)
    if not str(block.get("id") or "").strip():
        errors.append(f"{kind}: empty id")
    return errors


def test_edit_is_offered_exactly_where_it_works():
    from app.services.ai_accountant.proposal_edit import EDITABLE
    assert B._EDITABLE == EDITABLE


def test_the_validator_catches_what_breaks_a_client():
    good = {"type": "file", "id": "f1", "name": "a.pdf", "mime": "application/pdf",
            "path": "/api/mobile/v1/documents/invoices/1", "fallback_text": "Invoice (PDF)"}
    assert contract_errors(good) == []
    assert contract_errors({**good, "path": "/static/a.pdf"})                     # not a phone document
    assert contract_errors({k: v for k, v in good.items() if k != "fallback_text"})
    assert contract_errors({"type": "figure", "id": "x", "label": "Cash", "value": "12", "currency": None,
                            "fallback_text": "Cash 12"})                         # a number sent as text
    assert contract_errors({"type": "hologram", "id": "h"})


# --- the recorded conversations ------------------------------------------------------------------

TOKEN = "0b6f2b8e-7a51-4c2e-9d3e-5f1a2b3c4d5e"
TXN_PREVIEW = {"date": "2026-10-10", "description": "اجارهٔ دفتر، مهر", "currency": "IRR", "lines": [
    {"account_code": "6112", "debit": 80_000_000, "credit": 0},
    {"account_code": "1110", "debit": 0, "credit": 80_000_000}]}


def _conversations(db) -> dict[str, list[dict]]:
    """What the builders make of recorded turns: the tool results a turn read,
    the proposals it drafted, its words; and the cards built outside a turn."""
    def turn(*, lang="fa", calendar="jalali", text=None, calls=(), proposals=(), intake=None):
        return B.build_blocks(db, text=text, proposals=list(proposals), intake=intake, lang=lang, calendar=calendar,
                              tool_calls=[{"name": n, "tool_use_id": f"call_{i}", "result": r} for i, (n, r) in enumerate(calls)])

    proposal = {"confirmation_token": TOKEN, "tool_name": "propose_create_transaction",
                "summary": "Proposed journal entry on 2026-10-10 (IRR):\n  اجارهٔ دفتر، مهر", "preview": TXN_PREVIEW,
                "needs_approval": False, "expires_at": "2026-10-10T10:10:00+00:00", "new_entities": []}
    return {
        "cash-fa": turn(text="موجودی نقد و بانک امروز: ۱٬۲۴۰٬۰۰۰٬۰۰۰ ریال.", calls=[("get_cash_position", {
            "total": 1_240_000_000, "currency": "IRR", "as_of": "2026-10-10",
            "accounts": [{"account_code": "1110", "account_name": "بانک ملت", "balance": 1_000_000_000},
                         {"account_code": "1120", "account_name": "صندوق", "balance": 240_000_000}]})]),
        "balance-en": turn(lang="en", calendar="gregorian", text="Bank Mellat holds 1,000,000,000 IRR.",
                           calls=[("get_account_balance", {"account_code": "1110", "account_name": "Bank Mellat",
                                                           "balance": 1_000_000_000, "currency": "IRR", "as_of": "2026-10-10"})]),
        "spending-fa": turn(text="این ماه بیشتر خرج اجاره شد.", calls=[("get_spending_summary", {
            "total": 120_000_000, "currency": "IRR",
            "by_category": [{"category_code": "6112", "category_name": "اجاره", "amount": 80_000_000},
                            {"category_code": "6130", "category_name": "سفر و ایاب‌وذهاب", "amount": 40_000_000}]})]),
        "invoices-en": turn(lang="en", calendar="gregorian", text="Two invoices are unpaid.", calls=[("list_invoices", {
            "count": 2, "totals_by_currency": {"IRR": 90_000_000},
            "invoices": [{"number": "INV-1042", "party": "Aria Co", "balance_due": 60_000_000, "currency": "IRR",
                          "due_date": "2026-10-01", "status": "overdue"},
                         {"number": "INV-1043", "party": "Sepehr", "balance_due": 30_000_000, "currency": "IRR",
                          "due_date": "2026-10-20", "status": "sent"}]})]),
        "budgets-fa": turn(text="بودجهٔ اجاره پر شده است.", calls=[("get_budget_status", {
            "total_budget": 100_000_000, "total_actual": 95_000_000,
            "budgets": [{"category": "اجاره", "actual": 80_000_000, "budget": 80_000_000, "used_pct": 100, "state": "over"},
                        {"category": "سفر", "actual": 15_000_000, "budget": 20_000_000, "used_pct": 75, "state": "ok"}]})]),
        "invoice-pdf-en": turn(lang="en", calendar="gregorian", text="Here is INV-1042.", calls=[("get_invoice", {
            "id": "7d0c9a40-1f6b-4b7e-8a3c-2b9d6e5f4a31", "number": "INV-1042"})]),
        "forecast-fa": turn(text="نقدینگی ۱۳ هفتهٔ آینده، به تخمین.", calls=[("get_cash_forecast", {
            "currency": "IRR", "as_of": "2026-10-10", "opening_cash": 1_240_000_000, "closing_cash": 610_000_000,
            "lowest": {"week_start": "2026-11-30", "closing": -120_000_000}, "first_negative_week": "2026-11-30",
            "weeks": [{"week_start": week, "inflow": 0, "outflow": 0, "closing": c, "risk": c < 0,
                       "main_items": [], "other_items": 0}
                      for week, c in zip(["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16",
                                          "2026-11-23", "2026-11-30", "2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28",
                                          "2027-01-04"],
                                           [1_100_000_000, 980_000_000, 860_000_000, 640_000_000, 420_000_000, 180_000_000,
                                            40_000_000, -120_000_000, 90_000_000, 260_000_000, 380_000_000, 500_000_000,
                                            610_000_000])]})]),
        "draft-fa": turn(text="پیش‌نویس سند آماده است.", proposals=[proposal]),
        "intake-en": turn(lang="en", calendar="gregorian", text="12 rows read from the statement; 10 match.",
                          intake={"kind": "transactions", "batch_id": "batch-7", "rows": 12, "matched": 10}),
        "statement-fa": turn(text="صورتحساب ملت خوانده شد: ۱۲ ردیف.", intake={
            "kind": "bank_statement", "status": "imported", "statement_id": "5e6f7a8b-0000-4000-8000-000000000003",
            "bank_name": "Mellat", "bank_label": "ملت", "file_name": "mellat-1405-06.xlsx", "total_rows": 12, "from_date": "2026-08-23",
            "to_date": "2026-09-22", "currency": "IRR",
            "counts": {"matched": 8, "unrecorded": 2, "needs_confirmation": 1, "amount_mismatch": 1, "missing_in_bank": 0,
                       "duplicates": 0},
            "balance": {"gap": 90_000, "explained": False}, "clean": False, "findings_preview": []}),
        "posted-fa": [B.posted_block(token=TOKEN, transaction_id="3f1e2d4c-0000-4000-8000-000000000001",
                                     audit_log_id="9a8b7c6d-0000-4000-8000-000000000002", voucher="1042",
                                     date_iso="2026-10-10", calendar="jalali", lang="fa", undo_seconds=120,
                                     file=B.invoice_file("7d0c9a40-1f6b-4b7e-8a3c-2b9d6e5f4a31", "INV-1042"))],
    }


def _approval(db, co) -> list[dict]:
    from app.api.mobile_chat import _approval_block
    row = AIProposal(confirmation_token=uuid.UUID(TOKEN), user_id=str(co["owner"].id), tool_name="propose_create_transaction",
                     tool_input=TXN_PREVIEW, user_message="اجارهٔ مهر را ثبت کن", status="pending",
                     summary="Proposed journal entry on 2026-10-10 (IRR):\n  اجارهٔ دفتر، مهر", approval_status="requested")
    db.add(row)
    db.commit()
    cfo = SessionUser(user_id=str(co["cfo"].id), username=co["cfo"].username, is_admin=False, role="cfo")
    return [_approval_block(db, row, cfo, calendar="jalali", lang="fa")]


def _differences(a, b, path="") -> list[str]:
    """Where two recordings part: the paths that differ, for a readable failure."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in _differences(a.get(k), b.get(k), f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _differences(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: recorded {a!r}, now {b!r}"]


def _normalised(blocks: list[dict]) -> list[dict]:
    """What varies from run to run: the words' random ids, who asked and when."""
    out = json.loads(json.dumps(blocks, ensure_ascii=False))
    for b in out:
        if b["type"] == "text":
            b["id"] = "text:0"
        if b["type"] == "approval":
            b["fallback_text"] = b["fallback_text"].replace(b["requested_by"], "maryam")
            b["requested_by"] = "maryam"
            b["requested_at"] = {"iso": "2026-10-10T08:30:00+00:00", "display": "۱۸ مهر ۱۴۰۵"}
    return out


def test_n29_every_block_type_meets_the_contract_and_matches_its_recording(db, co):
    with use_company(co["cid"]):
        built = {**_conversations(db), "approval-fa": _approval(db, co)}
    seen = set()
    for name, blocks in built.items():
        blocks = _normalised(blocks)
        for b in blocks:
            assert contract_errors(b) == [], (name, b)
            seen.add(b["type"])
        ids = [b["id"] for b in blocks]
        assert len(ids) == len(set(ids)), name                     # the phone keys its list by id
        path = RECORDED / f"{name}.json"
        recording = {"conversation": name, "blocks": blocks}
        if os.environ.get("CONTRACT_REWRITE") or not path.exists():
            path.write_text(json.dumps(recording, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed = _differences(json.loads(path.read_text(encoding="utf-8")), recording)
        assert not changed, f"{name}: the builders now send something else (rewrite with CONTRACT_REWRITE=1 once reviewed): {changed}"
    assert seen == set(SCHEMA["discriminator"]["mapping"]), f"block types with no recording: {set(SCHEMA['discriminator']['mapping']) - seen}"
    stale = {p.stem for p in RECORDED.glob("*.json")} - set(built)
    assert not stale, f"recordings with no conversation: {stale}"


# --- every route, and who may call it ----------------------------------------------------------

ALL = frozenset(ROLE_PERMISSIONS)
BOOKS = frozenset({"owner", "cfo", "accountant", "personal"})
PUBLIC = None                                       # before sign-in: no role yet

MOBILE_ROUTES = {
    ("POST", "/api/mobile/v1/auth/login"): PUBLIC,
    ("POST", "/api/mobile/v1/auth/2fa"): PUBLIC,
    ("POST", "/api/mobile/v1/auth/refresh"): PUBLIC,
    # the account and its phones: everyone signed in
    ("GET", "/api/mobile/v1/me"): ALL,
    ("PUT", "/api/mobile/v1/me/language"): ALL,
    ("DELETE", "/api/mobile/v1/session"): ALL,
    ("GET", "/api/mobile/v1/devices"): ALL,
    ("DELETE", "/api/mobile/v1/devices/{device_id}"): ALL,
    ("POST", "/api/mobile/v1/crashes"): ALL,
    # the chat: the web chat's rights (books:write; reading needs books:read). Managers,
    # employees and viewers wait for the owner's decision on staff chat (roadmap §11.7)
    ("GET", "/api/mobile/v1/threads"): BOOKS,
    ("GET", "/api/mobile/v1/threads/{thread_id}/messages"): BOOKS,
    ("POST", "/api/mobile/v1/chat"): BOOKS,
    ("POST", "/api/mobile/v1/chat/stream"): BOOKS,
    ("POST", "/api/mobile/v1/briefing"): BOOKS,
    ("POST", "/api/mobile/v1/uploads"): BOOKS,
    ("POST", "/api/mobile/v1/transcribe"): BOOKS,
    ("POST", "/api/mobile/v1/proposals/{token}/confirm"): BOOKS,
    ("POST", "/api/mobile/v1/proposals/{token}/cancel"): BOOKS,
    ("POST", "/api/mobile/v1/proposals/{token}/edit"): BOOKS,
    ("POST", "/api/mobile/v1/statements/{statement_id}/next"): BOOKS,
    ("POST", "/api/mobile/v1/postings/{audit_log_id}/undo"): BOOKS,
    # a document from the books: whoever reads the books or the reports
    ("GET", "/api/mobile/v1/documents/invoices/{invoice_id}"): frozenset({"owner", "cfo", "accountant", "viewer", "personal"}),
    # the second person
    ("GET", "/api/mobile/v1/approvals"): frozenset({"owner", "cfo", "manager"}),
    ("POST", "/api/mobile/v1/approvals/{token}/approve"): frozenset({"owner", "cfo", "manager"}),
    ("POST", "/api/mobile/v1/approvals/{token}/reject"): frozenset({"owner", "cfo", "manager"}),
}


def test_n29_every_mobile_route_is_in_the_contract_with_its_roles():
    from app.main import app
    served = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() if p.startswith("/api/mobile/v1") for m in ops}
    assert served == set(MOBILE_ROUTES), {"not in the contract": served - set(MOBILE_ROUTES),
                                          "no longer served": set(MOBILE_ROUTES) - served}


@pytest.mark.parametrize("route", sorted(MOBILE_ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
def test_n29_each_mobile_route_is_open_to_exactly_its_roles(route):
    expected = MOBILE_ROUTES[route]
    perm = ROUTE_PERMISSIONS.get(route)
    if expected is PUBLIC:
        assert perm is None                         # served before sign-in (app/main.py PUBLIC_PATHS)
        return
    assert {r for r in ALL if role_can(r, perm)} == set(expected)
