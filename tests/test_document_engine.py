"""Branded document engine: locale-aware HTML/PDF rendering + formatting.

HTML assertions run without WeasyPrint (fast); one PDF test exercises the real
WeasyPrint path (system libs present in the Docker image).
"""
from __future__ import annotations

import importlib
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.models  # noqa: F401
from app.db.tenant import use_company, clear_current_company
from app.models.account import Account  # noqa: F401
from app.models.company import Company
from app.models.company_profile import CompanyProfile
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.documents import formatting as F


# ---------------------------------------------------------------------------
# Pure formatting (no DB)
# ---------------------------------------------------------------------------
def test_money_ltr_and_rtl():
    assert F.fmt_money(1250, "GBP", "uk") == "£1,250"
    rtl = F.fmt_money(1250, "IRR", "ir")
    assert "۱٬۲۵۰" in rtl or "۱,۲۵۰" in rtl  # Persian digits


def test_amount_in_words_en_and_fa():
    en = F.amount_in_words(1250, "GBP", "uk")
    assert "thousand" in en.lower() and "pound" in en.lower()
    fa = F.amount_in_words(1250, "IRR", "ir")
    assert "هزار" in fa  # 'thousand' in Persian
    assert "ریال" in fa


def test_dates_locale_aware():
    d = date(2025, 3, 21)
    assert F.fmt_date(d, "uk") == "2025-03-21"
    fa = F.fmt_date(d, "ir")
    assert "/" in fa and any(ch in fa for ch in "۰۱۲۳۴۵۶۷۸۹")  # Jalali + Persian digits


# ---------------------------------------------------------------------------
# DB-backed rendering
# ---------------------------------------------------------------------------
@pytest.fixture()
def Session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _pragma(conn, _rec):
        cur = conn.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()

    Base.metadata.create_all(bind=eng)
    yield sessionmaker(bind=eng, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=eng)


@pytest.fixture(autouse=True)
def _clear():
    clear_current_company()
    yield
    clear_current_company()


def _company(db, locale, ccy, slug):
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        c = Company(name="Acme", slug=slug, locale=locale, base_currency=ccy, status="active")
        db.add(c); db.flush()
    return c


def _seed_invoice(db, company, *, with_profile=True):
    with use_company(company.id):
        if with_profile:
            db.add(CompanyProfile(
                legal_name="Acme Trading Ltd", brand_color="#8e44ad",
                address="1 High Street", tax_id="GB123456",
                bank_details="HSBC · GB00 0000", invoice_footer="Thank you for your business",
            ))
        client = Entity(type="client", name="Globex", legal_name="Globex Corp",
                        address="9 Market Sq", tax_id="GB999")
        db.add(client); db.flush()
        inv = Invoice(number="INV-0001", kind="sales", status="issued",
                      issue_date=date(2025, 3, 1), due_date=date(2025, 3, 31),
                      amount=1250, currency=company.base_currency, entity_id=client.id)
        db.add(inv); db.flush()
        db.add(InvoiceItem(invoice_id=inv.id, product_name="Consulting", quantity=5,
                           unit_price=250, line_total=1250))
        db.flush()
        inv = db.get(Invoice, inv.id)
        return inv, client


def _render_html_invoice(db, company):
    """Render the invoice context to HTML (no WeasyPrint) for content assertions."""
    from app.services.documents import render as R
    from app.services.documents import engine as E
    inv, client = _seed_invoice(db, company)
    with use_company(company.id):
        # Reproduce render_invoice_pdf's context but stop at HTML.
        captured = {}
        orig = E.render_pdf

        def _capture(ctx, template_name="document.html"):
            captured["html"] = E.render_html(ctx, template_name)
            return b"%PDF-stub"
        R.render_pdf = _capture
        try:
            R.render_invoice_pdf(db, inv, client)
        finally:
            R.render_pdf = orig
    return captured["html"]


def test_uk_invoice_html_is_branded_ltr(Session):
    db = Session()
    company = _company(db, "uk", "GBP", "uk-co")
    html = _render_html_invoice(db, company)
    assert 'dir="ltr"' in html
    assert "Acme Trading Ltd" in html          # legal name from profile, NOT "Accounting Assistant"
    assert "Accounting Assistant" not in html
    assert "#8e44ad" in html                    # brand colour themed
    assert "Globex" in html                     # recipient party card
    assert "£1,250" in html                     # currency formatting
    assert "pound" in html.lower()              # amount in words
    assert "Thank you for your business" in html  # footer
    db.close()


def test_ir_invoice_html_is_rtl_persian(Session):
    db = Session()
    company = _company(db, "ir", "IRR", "ir-co")
    html = _render_html_invoice(db, company)
    assert 'dir="rtl"' in html
    assert "صورتحساب" in html                    # "invoice" in Persian
    assert "ریال" in html                        # currency word in amount-in-words
    assert any(ch in html for ch in "۰۱۲۳۴۵۶۷۸۹")  # Persian digits
    db.close()


def test_invoice_pdf_bytes_render(Session):
    """Exercises the real WeasyPrint path end to end. Skips cleanly if the
    native pango/cairo libs aren't present (e.g. a bare environment)."""
    try:
        import weasyprint  # noqa: F401
    except Exception as e:  # ImportError or OSError (missing native libs)
        pytest.skip(f"weasyprint unavailable: {e}")
    from app.services.documents import render_invoice_pdf
    db = Session()
    company = _company(db, "uk", "GBP", "pdf-co")
    inv, client = _seed_invoice(db, company)
    with use_company(company.id):
        pdf = render_invoice_pdf(db, inv, client)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 2000
    db.close()


def _pdf_fonts(pdf: bytes) -> set[str]:
    """The base font names a PDF embeds, without the subset prefix ("ABCDEF+")."""
    import io

    from pypdf import PdfReader
    out = set()
    for page in PdfReader(io.BytesIO(pdf)).pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            out.add(str(ref.get_object()["/BaseFont"]).lstrip("/").split("+")[-1])
    return out


def test_the_font_stack_reaches_the_css():
    """Autoescaping turned the stack's quotes into &#39; — which a <style>
    block doesn't decode — so WeasyPrint dropped every font-family rule and
    every document fell back to its default serif."""
    import re
    from pathlib import Path

    from app.services.documents.branding import _FONT_LTR, _FONT_RTL
    from app.services.documents.engine import render_html
    for tpl in Path("app/services/documents/templates").glob("*.html"):
        src = tpl.read_text(encoding="utf-8")
        assert not re.search(r"\{\{\s*font_family\s*\}\}", src), tpl.name          # always "| safe"
    ctx = {"rtl": True, "dir": "rtl", "lang": "fa", "brand": {"issuer": {"name": "X", "logo": None}},
           "brand_color": "#0f766e", "font_family": _FONT_RTL, "words": {"page": "صفحه"}, "cover": None,
           "extra_html": "", "amounts_in": "", "prepared": "", "tables": []}
    html = render_html(ctx, "statements.html")
    assert f"font-family: {_FONT_RTL};" in html and "&#39;" not in html[:html.index("</style>")]
    assert "'" in _FONT_LTR                                                         # the stack does quote names


def test_invoices_are_set_in_the_brand_font(Session):
    try:
        import weasyprint  # noqa: F401
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"weasyprint unavailable: {e}")
    from app.services.documents import render_invoice_pdf
    db = Session()
    for locale, ccy, slug, family in (("uk", "GBP", "font-uk", "NotoSans"), ("ir", "IRR", "font-ir", "NotoNaskhArabic")):
        company = _company(db, locale, ccy, slug)
        inv, client = _seed_invoice(db, company)
        with use_company(company.id):
            fonts = _pdf_fonts(render_invoice_pdf(db, inv, client))
        assert any(f.replace("-", "").startswith(family) for f in fonts), (locale, fonts)
    db.close()
