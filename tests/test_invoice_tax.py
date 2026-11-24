"""VAT / sales-tax on invoices: per-line tax math, mixed taxable/exempt, the
sales & purchase posting splits (UK + Iran), tax-summary netting, and the
always-present estimate caveat.

Calls endpoint functions directly with an isolated in-memory chart per locale.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import _to_read, create_invoice
from app.api.reports import get_tax_summary
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.transaction import TransactionLine
from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate
from app.services.locale_service import set_reporting_locale
from app.services.tax_service import TAX_CAVEAT, compute_tax_summary


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _bal(db: Session, code: str) -> int:
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one()
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    return int(dr) - int(cr)


def _balanced(db: Session) -> bool:
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0))).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0))).scalar() or 0
    return int(dr) == int(cr)


def _issue(db, *, kind="sales", currency="GBP", items, number=None, days_due=30):
    today = date.today()
    return create_invoice(InvoiceCreate(
        number=number or f"INV-{uuid.uuid4().hex[:6]}", kind=kind,
        issue_date=today, due_date=today + timedelta(days=days_due),
        amount=0, currency=currency, status="issued",
        items=[InvoiceItemCreate(**it) for it in items],
    ), db)


class TestTaxMath:
    def test_per_line_tax_and_grand_total(self, uk):
        out = _issue(uk, items=[{"product_name": "A", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        assert out.subtotal == 1000
        assert out.tax_total == 200
        assert out.grand_total == 1700 - 500  # 1200
        assert out.amount == 1200  # amount tracks grand_total

    def test_mixed_taxable_and_exempt(self, uk):
        out = _issue(uk, items=[
            {"product_name": "Taxable", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20, "taxable": True},
            {"product_name": "Exempt", "quantity": 1, "unit_price": 500, "line_total": 500, "tax_rate": 20, "taxable": False},
        ])
        assert out.subtotal == 1500       # both lines
        assert out.tax_total == 200       # only the taxable line
        assert out.grand_total == 1700
        assert out.amount == 1700

    def test_zero_rate_no_tax(self, uk):
        out = _issue(uk, items=[{"product_name": "A", "quantity": 1, "unit_price": 800, "line_total": 800, "tax_rate": 0}])
        assert out.tax_total == 0 and out.grand_total == 800


class TestRialHalfUp:
    """Rial VAT rounds half-up at .5 (5 rials at 10% is 1, not Python round()'s 0) — issue #2."""

    @pytest.mark.parametrize("price,vat", [(5, 1), (15, 2), (25, 3), (35, 4), (45, 5), (44, 4), (46, 5)])
    def test_irr_invoice_line_vat_half_up(self, ir, price, vat):
        out = _issue(ir, currency="IRR", items=[{"product_name": "A", "quantity": 1, "unit_price": price, "tax_rate": 10}])
        assert out.tax_total == vat
        assert out.grand_total == out.amount == price + vat

    def test_line_tax_helpers_half_up_without_places(self):
        from app.api.invoices import _line_tax as invoice_line_tax
        from app.services.tax_service import _line_tax, _notional_tax
        for cur in ("IRR", None):
            assert invoice_line_tax(5, 10, True, cur) == 1
            assert invoice_line_tax(25, 10, True, cur) == 3
            assert _line_tax(45, 10, True, cur) == 5
            assert _notional_tax(25, 10, cur) == 3
            assert _line_tax(1_000_000, 9, True, cur) == 90_000
            assert isinstance(_line_tax(25, 10, True, cur), int)

    def test_shared_helper(self):
        from app.core.money import issued_half_even, tax_on
        assert [tax_on(p, 10, "IRR") for p in (5, 25, 45)] == [1, 3, 5]
        assert [tax_on(p, 10, "IRR", half_even=True) for p in (5, 25, 45)] == [0, 2, 4]   # the rule before
        assert tax_on(50, 9, None) == 5 and tax_on(Decimal("19.99"), 20, "GBP") == Decimal("4.00")
        assert tax_on(Decimal("0.125"), 20, "OMR") == Decimal("0.025")
        assert issued_half_even(27, [(25, 10, True)], "IRR")            # amount made with round()
        assert not issued_half_even(28, [(25, 10, True)], "IRR")        # made half-up
        assert not issued_half_even(33, [(30, 10, True)], "IRR")        # no .5: the same either way
        assert not issued_half_even(27, [(25, 10, False), (2, 0, True)], "IRR")
        assert not issued_half_even(Decimal("27.5"), [(25, 10, True)], "GBP")

    def test_invoice_issued_with_half_even_vat_still_reads_as_issued(self, ir):
        """An IRR invoice issued before the fix: its amount (and entries) were made with
        round()'s VAT — 25 at 10% gave 2, 50 at 9% gave 4 — so it keeps that VAT."""
        from app.services import tax_ir
        from app.services.moadian.builder import build
        out = _issue(ir, currency="IRR", items=[
            {"product_name": "A", "quantity": 1, "unit_price": 25, "tax_rate": 10},
            {"product_name": "B", "quantity": 1, "unit_price": 50, "tax_rate": 9},
            {"product_name": "C", "quantity": 1, "unit_price": 10, "tax_rate": 10, "taxable": False},
        ])
        assert out.tax_total == 3 + 5 and out.amount == 85 + 8                      # half-up today
        row = ir.get(Invoice, out.id)
        row.amount = 85 + 2 + 4                                                     # as issued before
        ir.commit()
        got = _to_read(row)
        assert got.subtotal == 85 and got.tax_total == 6
        assert got.grand_total == got.amount == got.balance_due == 91
        today = date.today()
        data = compute_tax_summary(ir, today - timedelta(days=1), today + timedelta(days=1), "IRR")
        assert data["output_tax"] == 6 and data["by_rate"]["10"]["output"] == 2
        assert tax_ir.invoice_split(row)["vat"] == 6
        packet = build(ir, row)
        assert [x["vam"] for x in packet.packet["body"]] == [2, 4, 0]
        assert not [p for p in packet.problems if "add up" in p]

    def test_vat_share_half_up_in_rials(self, ir):
        """A 6-rial credit on an invoice of VAT 10 in 120 carries 0.5 of VAT: 1, not round()'s 0."""
        from app.api.invoices import _vat_share
        out = _issue(ir, currency="IRR", items=[
            {"product_name": "A", "quantity": 1, "unit_price": 100, "tax_rate": 10},
            {"product_name": "B", "quantity": 1, "unit_price": 10, "tax_rate": 10, "taxable": False},
        ])
        assert out.tax_total == 10 and out.grand_total == 120
        row = ir.get(Invoice, out.id)
        assert _vat_share(row, 6) == 1
        assert _vat_share(row, 18) == 2 and _vat_share(row, 120) == 10


class TestSalesPostingSplit:
    def test_uk_sales_split(self, uk):
        _issue(uk, kind="sales", items=[
            {"product_name": "Taxable", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20, "taxable": True},
            {"product_name": "Exempt", "quantity": 1, "unit_price": 500, "line_total": 500, "taxable": False},
        ])
        assert _bal(uk, "1100") == 1700    # debtors = grand total
        assert _bal(uk, "4000") == -1500   # revenue = subtotal
        assert _bal(uk, "2200") == -200    # output VAT payable = tax
        assert _balanced(uk)

    def test_iran_sales_split(self, ir):
        _issue(ir, kind="sales", currency="IRR", items=[
            {"product_name": "A", "quantity": 1, "unit_price": 1_000_000, "line_total": 1_000_000, "tax_rate": 10},
        ])
        assert _bal(ir, "1112") == 1_100_000   # AR
        assert _bal(ir, "4110") == -1_000_000  # revenue
        assert _bal(ir, "2130") == -100_000    # output VAT
        assert _balanced(ir)


class TestPurchasePostingSplit:
    def test_uk_purchase_split(self, uk):
        _issue(uk, kind="purchase", items=[
            {"product_name": "Goods", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20},
        ])
        assert _bal(uk, "5000") == 1000    # expense = subtotal
        assert _bal(uk, "1400") == 200     # input VAT recoverable = tax (asset DR)
        assert _bal(uk, "2100") == -1200   # creditors = grand total
        assert _balanced(uk)

    def test_iran_purchase_split(self, ir):
        _issue(ir, kind="purchase", currency="IRR", items=[
            {"product_name": "Goods", "quantity": 1, "unit_price": 2_000_000, "line_total": 2_000_000, "tax_rate": 10},
        ])
        assert _bal(ir, "6112") == 2_000_000
        assert _bal(ir, "1130") == 200_000     # input VAT
        assert _bal(ir, "2110") == -2_200_000  # AP
        assert _balanced(ir)


class TestNoTaxPostingUnchanged:
    def test_no_vat_line_when_zero(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "A", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 0}])
        assert _bal(uk, "1100") == 1000 and _bal(uk, "4000") == -1000
        assert _bal(uk, "2200") == 0  # no VAT line
        assert _balanced(uk)


class TestTaxSummary:
    def test_output_input_net(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "S", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        _issue(uk, kind="purchase", items=[{"product_name": "P", "quantity": 1, "unit_price": 500, "line_total": 500, "tax_rate": 20}])
        today = date.today()
        data = compute_tax_summary(uk, today - timedelta(days=1), today + timedelta(days=1))
        assert data["output_tax"] == 200
        assert data["input_tax"] == 100
        assert data["net_tax"] == 100
        assert data["rates"] == [20]
        assert data["caveat"] == TAX_CAVEAT

    def test_endpoint_defaults_to_quarter_and_has_caveat(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "S", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        data = get_tax_summary(None, None, None, uk)
        assert "caveat" in data and data["caveat"] == TAX_CAVEAT
        assert "assumptions" in data
        # Default period is the current quarter, which contains today's issue.
        assert data["output_tax"] == 200

    def test_caveat_present_even_with_no_tax(self, uk):
        data = compute_tax_summary(uk, date.today() - timedelta(days=1), date.today())
        assert data["caveat"] == TAX_CAVEAT
        assert data["net_tax"] == 0
        assert "No tax rates" in data["assumptions"]


class TestDefaultPeriodIsJalaliSeasonForIran:
    """Bug #4: 'this quarter' VAT for an Iranian company started on the
    Gregorian quarter (1 October) and left out Mehr 1–9 sales. The default
    period is the Jalali season (Mehr 1 1405 = 2026-09-23), as the VAT return
    (/tax/ir/vat-return) files it; other companies keep the calendar quarter."""

    TODAY = date(2026, 11, 24)  # 3 Azar 1405 — season 3 (Mehr–Azar)

    class _Today(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 24)

    def _seed(self, ir):
        for number, day, price in (("QA-4", date(2026, 9, 25), 1_000_000),   # 3 Mehr 1405
                                   ("QA-1", date(2026, 10, 5), 1_250_000),
                                   ("QA-2", date(2026, 11, 10), 1_000_000)):
            create_invoice(InvoiceCreate(
                number=number, kind="sales", issue_date=day, due_date=day + timedelta(days=30),
                amount=0, currency="IRR", status="issued",
                items=[InvoiceItemCreate(product_name="S", quantity=1, unit_price=price,
                                         line_total=price, tax_rate=10)],
            ), ir)

    def test_default_start_follows_company(self, ir, uk):
        from app.services.tax_service import default_period_start
        assert default_period_start(ir, self.TODAY) == date(2026, 9, 23)
        assert default_period_start(ir, date(2026, 3, 25)) == date(2026, 3, 21)  # 5 Farvardin 1405
        assert default_period_start(uk, self.TODAY) == date(2026, 10, 1)

    def test_endpoint_default_is_the_jalali_season(self, ir, monkeypatch):
        import app.api.reports as reports
        self._seed(ir)
        monkeypatch.setattr(reports, "date", self._Today)
        data = get_tax_summary(None, None, None, ir)
        assert data["from_date"] == "2026-09-23"
        assert data["output_tax"] == 325_000

    def test_ai_tool_default_is_the_jalali_season(self, ir, monkeypatch):
        import asyncio
        import app.services.ai_accountant.read_tools as read_tools
        from app.services.ai_accountant.base import ToolContext
        self._seed(ir)
        monkeypatch.setattr(read_tools, "date", self._Today)
        tool = read_tools.GetTaxSummary()
        data = asyncio.run(tool.run(ToolContext(db=ir, user_id="u", username="t"),
                                    read_tools.GetTaxSummaryInput()))
        assert data["from_date"] == "2026-09-23"
        assert data["output_tax"] == 325_000


def test_system_prompt_maps_this_quarter_to_the_jalali_season():
    from app.services.ai_accountant import orchestrator
    prompt = orchestrator.SYSTEM_PROMPT
    assert "Jalali SEASON" in prompt and "هذا الفصل" in prompt and "این فصل" in prompt
