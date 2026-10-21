"""A bank statement's differences one at a time on the web, and the account it is compared with
(scenarios AJ11–AJ13).

From the owner, 2026-10-21: فرهنگ نو's Day Bank statement in the chat, «رفع قدم‌به‌قدم» — the first
difference came as a card and, once it was confirmed, nothing came next. The card's bank side was 1110
«موجودی نقد و بانک», not «بانک دی»: the reader names the bank «Day», the books «بانک دی» (made by the
journal import, account 0105222028000), and the two were never matched, so the statement was compared
with the chart's own bank account and every row of it looked missing from the books."""
from __future__ import annotations

import uuid
from datetime import date

from app.db.tenant import use_company
from app.models.bank_statement import BankStatement, BankStatementRow
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

DAY_NUMBER = "0105222028000"


def _day_bank(db, co, *, entity=True) -> str:
    """«بانک دی» as the journal import makes it: a bank party with its number, its own account."""
    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    with use_company(co["cid"]):
        code, _ = _resolve_bank_account(db, "بانک دی", None, None)
        if entity:
            db.add(Entity(type="bank", name="بانک دی", code=code, account_number=DAY_NUMBER))
        db.commit()
        return code


def _statement(db, co, *, bank="Day", number=None, rows=3) -> str:
    with use_company(co["cid"]):
        s = BankStatement(bank_name=bank, account_number=number, source_type="pdf",
                          source_filename=f"{uuid.uuid4().hex[:6]}_receipt.pdf", currency="IRR",
                          from_date=date(2026, 7, 23), to_date=date(2026, 7, 26), status="parsed", total_rows=rows)
        db.add(s)
        db.flush()
        for i in range(1, rows + 1):
            db.add(BankStatementRow(statement_id=s.id, row_index=i, tx_date=date(2026, 7, 22 + i),
                                    description=f"تعیین وضعیت چک عادی {i}", debit=0, credit=350_000_000 + i,
                                    balance=None, confidence=1.0, recon_status="unmatched",
                                    suggested_account_code="4110", category="misc"))
        db.commit()
        return str(s.id)


def _resolve(db, co, sid):
    from app.services.statement_import import bank_account_guessed, resolve_bank_account
    with use_company(co["cid"]):
        s = db.get(BankStatement, uuid.UUID(sid))
        return (*resolve_bank_account(db, s), bank_account_guessed(db, s))


# --- AJ11: «Day» is «بانک دی» ------------------------------------------------------------------------------------

def test_aj11_day_is_the_bank_called_bank_dey():
    from app.services.statement_import import canonical_bank
    assert canonical_bank("Day") == canonical_bank("بانک دی") == canonical_bank("Day Bank") == "Dey"
    assert canonical_bank("دی") is None                       # alone, a month
    assert canonical_bank("Mellat") == canonical_bank("بانک ملت") == "Mellat"


def test_aj11_the_statement_finds_bank_dey_by_name_or_number(db, co):
    code = _day_bank(db, co)
    assert _resolve(db, co, _statement(db, co)) == (code, "bank", False)
    assert _resolve(db, co, _statement(db, co, bank="Unknown", number=DAY_NUMBER)) == (code, "account_number", False)


def test_aj11_an_account_named_for_the_bank_without_a_party(db, co):
    from app.models.account import Account
    from app.services.chart_service import create_account
    with use_company(co["cid"]):
        loan = create_account(db, name="وام از بانک دی", parent_code="2110").code
        acc = create_account(db, name=f"بانک دی حساب {DAY_NUMBER}", parent_code="1110").code
        db.commit()
    assert _resolve(db, co, _statement(db, co, number=DAY_NUMBER))[:2] == (acc, "account_name")
    assert _resolve(db, co, _statement(db, co))[:2] == (acc, "account_name")          # by the bank, not the loan
    with use_company(co["cid"]):
        assert db.query(Account).filter(Account.code == loan).one().name == "وام از بانک دی"


# --- AJ12: which account, and a guess is said -------------------------------------------------------------------

def test_aj12_a_named_bank_none_of_the_books_has_is_only_a_guess(db, co):
    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    sid = _statement(db, co, bank="Parsian")
    assert _resolve(db, co, sid)[1:] == ("default", False)                # one bank in the books: its account, as before
    with use_company(co["cid"]):
        code, _ = _resolve_bank_account(db, "بانک ملت", None, None)
        db.add(Entity(type="bank", name="بانک ملت", code=code))
        db.commit()
    assert _resolve(db, co, sid)[1:] == ("default", True)                 # Parsian isn't Mellat: chosen first
    assert _resolve(db, co, _statement(db, co, bank="Unknown"))[1:] == ("default", False)   # nothing names a bank


def test_aj12_the_card_says_which_account(db, co):
    from app.services.ai_accountant.statement_intake import _review_fields
    code = _day_bank(db, co)
    sid = _statement(db, co)
    with use_company(co["cid"]):
        acct = _review_fields(db, uuid.UUID(sid))["bank_account"]
    assert acct["code"] == code and acct["name"] and acct["guessed"] is False


# --- AJ13: step by step on the web --------------------------------------------------------------------------------

def test_aj13_one_card_at_a_time_and_the_next_after_confirming(client, db, co, phone, monkeypatch):
    from app.api import ai_accountant as api_mod

    async def never(*a, **kw):
        raise AssertionError("the model was called")
    monkeypatch.setattr(api_mod, "run_chat_turn", never)
    code = _day_bank(db, co)
    sid = _statement(db, co)
    client.put("/api/mobile/v1/me/language", headers=phone, json={"language": "fa"})
    first = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={}).json()
    assert first["left"] == 2 and first["text"] == "پس از این، ۲ ردیف دیگر مانده." and first["choose"] is False
    [card] = first["proposals"]
    bank_line = next(ln for ln in card["preview"]["lines"] if ln["account_code"] == code)
    assert bank_line["debit"] == 350_000_001                               # the money in, on «بانک دی»'s own account
    assert card["card"]["date"]["iso"] == "2026-07-23"
    session = first["session_id"]
    done = client.post("/ai-accountant/execute", headers=phone, json={"confirmation_token": card["confirmation_token"]})
    assert done.status_code == 200, done.text
    second = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={"session_id": session}).json()
    assert second["left"] == 1 and second["proposals"][0]["card"]["date"]["iso"] == "2026-07-24"
    third = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={"session_id": session}).json()
    assert third["left"] == 0 and len(third["proposals"]) == 1                  # «بعدی» skips the pending one
    last = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={"session_id": session}).json()
    assert last["proposals"] == [] and "همهٔ ردیف‌های این صورتحساب" in last["text"]
    # the conversation keeps each card with its words, and where step by step stands
    msgs = client.get(f"/ai-accountant/sessions/{session}/messages", headers=phone).json()
    steps = [m["content"]["statement_next"] for m in msgs if m["content"].get("statement_next")]
    assert [s["left"] for s in steps] == [2, 1, 0, 0] and all(s["statement_id"] == sid for s in steps)
    assert sum(len(m["cards"]) for m in msgs) == 3


def test_aj12_a_guessed_account_is_chosen_before_any_card(client, db, co, phone):
    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    with use_company(co["cid"]):
        code, _ = _resolve_bank_account(db, "بانک ملت", None, None)
        db.add(Entity(type="bank", name="بانک ملت", code=code))
        db.commit()
    sid = _statement(db, co, bank="Parsian")
    out = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={}).json()
    assert out["choose"] is True and out["proposals"] == [] and code in {c["code"] for c in out["choices"]}
    assert client.put(f"/brain/bank-statements/{sid}/bank-account", headers=phone, json={"code": code}).status_code == 200
    again = client.post(f"/ai-accountant/statements/{sid}/next", headers=phone, json={"session_id": out["session_id"]}).json()
    assert again["choose"] is False and len(again["proposals"]) == 1
    assert any(ln["account_code"] == code for ln in again["proposals"][0]["preview"]["lines"])


def test_aj12_the_phone_asks_for_the_account_too(client, db, co, phone):
    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    with use_company(co["cid"]):
        code, _ = _resolve_bank_account(db, "بانک ملت", None, None)
        db.add(Entity(type="bank", name="بانک ملت", code=code))
        db.commit()
    sid = _statement(db, co, bank="Parsian")
    blocks = client.post(f"/api/mobile/v1/statements/{sid}/next", headers=phone, json={}).json()["blocks"]
    assert [b["type"] for b in blocks] == ["text"] and "kind" not in blocks[0]
