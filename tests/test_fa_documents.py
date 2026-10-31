"""A Persian company's documents read in Persian: no English word where the
code used to fall back to one — «Regular hours: 160 h», «Service / Product»,
«Item», «شرایط پرداخت: bank», «نمونه — Sample» (scenarios BE1–BE7)."""
from __future__ import annotations

import html as H
import re
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.db.tenant import use_company
from app.models.company_profile import CompanyProfile
from app.services.documents import engine as E
from app.services.documents import render as R
from tests.test_document_engine import Session, _clear, _company  # noqa: F401 — fixtures and the company maker

PARTY = NS(name="بازرگانی پارس", legal_name="شرکت بازرگانی پارس", address="تهران، خیابان ولیعصر")
EMPLOYEE = NS(name="مریم احمدی", bank_name="بانک ملت", iban=None, account_number=None)


@pytest.fixture()
def render(Session, monkeypatch):
    """(company locale) → a function that renders one document to its HTML."""
    made = {}

    def capture(ctx, template_name="document.html"):
        made["html"] = E.render_html(ctx, template_name)
        return b"%PDF-stub"
    monkeypatch.setattr(R, "render_pdf", capture)

    def make(locale="ir", currency="IRR"):
        db = Session()
        company = _company(db, locale, currency, f"{locale}-docs")
        with use_company(company.id):
            db.add(CompanyProfile(legal_name="شرکت نمونهٔ آرمان" if locale == "ir" else "Acme Trading Ltd",
                                  address="تهران" if locale == "ir" else "1 High Street"))
            db.flush()

        def one(fn, *args, **kwargs):
            with use_company(company.id):
                fn(db, *args, **kwargs)
            return made["html"]
        return one
    return make


def _invoice(**kw):
    base = dict(number="1405-12", status="issued", issue_date=date(2026, 5, 1), due_date=date(2026, 5, 31),
                valid_until=date(2026, 5, 31), amount=1_000_000, currency="IRR", description=None, items=[])
    return NS(**{**base, **kw})


def _payslip_line(**kw):
    base = dict(hours=160, overtime_hours=10, leave_hours=8, gross=120_000_000, income_tax=5_000_000,
                social_security=8_400_000, pre_tax_deductions=0, net_pay=106_600_000,
                employee_name="مریم احمدی", paid_to=None)
    return NS(**{**base, **kw})


RUN = NS(currency="IRR", period_start=date(2026, 4, 21), period_end=date(2026, 5, 21), pay_date=date(2026, 5, 22),
         kind="regular")


def _documents(one):
    item = NS(product_name=None, quantity=2, unit_price=500_000, tax_rate=0, line_total=1_000_000)
    yield "invoice", one(R.render_invoice_pdf, _invoice(items=[item]), PARTY)
    yield "invoice without items", one(R.render_invoice_pdf, _invoice(), PARTY)
    yield "quote", one(R.render_quote_pdf, _invoice(status="sent", items=[item]), PARTY)
    yield "time invoice", one(R.render_time_invoice_pdf, _invoice(), PARTY,
                              [{"project": "پروژهٔ آرمان", "worker": "مریم احمدی", "hours": 10,
                                "rate": 100_000, "amount": 1_000_000}], (date(2026, 4, 21), date(2026, 5, 21)))
    for method in ("bank", "cash", "cheque", "transfer", "credit"):
        # the receipt's number is the payment id's first 8 hex characters, a code: digits only here
        payment = NS(id="31259870-0000", amount=400_000, currency="IRR", date=date(2026, 5, 10), method=method)
        yield f"receipt by {method}", one(R.render_receipt_pdf, payment, _invoice(), PARTY, 600_000)
    yield "statement", one(R.render_statement_pdf, PARTY, [
        {"date": date(2026, 5, 1), "description": "فاکتور ۱۴۰۵-۱۲", "debit": 1_000_000, "credit": 0},
        {"date": date(2026, 5, 10), "description": "دریافت", "debit": 0, "credit": 400_000}],
        (date(2026, 5, 1), date(2026, 5, 31)), "IRR")
    po = NS(number="1405-7", currency="IRR", order_date=date(2026, 5, 2), status="draft", description=None,
            lines=[NS(description=None, ordered_qty=3, unit_price=200_000, line_total=600_000)])
    yield "purchase order", one(R.render_purchase_order_pdf, po, PARTY)
    yield "payslip", one(R.render_payslip_pdf, RUN, _payslip_line(), EMPLOYEE)


def _english(page: str) -> list[str]:
    """The English words a reader sees: the page's text without its styles,
    tags, currency codes and file-format names."""
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S | re.I)
    text = H.unescape(re.sub(r"<[^>]+>", " ", text))
    return [w for w in re.findall(r"[A-Za-z]+", text) if w not in {"IRR"}]


def test_every_document_reads_in_persian(render):
    one = render("ir")
    left = {name: words for name, page in _documents(one) if (words := _english(page))}
    assert not left, f"English on a Persian company's documents: {left}"


def test_an_hourly_payslip_names_its_hours_in_persian(render):
    page = render("ir")(R.render_payslip_pdf, RUN, _payslip_line(), EMPLOYEE)
    for row in ("کارکرد عادی: ۱۶۰ ساعت", "اضافه‌کاری: ۱۰ ساعت", "مرخصی با حقوق: ۸ ساعت"):
        assert row in page, row
    assert "Regular hours" not in page and " h<" not in page


def test_items_without_a_name(render):
    one = render("ir")
    assert "کالا / خدمات" in one(R.render_invoice_pdf, _invoice(), PARTY)
    nameless = NS(product_name=None, quantity=1, unit_price=10, tax_rate=0, line_total=10)
    assert ">کالا<" in re.sub(r"\s+", "", one(R.render_invoice_pdf, _invoice(items=[nameless]), PARTY))


@pytest.mark.parametrize("method, fa, en", [("bank", "بانک", "Bank"), ("cash", "نقد", "Cash"),
                                            ("cheque", "چک", "Cheque"), ("transfer", "انتقال بانکی", "Bank transfer")])
def test_a_receipt_says_how_it_was_paid(render, method, fa, en):
    payment = NS(id="3f2a9c1e-0000", amount=400_000, currency="IRR", date=date(2026, 5, 10), method=method)
    page = render("ir")(R.render_receipt_pdf, payment, _invoice(number="INV-12"), PARTY, 0)
    assert "روش پرداخت" in page and fa in page and "شرایط پرداخت" not in page
    assert "INV-۱۲" in page                                   # the invoice's number in Persian digits (BE5)
    payment.currency = "GBP"
    uk = render("uk", "GBP")(R.render_receipt_pdf, payment, _invoice(number="INV-12", currency="GBP"), PARTY, 0)
    assert "Paid by" in uk and en in uk


def test_the_cheque_print_test_names_its_sample_in_the_cheques_language(Session):
    from app.services import cheque_print
    db = Session()
    for locale, payee in (("ir", "نمونه"), ("uk", "Sample")):
        company = _company(db, locale, "IRR" if locale == "ir" else "GBP", f"{locale}-cheque")
        with use_company(company.id):
            assert cheque_print.sample_payee(db) == payee
    db.close()
