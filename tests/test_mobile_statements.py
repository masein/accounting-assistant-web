"""A bank statement on the phone, one difference at a time (scenario N31;
roadmap ROADMAP_ANDROID_CHAT P2.3): no model call, the same proposal tool."""
from __future__ import annotations

import uuid
from datetime import date

from app.db.tenant import use_company
from app.models.bank_statement import BankStatement, BankStatementRow
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _statement(db, co) -> str:
    """Two rows the books don't have (a fee, a transfer) in a window no other test uses."""
    with use_company(co["cid"]):
        s = BankStatement(bank_name="Mellat", source_type="csv", source_filename=f"{uuid.uuid4().hex[:6]}.csv",
                          currency="IRR", from_date=date(2026, 9, 1), to_date=date(2026, 9, 3), status="parsed",
                          total_rows=2)
        db.add(s)
        db.flush()
        for i, (d, desc, debit) in enumerate([(date(2026, 9, 1), "کارمزد بانکی", 90_000),
                                               (date(2026, 9, 3), "انتقال به حساب آریا", 4_500_000)], start=1):
            db.add(BankStatementRow(statement_id=s.id, row_index=i, tx_date=d, description=desc, debit=debit, credit=0,
                                    balance=None, confidence=1.0, recon_status="unmatched",
                                    suggested_account_code="6112", category="misc"))
        db.commit()
        return str(s.id)


def test_n31_next_gives_each_unrecorded_row_as_a_voucher_then_says_done(client, db, co, phone, monkeypatch):
    from app.api import ai_accountant as api_mod

    async def never(*a, **kw):
        raise AssertionError("the model was called")
    monkeypatch.setattr(api_mod, "run_chat_turn", never)
    sid = _statement(db, co)
    fa = {**phone, "X-UI-Language": "fa"}
    client.put(f"{API}/me/language", headers=phone, json={"language": "fa"})
    first = client.post(f"{API}/statements/{sid}/next", headers=fa, json={}).json()
    card, words = first["blocks"]
    assert card["type"] == "proposal" and card["amount"]["value"] == 90_000
    assert card["title"] == "کارمزد بانکی" and card["date"]["iso"] == "2026-09-01"     # the bank's date and words
    assert words["kind"] == "statement_next" and words["statement_id"] == sid and words["text"] == "پس از این، ۱ ردیف دیگر مانده."
    thread = first["thread_id"]
    second = client.post(f"{API}/statements/{sid}/next", headers=fa, json={"thread_id": thread}).json()
    card2, last = second["blocks"]
    assert card2["amount"]["value"] == 4_500_000 and "kind" not in last                 # the last: no "next" offered
    done = client.post(f"{API}/statements/{sid}/next", headers=fa, json={"thread_id": thread}).json()["blocks"]
    assert [b["type"] for b in done] == ["text"] and "همهٔ ردیف‌های این صورتحساب" in done[0]["text"]
    # confirming posts the row, from the statement
    posted = client.post(f"{API}/proposals/{card['token']}/confirm", headers=phone).json()
    assert posted["state"] == "posted"
    with use_company(co["cid"]):
        db.expire_all()
        row = db.query(BankStatementRow).filter(BankStatementRow.statement_id == uuid.UUID(sid),
                                                BankStatementRow.row_index == 1).one()
        assert row.created_transaction_id is not None
    # every block is one the clients know (docs/contracts)
    from tests.test_mobile_contract import contract_errors
    for b in first["blocks"] + second["blocks"] + done:
        assert contract_errors(b) == [], b
    # the thread keeps the cards and the words, for the history to redraw
    msgs = client.get(f"{API}/threads/{thread}/messages", headers=phone).json()
    assert [b["type"] for m in msgs for b in m["blocks"]] == ["proposal", "text", "proposal", "text", "text"]


def test_n31_someone_elses_or_no_statement(client, db, co, phone):
    assert client.post(f"{API}/statements/{uuid.uuid4()}/next", headers=phone, json={}).status_code == 404
    assert client.post(f"{API}/statements/nonsense/next", headers=phone, json={}).status_code == 404


def test_n31_the_bank_is_named_in_the_readers_language():
    from app.services.ai_accountant.statement_intake import _reply, bank_label
    assert bank_label("Mellat", "fa") == "ملت" and bank_label("Mellat", "en") == "Mellat"
    assert bank_label("Unknown", "fa") is None and bank_label("Some Bank", "fa") == "Some Bank"
    said = _reply("fa", {"bank_name": "Mellat", "total_rows": 12, "counts": {"unrecorded": 2}, "clean": False})
    assert said.startswith("صورتحساب ملت را خواندم") and "Mellat" not in said
