"""UK Making Tax Digital, part one (roadmap 2026-09 §3.6): tax years, ITSA
quarters, VAT periods and deadlines, MTD settings, and VAT return boxes 1–9
with the MTD VAT API body."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import use_company
from app.models.company import Company
from app.models.credit_note import CreditNote
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.uk_mtd import periods as P
from app.services.uk_mtd import settings as S
from app.services.uk_mtd.vat import vat_return


# --- tax years and ITSA quarters --------------------------------------------------------

@pytest.mark.parametrize("d, year", [(date(2026, 4, 5), 2025), (date(2026, 4, 6), 2026), (date(2027, 1, 1), 2026)])
def test_tax_year_of(d, year):
    assert P.tax_year_of(d) == year


def test_tax_year_labels():
    assert P.tax_year_label(2026) == "2026-27"
    assert P.parse_tax_year("2026-27") == P.parse_tax_year(2026) == 2026
    for bad in ("2026-28", "abc", "1999-00"):
        with pytest.raises(ValueError):
            P.parse_tax_year(bad)
    assert P.tax_year_bounds(2026) == (date(2026, 4, 6), date(2027, 4, 5))


def test_itsa_quarters_standard_and_calendar():
    std = [(q.start, q.end, q.deadline) for q in P.itsa_quarters(2026)]
    assert std == [
        (date(2026, 4, 6), date(2026, 7, 5), date(2026, 8, 7)),
        (date(2026, 7, 6), date(2026, 10, 5), date(2026, 11, 7)),
        (date(2026, 10, 6), date(2027, 1, 5), date(2027, 2, 7)),
        (date(2027, 1, 6), date(2027, 4, 5), date(2027, 5, 7)),
    ]
    cal = [(q.start, q.end, q.deadline) for q in P.itsa_quarters(2026, "calendar")]
    assert cal[0] == (date(2026, 4, 1), date(2026, 6, 30), date(2026, 8, 7))
    assert cal[3] == (date(2027, 1, 1), date(2027, 3, 31), date(2027, 5, 7))
    assert P.itsa_quarters(2026)[2].cumulative_start == date(2026, 4, 6)
    assert P.itsa_quarter_of(date(2026, 10, 5)).quarter == 2
    assert P.itsa_quarter_of(date(2026, 10, 5), "calendar").quarter == 3
    with pytest.raises(ValueError):
        P.itsa_quarters(2026, "weekly")


# --- VAT periods -------------------------------------------------------------------------------

@pytest.mark.parametrize("end, stagger, start, deadline", [
    (date(2026, 3, 31), 1, date(2026, 1, 1), date(2026, 5, 7)),
    (date(2026, 6, 30), 1, date(2026, 4, 1), date(2026, 8, 7)),
    (date(2026, 1, 31), 2, date(2025, 11, 1), date(2026, 3, 7)),
    (date(2026, 2, 28), 3, date(2025, 12, 1), date(2026, 4, 7)),   # the 7th of the second month
    (date(2026, 11, 30), "monthly", date(2026, 11, 1), date(2027, 1, 7)),
])
def test_vat_periods_and_deadlines(end, stagger, start, deadline):
    p = P.vat_period_ending(end, stagger)
    assert (p.start, p.end, p.deadline) == (start, end, deadline)


def test_vat_period_validation_and_listing():
    for end, stagger in ((date(2026, 3, 30), 1), (date(2026, 4, 30), 1), (date(2026, 3, 31), 7)):
        with pytest.raises(ValueError):
            P.vat_period_ending(end, stagger)
    listed = P.vat_periods_before(date(2026, 9, 26), 1, count=3)
    assert [p.end for p in listed] == [date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31)]
    assert len(P.vat_periods_before(date(2026, 9, 26), "monthly", count=4)) == 4


# --- settings ---------------------------------------------------------------------------------

@pytest.mark.parametrize("vrn, ok", [("GB999999973", True), ("999 9999 73", True), ("999999972", False),
                                     ("12345", False), ("", False)])
def test_vrn_checksum(vrn, ok):
    assert S.valid_vrn(vrn) is ok


# --- a UK company with invoices ------------------------------------------------------------------

@pytest.fixture()
def uk(db, client):
    c = Company(id=uuid.uuid4(), name="MTD Ltd", slug=f"mtd-{uuid.uuid4().hex[:8]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(c.id), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"company": c, "login": login}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _inv(db, uk, kind, lines, *, day=date(2026, 5, 10), status="issued", currency="GBP"):
    with use_company(uk["company"].id):
        inv = Invoice(id=uuid.uuid4(), number=f"U-{uuid.uuid4().hex[:6]}", kind=kind, status=status,
                      issue_date=day, due_date=day, amount=0, currency=currency)
        items = []
        for total, rate, treatment in lines:
            items.append(InvoiceItem(product_name="x", quantity=1, unit_price=total, line_total=total, tax_rate=rate,
                                     taxable=treatment == "standard" and rate > 0, tax_treatment=treatment))
        inv.items = items
        inv.amount = sum(t for t, _r, _tr in lines) + sum(t * r // 100 for t, r, tr in lines if tr == "standard")
        db.add(inv)
        db.commit()
    return inv


def _ret(db, uk, end=date(2026, 6, 30)):
    with use_company(uk["company"].id):
        return vat_return(db, P.vat_period_ending(end, 1), currency="GBP")


def test_the_nine_boxes(db, uk):
    sale = _inv(db, uk, "sales", [(1000, 20, "standard"), (500, 0, "zero_rated"), (300, 0, "exempt")])
    _inv(db, uk, "purchase", [(400, 20, "standard")])
    _inv(db, uk, "purchase", [(1000, 20, "reverse_charge")])        # e.g. services from abroad
    _inv(db, uk, "sales", [(9999, 20, "standard")], status="draft")  # not recognised
    _inv(db, uk, "sales", [(7777, 20, "standard")], day=date(2026, 7, 1))  # next period
    _inv(db, uk, "sales", [(50, 20, "standard")], currency="EUR")    # other currency: listed only
    with use_company(uk["company"].id):
        db.add(CreditNote(invoice_id=sale.id, kind="sales", date=date(2026, 6, 1), amount=180, currency="GBP",
                          note_type="reduction"))
        db.commit()
    r = _ret(db, uk)
    # the credit is 180 of a 2,000 gross invoice carrying 200 VAT → 18 VAT back
    b = r["boxes"]
    assert b["1"] == 200 + 200 - 18                  # sales VAT + reverse-charge VAT − credit
    assert b["2"] == 0 and b["3"] == b["1"]
    assert b["4"] == 80 + 200
    assert b["5"] == abs(b["3"] - b["4"]) == 102
    assert b["6"] == 1000 + 500 + 300 - (180 - 18)
    assert b["7"] == 400 + 1000
    assert b["8"] == b["9"] == 0
    assert r["direction"] == "payable"
    assert r["counts"] == {"sales": 1, "purchases": 2, "credit_notes": 1}
    assert [o["currency"] for o in r["other_currency"]] == ["EUR"]
    body = r["hmrc_body"]
    assert body["vatDueSales"] == 382.0 and body["netVatDue"] == 102.0
    assert body["totalValueSalesExVAT"] == 1638 and isinstance(body["totalValuePurchasesExVAT"], int)
    assert set(body) == {"periodKey", "vatDueSales", "vatDueAcquisitions", "totalVatDue", "vatReclaimedCurrPeriod",
                         "netVatDue", "totalValueSalesExVAT", "totalValuePurchasesExVAT",
                         "totalValueGoodsSuppliedExVAT", "totalAcquisitionsExVAT", "finalised"}


def test_a_repayment_period(db, uk):
    _inv(db, uk, "purchase", [(5000, 20, "standard")])
    r = _ret(db, uk)
    assert r["boxes"]["5"] == 1000 and r["direction"] == "repayable"


def test_settings_roundtrip_and_validation(db, uk):
    owner = uk["login"]("owner")
    assert owner.get("/tax/uk/settings").json()["vat_registered"] is False
    r = owner.put("/tax/uk/settings", json={"vat_registered": True, "vrn": "GB 999 9999 73", "vat_stagger": "2",
                                            "income_source": "self_employment"})
    assert r.status_code == 200, r.text
    assert r.json()["vrn"] == "999999973" and r.json()["vat_stagger"] == "2"
    for bad in ({"vrn": "123"}, {"vat_stagger": "5"}, {"income_source": "company"}, {"period_basis": "weekly"}):
        assert owner.put("/tax/uk/settings", json=bad).status_code == 422, bad
    acc = uk["login"]("accountant")
    assert acc.get("/tax/uk/settings").status_code == 200
    assert acc.put("/tax/uk/settings", json={"vat_registered": False}).status_code == 403


def test_vat_routes_and_csv(db, uk):
    _inv(db, uk, "sales", [(1000, 20, "standard")])
    owner = uk["login"]("owner")
    per = owner.get("/tax/uk/vat/periods").json()
    assert per["stagger"] == "1" and per["periods"][0]["end"].endswith(("-31", "-30"))
    r = owner.get("/tax/uk/vat/return?period_end=2026-06-30").json()
    assert r["boxes"]["1"] == 200 and r["period"]["deadline"] == "2026-08-07"
    assert owner.get("/tax/uk/vat/return?period_end=2026-06-29").status_code == 422
    assert owner.get("/tax/uk/vat/return?period_end=2026-05-31").status_code == 422   # not a stagger-1 quarter end
    c = owner.get("/tax/uk/vat/return/export?period_end=2026-06-30")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(c.text)))
    boxes = {row[0]: row[2] for row in rows if row and row[0].isdigit()}
    assert boxes["1"] == "200.00" and boxes["6"] == "1000" and len(boxes) == 9
    emp = uk["login"]("employee")
    assert emp.get("/tax/uk/vat/return?period_end=2026-06-30").status_code == 403


# --- reminder ----------------------------------------------------------------------------------------

def _refresh(db, uk, today):
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    with use_company(uk["company"].id):
        refresh_notifications(db, today=today)
        db.commit()
        return {n.dedupe_key: n for n in db.execute(select(Notification).where(
            Notification.kind == "tax_filing", Notification.dismissed_at.is_(None))).scalars()}


def test_vat_return_reminder(db, uk):
    deadline = P.vat_period_ending(date(2026, 6, 30), 1).deadline      # 7 Aug 2026
    assert _refresh(db, uk, deadline - timedelta(days=5)) == {}       # not VAT registered yet
    with use_company(uk["company"].id):
        S.save_settings(db, vat_registered=True)
        db.commit()
    notes = _refresh(db, uk, deadline - timedelta(days=5))
    assert set(notes) == {"uk-vat-2026-06-30"} and notes["uk-vat-2026-06-30"].level == "warning"
    assert _refresh(db, uk, deadline - timedelta(days=1))["uk-vat-2026-06-30"].level == "high"
    assert _refresh(db, uk, deadline + timedelta(days=1)) == {}
    uk["company"].locale = "ir"
    db.commit()
    assert _refresh(db, uk, deadline - timedelta(days=3)) == {}
