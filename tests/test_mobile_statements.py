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
    assert bank_label("Mellat", "ar") == "ملت"                      # one script: the 2026-10-11 pass
    assert bank_label("Unknown", "fa") is None and bank_label("Some Bank", "fa") == "Some Bank"
    said = _reply("fa", {"bank_name": "Mellat", "total_rows": 12, "counts": {"unrecorded": 2}, "clean": False})
    assert said.startswith("صورتحساب ملت را خواندم") and "Mellat" not in said


def test_n41_money_against_an_open_invoice_is_a_payment_on_it(client, db, co, phone, monkeypatch):
    """«ARIA DESIGN LTD INV-1001» on the statement was offered as a voucher to
    Sales — the sale counted twice, the invoice still open. Now the walk offers
    a payment on the invoice (by its number, or the one open invoice of the
    party named), and posting it clears the receivable and the row."""
    from app.api import ai_accountant as api_mod
    from app.api.invoices import _invoice_totals, insert_invoice
    from app.models.entity import Entity
    from app.models.invoice import Invoice
    from app.schemas.invoice import InvoiceCreate

    async def never(*a, **kw):
        raise AssertionError("the model was called")
    monkeypatch.setattr(api_mod, "run_chat_turn", never)
    with use_company(co["cid"]):
        aria, north = Entity(type="client", name="Aria Design Ltd"), Entity(type="supplier", name="Northwind Supplies")
        db.add_all([aria, north])
        db.flush()
        for number, kind, who, price in (("INV-1001", "sales", aria, 1_200_000), ("NW-778", "purchase", north, 300_000)):
            insert_invoice(db, InvoiceCreate(number=number, kind=kind, issue_date=date(2026, 8, 20), due_date=date(2026, 9, 20),
                                             amount=0, currency="IRR", entity_id=who.id,
                                             items=[{"product_name": "Work", "quantity": 1, "unit_price": price, "tax_rate": 0}]))
        s = BankStatement(bank_name="Mellat", source_type="csv", source_filename=f"{uuid.uuid4().hex[:6]}.csv",
                          currency="IRR", from_date=date(2026, 9, 5), to_date=date(2026, 9, 7), status="parsed", total_rows=3)
        db.add(s)
        db.flush()
        for i, (d, desc, debit, credit) in enumerate([(date(2026, 9, 5), "ARIA DESIGN LTD INV-1001", 0, 600_000),
                                                       (date(2026, 9, 6), "NORTHWIND SUPPLIES", 300_000, 0),
                                                       (date(2026, 9, 7), "Coffee", 40_000, 0)], start=1):
            db.add(BankStatementRow(statement_id=s.id, row_index=i, tx_date=d, description=desc, debit=debit, credit=credit,
                                    balance=None, confidence=1.0, recon_status="unmatched"))
        db.commit()
        sid = str(s.id)
    first = client.post(f"{API}/statements/{sid}/next", headers=phone, json={}).json()
    card = first["blocks"][0]
    assert card["tool"] == "propose_record_invoice_payment" and card["amount"]["value"] == 600_000
    assert card["title"] == "ARIA DESIGN LTD INV-1001" and card["date"]["iso"] == "2026-09-05"
    with use_company(co["cid"]):
        from app.services.account_resolver import resolve_account_code
        ar, ap = resolve_account_code(db, "ar"), resolve_account_code(db, "ap")
    assert [(ln["account"], ln["debit"], ln["credit"]) for ln in card["lines"]][1] == (ar, 0, 600_000)   # not Sales
    thread = first["thread_id"]
    second = client.post(f"{API}/statements/{sid}/next", headers=phone, json={"thread_id": thread}).json()["blocks"][0]
    assert second["tool"] == "propose_record_invoice_payment"                      # by the supplier's name
    assert [(ln["account"], ln["debit"]) for ln in second["lines"]][0] == (ap, 300_000)
    third = client.post(f"{API}/statements/{sid}/next", headers=phone, json={"thread_id": thread}).json()["blocks"][0]
    assert third["tool"] == "propose_create_transaction"                             # no invoice: a voucher, as before
    from tests.test_mobile_contract import contract_errors
    assert contract_errors(card) == [], card
    posted = client.post(f"{API}/proposals/{card['token']}/confirm", headers=phone).json()
    assert posted["state"] == "posted" and posted["block"]["undo_seconds"] > 0
    # Undo under the card: the entry goes, the invoice reopens, the row is offered again
    undone = client.post(f"{API}/postings/{posted['block']['audit_log_id']}/undo", headers=phone)
    assert undone.status_code == 200 and undone.json() == {"state": "undone", "mode": "deleted"}, undone.text
    assert client.post(f"{API}/postings/{posted['block']['audit_log_id']}/undo", headers=phone).status_code == 400
    with use_company(co["cid"]):
        db.expire_all()
        inv = db.query(Invoice).filter(Invoice.number == "INV-1001").one()
        assert _invoice_totals(db, inv)[2] == 1_200_000
        row = db.query(BankStatementRow).filter(BankStatementRow.statement_id == uuid.UUID(sid),
                                                BankStatementRow.row_index == 1).one()
        assert row.created_transaction_id is None and row.recon_status == "unmatched"
    # opened again, the card says it was undone, with its seal's reference and date
    history = client.get(f"{API}/threads/{thread}/messages", headers=phone).json()
    stamp = next(b for m in history for b in m.get("blocks") or [] if b.get("token") == first["blocks"][0]["token"])
    assert stamp["state"] == "executed" and stamp["posted"]["undone"] is True
    assert stamp["posted"]["voucher"] == "INV-1001" and stamp["posted"]["date"]["iso"] == "2026-09-05"
    from tests.test_mobile_contract import contract_errors as errors
    assert errors(stamp) == [], stamp
    card = client.post(f"{API}/statements/{sid}/next", headers=phone, json={}).json()["blocks"][0]
    assert card["tool"] == "propose_record_invoice_payment" and card["title"] == "ARIA DESIGN LTD INV-1001"
    posted = client.post(f"{API}/proposals/{card['token']}/confirm", headers=phone).json()
    assert posted["state"] == "posted"
    with use_company(co["cid"]):
        db.expire_all()
        inv = db.query(Invoice).filter(Invoice.number == "INV-1001").one()
        assert _invoice_totals(db, inv)[2] == 600_000                                # 1,200,000 less the 600,000
        row = db.query(BankStatementRow).filter(BankStatementRow.statement_id == uuid.UUID(sid),
                                                BankStatementRow.row_index == 1).one()
        assert row.created_transaction_id is not None and row.recon_status == "matched"
    # the row is in the books now: the walk doesn't offer it again
    again = client.post(f"{API}/statements/{sid}/next", headers=phone, json={}).json()["blocks"][0]
    assert again.get("title") != "ARIA DESIGN LTD INV-1001"
    # after the window, Reverse: a compensating entry, the invoice reopens again
    from app.services.ai_accountant.execute_service import reverse_action
    with use_company(co["cid"]):
        r = reverse_action(db, audit_log_id=posted["block"]["audit_log_id"], actor_user_id=str(co["owner"].id))
        assert r.mode == "reversed" and r.reversal_transaction_id
        db.expire_all()
        assert _invoice_totals(db, db.query(Invoice).filter(Invoice.number == "INV-1001").one())[2] == 1_200_000


def test_n41_which_invoice_a_row_settles(db, co):
    from app.api.invoices import insert_invoice
    from app.models.entity import Entity
    from app.schemas.invoice import InvoiceCreate
    from app.services.ai_accountant.statement_intake import open_invoice_for_row as find
    with use_company(co["cid"]):
        a, b = Entity(type="client", name="Aria Design Ltd"), Entity(type="client", name="Brightline")
        db.add_all([a, b])
        db.flush()
        for number, who in (("INV-1001", a), ("INV-1002", b), ("INV-1003", b)):
            insert_invoice(db, InvoiceCreate(number=number, kind="sales", issue_date=date(2026, 8, 20), due_date=date(2026, 9, 20),
                                             amount=0, currency="IRR", entity_id=who.id,
                                             items=[{"product_name": "Work", "quantity": 1, "unit_price": 100, "tax_rate": 0}]))
        db.flush()
        assert find(db, "Payment INV-1002 thanks", "in", "IRR").number == "INV-1002"
        assert find(db, "ARIA DESIGN", "in", "IRR").number == "INV-1001"            # «Ltd» left off by the bank
        assert find(db, "BRIGHTLINE", "in", "IRR") is None                            # two open: not a guess
        assert find(db, "INV-1001", "out", "IRR") is None                             # money out doesn't settle a sale
        assert find(db, "INV-10011", "in", "IRR") is None                             # a longer number isn't it
        assert find(db, "ARIA DESIGN INV-1001", "in", "USD") is None                  # another currency
        db.rollback()
