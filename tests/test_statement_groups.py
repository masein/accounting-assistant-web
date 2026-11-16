"""Step by step in groups (chat review 2026-11-10, scenarios CD1–CD4).

From production: a school's statement of 205 rows took 139 separate confirms; one day alone had 26 small
card-to-card tuition transfers, each its own card. Now, once a row's card is confirmed, Next offers one card for
the open rows like it — the same direction and the first words of the narration — with the account that card had,
a voucher per day. Figures and card numbers made up.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.db.tenant import use_company
from app.models.bank_statement import BankStatement, BankStatementRow
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)
from tests.test_statement_step_by_step import _day_bank

TUITION = "انتقال کارت به کارت از {card} شهریه"


def _statement(db, co, rows) -> str:
    """rows: (narration, credit, debit) — one a day from 2026-08-01."""
    with use_company(co["cid"]):
        s = BankStatement(bank_name="Day", source_type="pdf", source_filename=f"{uuid.uuid4().hex[:6]}.pdf",
                          currency="IRR", from_date=date(2026, 8, 1), to_date=date(2026, 8, 9), status="parsed",
                          total_rows=len(rows))
        db.add(s)
        db.flush()
        for i, (desc, credit, debit) in enumerate(rows, start=1):
            db.add(BankStatementRow(statement_id=s.id, row_index=i, tx_date=date(2026, 8, min(i, 9)), description=desc,
                                    debit=debit, credit=credit, balance=None, confidence=1.0, recon_status="unmatched",
                                    suggested_account_code="4110", category="misc"))
        db.commit()
        return str(s.id)


def _next(client, phone, sid, session=None):
    r = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone,
                    json={"session_id": session} if session else {})
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, phone, card):
    r = client.post("/ai-accountant/execute", headers=phone, json={"confirmation_token": card["confirmation_token"]})
    assert r.status_code == 200, r.text


def test_cd1_rows_alike_in_one_card(client, db, co, phone):
    _day_bank(db, co)
    sid = _statement(db, co, [(TUITION.format(card=f"6037-99{i}"), 1_000_000 * i, 0) for i in range(1, 5)]
                     + [("کارمزد پایا", 0, 12_000)])
    client.put("/api/mobile/v1/me/language", headers=phone, json={"language": "fa"})
    first = _next(client, phone, sid)
    session = first["session_id"]
    _confirm(client, phone, first["proposals"][0])                         # row 1, its account 4110
    group = _next(client, phone, sid, session)
    [card] = group["proposals"]
    assert card["tool_name"] == "propose_post_statement"
    assert "۳ ردیف دیگر مثل ردیف قبل است («انتقال کارت به»)" in group["text"]
    assert card["preview"]["money_in_account_code"] == "4110" and len(card["preview"]["row_ids"]) == 3
    assert card["preview"]["per"] == "day" and card["preview"]["amount"] == 9_000_000             # 2 + 3 + 4 million
    # waiting: Next again names the open card, no second one (CD3)
    wait = _next(client, phone, sid, session)
    assert wait["proposals"] == [] and "بالاتر هنوز منتظر است" in wait["text"]
    _confirm(client, phone, card)
    with use_company(co["cid"]):
        rows = db.query(BankStatementRow).filter(BankStatementRow.statement_id == uuid.UUID(sid)).all()
        assert sum(1 for r in rows if r.created_transaction_id) == 4
    last = _next(client, phone, sid, session)                                # the fee row: one at a time
    assert last["proposals"][0]["tool_name"] == "propose_create_transaction"
    assert "کارمزد" in last["proposals"][0]["preview"]["description"]


def test_cd2_a_cancelled_group_comes_one_at_a_time(client, db, co, phone):
    _day_bank(db, co)
    sid = _statement(db, co, [(TUITION.format(card=f"6037-88{i}"), 2_000_000 + i, 0) for i in range(1, 5)])
    first = _next(client, phone, sid)
    session = first["session_id"]
    _confirm(client, phone, first["proposals"][0])
    group = _next(client, phone, sid, session)
    token = group["proposals"][0]["confirmation_token"]
    assert client.post(f"/ai-accountant/proposals/{token}/cancel", headers=phone).status_code == 200
    one = _next(client, phone, sid, session)
    assert one["proposals"][0]["tool_name"] == "propose_create_transaction"     # not offered again
    _confirm(client, phone, one["proposals"][0])
    again = _next(client, phone, sid, session)
    assert again["proposals"][0]["tool_name"] == "propose_create_transaction"


def test_cd4_too_few_alike_or_no_words():
    from app.services.ai_accountant.statement_next import narration_pattern
    assert narration_pattern("انتقال کارت به کارت از ۶۰۳۷۹۹۱۱۲۲ شهریه") == "انتقال کارت به"
    assert narration_pattern("انتقال كارت به كارت از 6037991122") == "انتقال کارت به"   # Arabic letters, Latin digits
    assert narration_pattern("123456 / 7890") == "" and narration_pattern("") == ""
    assert narration_pattern("POS SNAPP TEHRAN 0912") == "pos snapp tehran"


def test_cd4_only_one_other_row_alike(client, db, co, phone):
    _day_bank(db, co)
    sid = _statement(db, co, [(TUITION.format(card="6037-771"), 1_000_000, 0), (TUITION.format(card="6037-772"), 1_500_000, 0),
                              ("کارمزد پایا", 0, 12_000)])
    first = _next(client, phone, sid)
    _confirm(client, phone, first["proposals"][0])
    nxt = _next(client, phone, sid, first["session_id"])
    assert nxt["proposals"][0]["tool_name"] == "propose_create_transaction"
