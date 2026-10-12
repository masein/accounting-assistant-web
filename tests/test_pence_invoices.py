"""Invoices and VAT to the penny (docs/PLAN_PENCE.md, phase P3; scenarios Q7, Q8).

An invoice's lines and VAT were rounded to whole pounds: 3 × £19.99 came to £60,
its VAT to £12, a payment of £30.50 was refused or cut. Pounds keep their pence
from the line to the payment, the VAT return and the PDF; rials are computed
exactly as before."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.test_statement_export import _company


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "uk", "GBP")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _entity(api, kind, name):
    r = api.post("/entities", json={"type": kind, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _invoice(api, kind, party, items, *, number, day="2026-05-10"):
    r = api.post("/invoices", json={"number": number, "kind": kind, "status": "issued", "issue_date": day,
                                    "due_date": day, "amount": 0, "entity_id": party, "items": items})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_q7_an_invoice_to_the_penny(uk):
    api, _ = uk
    aria = _entity(api, "client", "Aria Design Ltd")
    inv = _invoice(api, "sales", aria, [{"product_name": "Widget", "quantity": 3, "unit_price": 19.99, "tax_rate": 20}],
                   number="INV-P1")
    assert (inv["subtotal"], inv["tax_total"], inv["grand_total"]) == (59.97, 11.99, 71.96)   # 11.994 → 11.99
    assert inv["amount"] == 71.96 and inv["items"][0]["line_total"] == 59.97
    r = api.post(f"/invoices/{inv['id']}/payments", json={"amount": "30.50", "date": "2026-05-20"})
    assert r.status_code == 201, r.text
    got = next(x for x in api.get("/invoices").json() if x["id"] == inv["id"])   # the list sums its own
    assert (got["amount_paid"], got["balance_due"], got["status"]) == (30.5, 41.46, "partially_paid")
    r = api.post(f"/invoices/{inv['id']}/payments", json={"amount": 41.46, "date": "2026-05-25"})
    assert r.status_code == 201, r.text
    got = next(x for x in api.get("/invoices").json() if x["id"] == inv["id"])
    assert got["balance_due"] == 0 and got["status"] == "paid" and got["amount_paid"] == 71.96
    # the receivable is cleared to the penny in the books too
    ledger = {x["account_code"]: x for x in api.get("/reports/ledger-summary").json()["rows"]}
    assert ledger["1100"]["debit_turnover"] == ledger["1100"]["credit_turnover"] == 71.96
    assert api.get(f"/invoices/{inv['id']}/pdf").status_code == 200


def test_q7_more_places_than_the_currency_has(uk):
    api, _ = uk
    aria = _entity(api, "client", "Aria Design Ltd")
    inv = _invoice(api, "sales", aria, [{"product_name": "Widget", "quantity": 1, "unit_price": 100, "tax_rate": 0}],
                   number="INV-P2")
    r = api.post(f"/invoices/{inv['id']}/payments", json={"amount": "10.005", "date": "2026-05-20"})
    assert r.status_code == 400 and r.json()["detail"] == "The amount can have at most 2 decimal places in GBP."
    r = api.post(f"/invoices/{inv['id']}/credit-notes", json={"amount": "1.001", "date": "2026-05-20", "reason": "x"})
    assert r.status_code == 400 and "at most 2 decimal places" in r.json()["detail"]
    # a unit price may go to 4 places; its line rounds to the penny
    inv = _invoice(api, "sales", aria, [{"product_name": "Screws", "quantity": 7, "unit_price": "0.1250", "tax_rate": 0}],
                   number="INV-P3")
    assert inv["amount"] == 0.88                                                           # 0.875 → 0.88


def test_q8_vat_boxes_to_the_penny(uk, db):
    api, cid = uk
    from app.db.tenant import use_company
    from app.services.uk_mtd import periods as P
    from app.services.uk_mtd.vat import vat_return
    aria, office = _entity(api, "client", "Aria Design Ltd"), _entity(api, "supplier", "Office World")
    _invoice(api, "sales", aria, [{"product_name": "Widget", "quantity": 3, "unit_price": 19.99, "tax_rate": 20}],
             number="INV-Q8")
    _invoice(api, "purchase", office, [{"product_name": "Paper", "quantity": 1, "unit_price": 12.50, "tax_rate": 20}],
             number="BILL-Q8")
    with use_company(cid):
        r = vat_return(db, P.vat_period_ending(date(2026, 6, 30), 1), currency="GBP")
    b = r["boxes"]
    assert (b["1"], b["4"], b["5"]) == (Decimal("11.99"), Decimal("2.5"), Decimal("9.49"))
    assert (b["6"], b["7"]) == (59, 12)                                  # whole pounds, the pence left out
    body = r["hmrc_body"]
    assert body["vatDueSales"] == 11.99 and body["netVatDue"] == 9.49
    assert body["totalValueSalesExVAT"] == 59 and body["totalValuePurchasesExVAT"] == 12


def test_a_rial_invoice_rounds_as_it_always_has(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        party = _entity(api, "client", "آریا")
        inv = _invoice(api, "sales", party, [{"product_name": "کالا", "quantity": 0.5, "unit_price": 5, "tax_rate": 10}],
                       number="F-1")
        assert inv["items"][0]["line_total"] == 2 and isinstance(inv["amount"], int)       # round(2.5) → 2, as before
        r = api.post(f"/invoices/{inv['id']}/payments", json={"amount": "1.5", "date": "2026-05-20"})
        assert r.status_code == 400 and r.json()["detail"] == "The amount can't have decimals in IRR."
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


def test_the_pdf_says_the_pence_in_figures_and_words():
    from app.services.documents.formatting import amount_in_words, fmt_amount, fmt_money
    assert fmt_money("71.96", "GBP", "uk") == "£71.96" and fmt_money(950, "GBP", "uk") == "£950.00"
    assert fmt_money(-7.5, "GBP", "uk") == "-£7.50"
    assert fmt_money(2_500_000, "IRR", "ir") == "۲,۵۰۰,۰۰۰ ﷼"                        # rial documents as before
    assert fmt_amount("1234.5", "GBP") == "1,234.50" and fmt_amount("1234.5", "GBP", "ir") == "۱,۲۳۴٫۵۰"
    assert amount_in_words("71.96", "GBP", "uk") == "Seventy-one pounds and ninety-six pence"
    assert amount_in_words(120, "GBP", "uk") == "One hundred and twenty pounds"
    assert amount_in_words("10.01", "USD", "uk") == "Ten dollars and one cents"
    assert amount_in_words(2_500_000, "IRR", "ir") == "دو میلیون و پانصد هزار ریال"


def test_the_accountants_invoice_card_adds_up_as_the_invoice_will(uk, db):
    import asyncio
    api, cid = uk
    _entity(api, "client", "Aria Design Ltd")
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.invoice_tools import ProposeCreateInvoice, ProposeCreateInvoiceInput
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u1", username="t", user_message="invoice Aria 3 widgets at £19.99 plus VAT")
        out = asyncio.run(ProposeCreateInvoice().run(ctx, ProposeCreateInvoiceInput(
            kind="sales", party="Aria Design Ltd", currency="GBP",
            lines=[{"description": "Widget", "quantity": 3, "unit_price": "19.99", "tax_rate": 20}])))
    assert (out["preview"]["subtotal"], out["preview"]["tax"], out["preview"]["total"]) == \
        (Decimal("59.97"), Decimal("11.99"), Decimal("71.96"))


def test_q7_a_payment_confirmed_in_the_chat_keeps_its_pence(uk, db):
    """The accountant's payment card posted int(amount): £30.50 went in as £30."""
    import asyncio
    api, cid = uk
    aria = _entity(api, "client", "Aria Design Ltd")
    inv = _invoice(api, "sales", aria, [{"product_name": "Widget", "quantity": 3, "unit_price": 19.99, "tax_rate": 20}],
                   number="INV-AI1")
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.execute_service import execute_proposal
    from app.services.ai_accountant.invoice_tools import (
        GetInvoice, GetInvoiceInput, ProposeRecordInvoicePayment, ProposeRecordInvoicePaymentInput,
    )
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u1", username="t", user_message="Aria paid £30.50")
        card = asyncio.run(ProposeRecordInvoicePayment().run(ctx, ProposeRecordInvoicePaymentInput(
            invoice="INV-AI1", amount="30.50", date=date(2026, 5, 20))))
        execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1", actor_username="t")
        db.commit()
        got = asyncio.run(GetInvoice().run(ctx, GetInvoiceInput(invoice="INV-AI1")))
    assert (got["amount"], got["amount_paid"], got["balance_due"]) == (Decimal("71.96"), Decimal("30.5"), Decimal("41.46"))
    assert got["payments"][0]["amount"] == Decimal("30.5")


def test_quotes_matching_and_reading_documents_keep_pence(uk):
    api, _ = uk
    aria = _entity(api, "client", "Aria Design Ltd")
    q = api.post("/quotes", json={"number": "Q-P1", "issue_date": "2026-05-10", "valid_until": "2026-06-10",
                                  "entity_id": aria, "amount": 0,
                                  "items": [{"product_name": "Design", "quantity": 1, "unit_price": "99.99", "tax_rate": 0}]})
    assert q.status_code in (200, 201), q.text
    item = q.json()["items"][0]
    assert item["unit_price"] == 99.99 and item["line_total"] == 99.99            # they read 99
    from app.services import three_way_match
    po = [{"key": "1", "description": "Paper", "ordered_qty": 10, "received_qty": 10, "unit_price": Decimal("12.49")}]
    ok = three_way_match.match(po, [{"key": "1", "description": "Paper", "quantity": 10, "unit_price": Decimal("12.50")}])
    assert ok.matched                                                              # within 1 % (it raised)
    over = three_way_match.match(po, [{"key": "1", "description": "Paper", "quantity": 10, "unit_price": Decimal("12.75")}])
    assert [d["type"] for d in over.discrepancies] == ["over_price"]
    from app.services.ocr_extract import coerce_amount
    assert coerce_amount("7.50") == Decimal("7.5") and coerce_amount("£1,234.56") == Decimal("1234.56")
    assert coerce_amount(21.6) == Decimal("21.6") and coerce_amount("۳٬۶۹۰٬۷۲۰ ریال") == 3_690_720
    assert coerce_amount("3,690,720.00") == 3_690_720 and type(coerce_amount("950.00")) is int


INVOICE_PAGES = [
    "/invoices", "/quotes", "/recurring-invoices", "/purchase-orders", "/tax/uk/vat/periods",
    "/tax/uk/vat/return?end=2026-06-30", "/tax/uk/vat/return/export?end=2026-06-30", "/tax/uk/itsa/update",
    "/reports/tax-summary", "/reports/owner-dashboard", "/reports/cash-forecast",
    "/manager-reports/operational/accounts-receivable", "/manager-reports/operational/accounts-payable",
    "/manager-reports/operational/debtor-creditor", "/manager-reports/sales/by-invoice",
    "/manager-reports/purchases/by-invoice", "/manager-reports/sales/by-product", "/manager-reports/purchases/by-product",
    "/manager-reports/sales/trend", "/brain/cfo/report", "/brain/ceo/report", "/insights", "/summary",
    "/manager-reports/close-pack/checklist", "/products/profitability",
]


def test_every_invoice_page_reads_pence(uk):
    """Invoices, payments, a credit note, a quote and a purchase order with
    pence: every page about them answers (an int() cut the pence, a Decimal
    met a float, an int field refused 7.5)."""
    api, _ = uk
    aria, office = _entity(api, "client", "Aria Design Ltd"), _entity(api, "supplier", "Office World")
    sale = _invoice(api, "sales", aria, [{"product_name": "Widget", "quantity": 3, "unit_price": 19.99, "tax_rate": 20,
                                          "unit_cost": "7.25"}], number="INV-S1")
    bill = _invoice(api, "purchase", office, [{"product_name": "Paper", "quantity": 2, "unit_price": "12.49", "tax_rate": 20}],
                    number="BILL-S1")
    assert api.post(f"/invoices/{sale['id']}/payments", json={"amount": "30.50", "date": "2026-05-20"}).status_code == 201
    assert api.post(f"/invoices/{bill['id']}/payments", json={"amount": "10.01", "date": "2026-05-21"}).status_code == 201
    r = api.post(f"/invoices/{sale['id']}/credit-notes", json={"amount": "5.99", "date": "2026-05-22", "reason": "dent"})
    assert r.status_code in (200, 201), r.text
    q = api.post("/quotes", json={"number": "Q-S1", "issue_date": "2026-05-10", "valid_until": "2026-06-10",
                                  "entity_id": aria, "amount": 0,
                                  "items": [{"product_name": "Design", "quantity": 1.5, "unit_price": "99.99", "tax_rate": 20}]})
    assert q.status_code in (200, 201), q.text
    assert q.json()["amount"] == 179.99                       # 1.5 × 99.99 = 149.985 → 149.99; VAT 29.998 → 30.00
    po = api.post("/purchase-orders", json={"entity_id": office, "order_date": "2026-05-10", "currency": "GBP",
                                            "lines": [{"description": "Chairs", "ordered_qty": 3, "unit_price": "45.99"}]})
    assert po.status_code in (200, 201), po.text
    failed = {}
    for path in INVOICE_PAGES:
        r = api.get(path)
        if r.status_code >= 500:
            failed[path] = r.status_code
    assert failed == {}, failed
