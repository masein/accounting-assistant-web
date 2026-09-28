"""Printing an issued cheque on its leaf (roadmap 2026-09 §3.4, part 2): the
date in figures and in words, the payee and their national id, the amount in
words and in figures, placed where the company's layout says; a guide print
for lining it up; the print recorded in the cheque's history."""
from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.tenant import use_company
from app.services import cheque_print as cp
from tests.test_cheque_lifecycle import Books, _company, _login  # noqa: F401 — shared fixtures below

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


@pytest.fixture()
def ir(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "ir", "IRR")
    yield Books(db, _login(client, cid), cid)
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "uk", "GBP")
    yield Books(db, _login(client, cid), cid)
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


# ─── The words ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("d,words", [
    (date(2026, 9, 27), "پنجم مهر ماه یک هزار و چهارصد و پنج"),            # 1405/07/05
    (date(2026, 3, 21), "یکم فروردین ماه یک هزار و چهارصد و پنج"),          # Nowruz 1405
    (date(2026, 7, 22), "سی و یکم تیر ماه یک هزار و چهارصد و پنج"),
    (date(2026, 10, 22), "سی‌ام مهر ماه یک هزار و چهارصد و پنج"),           # not the archaic «سیم»
])
def test_the_date_in_persian_words(d, words):
    assert cp.date_in_words_fa(d) == words


def test_a_long_line_shrinks_to_its_box():
    assert cp.fit_font_size("short", 140, 11) == 11
    small = cp.fit_font_size("نهصد و نود و نه میلیارد و نهصد و نود و نه میلیون و نهصد و نود و نه هزار و نهصد و نود و نه ریال", 100, 11)
    assert 7 <= small < 11
    assert cp.fit_font_size("x" * 500, 20, 11) == 7


def test_the_iranian_fields(ir):
    party = ir.party("supplier", "Pars Paper")
    ir.api.patch(f"/entities/{party['id']}", json={"national_id": "10101234567"})
    c = ir.cheque(direction="pay", amount=12_500_000, due_date="2026-09-27", entity_id=party["id"])
    assert c["counterparty"] == "Pars Paper"                     # named after its party
    with use_company(ir.cid):
        from app.models.commitment import Commitment
        import uuid
        row = ir.db.get(Commitment, uuid.UUID(c["id"]))
        v = cp.cheque_values(ir.db, row)
        assert v == {
            "date": "۱۴۰۵/۰۷/۰۵",
            "date_words": "پنجم مهر ماه یک هزار و چهارصد و پنج",
            "payee": "Pars Paper",
            "national_id": "۱۰۱۰۱۲۳۴۵۶۷",
            "amount_words": "دوازده میلیون و پانصد هزار ریال",
            "amount": "#۱۲٬۵۰۰٬۰۰۰#",
        }
        # what the dialog overrides
        v2 = cp.cheque_values(ir.db, row, payee="Someone else", national_id="0012345678", on=date(2026, 9, 28))
        assert v2["payee"] == "Someone else" and v2["national_id"] == "۰۰۱۲۳۴۵۶۷۸" and v2["date"] == "۱۴۰۵/۰۷/۰۶"


def test_the_uk_fields(uk):
    row = SimpleNamespace(amount=12_500, due_date=date(2026, 9, 27), counterparty="Acme Ltd", entity_id=None)
    with use_company(uk.cid):
        v = cp.cheque_values(uk.db, row)
    assert v == {"date": "27/09/2026", "payee": "Acme Ltd",
                 "amount_words": "Twelve thousand, five hundred pounds only", "amount": "£12,500.00"}


# ─── The page ────────────────────────────────────────────────────────────────

def test_the_page_is_the_leaf_and_fields_sit_where_the_layout_says(ir):
    with use_company(ir.cid):
        cp.save_layout(ir.db, {"offset_x": 2, "offset_y": -1.5, "fields": {"amount": {"x": 12, "y": 50, "w": 40}}})
        html = cp.render_html(ir.db, {"date": "۱۴۰۵/۰۷/۰۵", "amount": "#۱۰۰#", "payee": "Pars"})
    assert "size: 175mm 80mm" in html
    assert re.search(r'class="f f-amount" dir="ltr"\s+style="left: 14mm; top: 48\.5mm; width: 40mm', html)
    assert "Pars" in html and "۱۴۰۵/۰۷/۰۵" in html
    assert "f-date_words" not in html                     # an empty field is not printed …
    assert "outline" not in html and "راهنمای تنظیم" not in html


def test_the_guide_shows_every_box_and_its_name(ir):
    with use_company(ir.cid):
        html = cp.render_html(ir.db, {"payee": "Pars"}, guide=True)
    for name in cp.FIELDS:
        assert f"f-{name}" in html                           # … but the guide shows every box
    assert "در وجه" in html and "مبلغ به حروف" in html and "outline" in html
    assert "overflow: visible" in html                       # the names above the boxes are not clipped


def test_the_print_is_a_pdf_the_size_of_the_leaf_and_goes_in_the_history(ir):
    from pypdf import PdfReader
    c = ir.cheque(direction="pay", amount=3_000_000, counterparty="Pars Paper", reference="100442")
    r = ir.api.post(f"/commitments/{c['id']}/print", json={"national_id": "10101234567"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="cheque-100442.pdf"' in r.headers["content-disposition"]
    box = PdfReader(io.BytesIO(r.content)).pages[0].mediabox
    assert round(float(box.width) / 72 * 25.4) == 175 and round(float(box.height) / 72 * 25.4) == 80
    events = ir.api.get(f"/commitments/{c['id']}/history").json()["events"]
    assert events[-1]["action"] == "printed" and events[-1]["note"] == "Pars Paper"

    r = ir.api.post(f"/commitments/{c['id']}/print", json={"guide": True})
    assert r.status_code == 200
    assert len(ir.api.get(f"/commitments/{c['id']}/history").json()["events"]) == len(events)   # a guide isn't a print


def test_only_our_open_cheques_are_printed(ir):
    received = ir.cheque(direction="receive", amount=100)
    r = ir.api.post(f"/commitments/{received['id']}/print", json={})
    assert r.status_code == 400 and "drawer" in r.json()["detail"]
    issued = ir.cheque(direction="pay", amount=100)
    ir.step(issued, "settle", on="2026-09-20")
    assert ir.api.post(f"/commitments/{issued['id']}/print", json={}).status_code == 409


def test_a_test_print_needs_no_cheque(ir):
    r = ir.api.post("/commitments/print-test")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ─── The layout ──────────────────────────────────────────────────────────────

def test_the_layout_is_the_company_s_and_keeps_every_box_on_the_leaf(ir):
    base = ir.api.get("/commitments/print-layout").json()
    assert base["locale"] == "ir" and base["width"] == 175 and set(base["fields"]) == set(cp.FIELDS)
    saved = ir.api.put("/commitments/print-layout", json={"offset_y": 1.5, "font_size": 12,
                                                          "fields": {"payee": {"x": 60, "w": 98}}}).json()
    assert saved["offset_y"] == 1.5 and saved["font_size"] == 12 and saved["fields"]["payee"]["x"] == 60
    assert saved["fields"]["payee"]["y"] == base["fields"]["payee"]["y"]                          # untouched
    assert ir.api.get("/commitments/print-layout").json()["fields"]["payee"]["w"] == 98
    for bad in ({"fields": {"amount": {"x": 160, "w": 40}}},        # off the right edge
                {"offset_x": 50}, {"font_size": 3}, {"width": "wide"}):
        assert ir.api.put("/commitments/print-layout", json=bad).status_code == 422, bad
    reset = ir.api.post("/commitments/print-layout/reset").json()
    assert reset["offset_y"] == 0 and reset["fields"]["payee"]["x"] == cp.DEFAULTS["ir"]["fields"]["payee"]["x"]


def test_a_uk_company_gets_the_uk_leaf(uk):
    layout = uk.api.get("/commitments/print-layout").json()
    assert layout["locale"] == "uk" and "date_words" not in layout["fields"] and layout["width"] == 178


def test_viewers_see_the_layout_but_do_not_print(client, ir):
    c = ir.cheque(direction="pay", amount=100)
    viewer = _login(client, ir.cid, role="viewer")
    assert viewer.get("/commitments/print-layout").status_code == 200
    assert viewer.post(f"/commitments/{c['id']}/print", json={}).status_code == 403
    assert viewer.put("/commitments/print-layout", json={"offset_x": 1}).status_code == 403
    assert viewer.post("/commitments/print-test").status_code == 403


def test_the_page_offers_printing_for_cheques_we_issue():
    ops = (JS / "12-ops.js").read_text(encoding="utf-8")
    assert "if (!rec && ['pending', 'bounced'].includes(r.status)) steps.unshift('print');" in ops
    assert "window.open('', '_blank')" in ops                     # opened inside the click, not after the request
    i18n = (JS / "02-i18n.js").read_text(encoding="utf-8")
    for key in ("cmActPrint", "cmStepPayee", "cmStepNid", "cmStepGuide", "cmHint_print", "cmEv_printed",
                "cmPrintTitle", "cmPrintSave", "cmPrintTest", *[f"cmPf_{f}" for f in cp.FIELDS]):
        assert len(re.findall(rf"^        {key}: ", i18n, re.M)) == 4, key
