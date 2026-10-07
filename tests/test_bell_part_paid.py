"""A part-paid invoice stays in the bell (scenario D12).

The bell looked at issued invoices only, so a customer's part payment took an
overdue invoice out of it — while the e-mail reminders (issued and part-paid)
kept going. It now covers both, says what is still open on a part-paid one,
and leaves out whatever payments and credit notes have settled.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.tenant import use_company
from app.utils.jalali import format_jalali
from tests.test_cfo_receivables import _invoice, _pay, co  # noqa: F401 — the fixture


def _bell(co, lang="en"):
    r = co["api"].get("/notifications/feed", headers={"X-UI-Language": lang})
    assert r.status_code == 200, r.text
    return r.json()


def _about(feed, inv):
    return [i for i in feed if inv["number"] in i["title"] and i["kind"] in ("invoice_overdue", "invoice_due")]


def test_a_part_paid_invoice_stays_overdue_and_says_what_is_open(co):
    sale = _invoice(co, days_ago=40)                      # 11,000,000, due 10 days ago
    _pay(co, sale, 4_000_000)
    (item,) = _about(_bell(co), sale)
    assert item["kind"] == "invoice_overdue" and item["level"] == "high"
    due = format_jalali(date.fromisoformat(sale["due_date"]))       # an Iranian company's calendar (#292)
    assert item["message"].endswith(f"day(s) past due ({due}); 7,000,000 IRR still open"), item["message"]
    (fa,) = _about(_bell(co, "fa"), sale)
    assert "مانده" in fa["message"] and "7,000,000" in fa["message"]
    assert "ریال" in fa["message"] and "IRR" not in fa["message"]           # the rial by its name
    for lang in ("es", "ar"):
        assert "7,000,000" in _about(_bell(co, lang), sale)[0]["message"]

    _pay(co, sale, 7_000_000)                             # paid off: it leaves the bell
    assert _about(_bell(co), sale) == []


def test_an_unpaid_overdue_invoice_reads_as_before(co):
    sale = _invoice(co, days_ago=40)
    (item,) = _about(_bell(co), sale)
    assert "still open" not in item["message"] and item["message"].startswith("Receivable from the customer — 10 day(s)")


def test_a_half_paid_bill_due_soon_shows_as_due(co):
    bill = _invoice(co, "purchase", days_ago=28)          # due in 2 days
    _pay(co, bill, bill["amount"] // 2)
    (item,) = _about(_bell(co), bill)
    assert item["kind"] == "invoice_due"


def test_whatever_is_settled_leaves_the_bell(co, db):
    credited = _invoice(co, days_ago=40)
    r = co["api"].post(f"/invoices/{credited['id']}/credit-notes",
                       json={"amount": credited["amount"], "date": date.today().isoformat()})
    assert r.status_code == 201, r.text
    # a part-paid row whose payments already cover it (an older, inconsistent status)
    covered = _invoice(co, days_ago=40)
    _pay(co, covered, covered["amount"])
    from app.models.invoice import Invoice
    with use_company(co["cid"]):
        row = db.execute(select(Invoice).where(Invoice.number == covered["number"])).scalar_one()
        row.status = "partially_paid"
        db.commit()
    feed = _bell(co)
    assert _about(feed, credited) == [] and _about(feed, covered) == []
