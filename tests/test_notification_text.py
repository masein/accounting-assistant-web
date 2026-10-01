"""Notifications read in the reader's language: the bell and web push showed
"Invoice INV-7 overdue", "Payroll payday …", "Budget exceeded: …" to a Persian
user. Every row now carries a text key and its values, rendered per reader
(app/services/notification_text.py); the English title/message stay for
older rows."""
from __future__ import annotations

import ast
import string
import uuid
from datetime import date, timedelta
from pathlib import Path

from app.services import notification_service
from app.services.notification_text import ENUMS, LANGS, PHRASES, TEXT, render

SERVICE = Path(notification_service.__file__)


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_text_reads_in_every_language_with_the_same_values():
    for key, text in TEXT.items():
        for part in ("title", "message"):
            assert set(text[part]) == set(LANGS), (key, part)
            assert len({frozenset(_fields(s)) for s in text[part].values()}) == 1, (key, part)
    for key, said in PHRASES.items():
        assert set(said) == set(LANGS) and len({frozenset(_fields(s)) for s in said.values()}) == 1, key
    for name, values in ENUMS.items():
        assert set(values) == set(LANGS) and len({frozenset(v) for v in values.values()}) == 1, name


def _upserts():
    return [n for n in ast.walk(ast.parse(SERVICE.read_text(encoding="utf-8")))
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_upsert"]


def _keys(node) -> set[str]:
    if isinstance(node, ast.Constant):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _keys(node.body) | _keys(node.orelse)
    return set()


def _given(node) -> set[str]:
    """The values a params={...} literal passes, spread parts included."""
    out = set()
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if k is None:                       # **({...} if … else {...})
                for part in ast.walk(v):
                    if isinstance(part, ast.Dict):
                        out |= {kk.value for kk in part.keys if isinstance(kk, ast.Constant)}
            elif isinstance(k, ast.Constant):
                out.add(k.value)
    return out


def test_every_notification_the_service_writes_is_worded_and_given_its_values():
    calls = _upserts()
    assert len(calls) >= 20
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "text_key" in kw, ast.unparse(call)[:90]
        node = kw["text_key"]
        if isinstance(node, ast.JoinedStr):     # tax_ir_{kind}_{today|in}, insight:{kind}
            prefix = node.values[0].value
            assert any(k.startswith(prefix) for k in TEXT) or prefix == "insight:", prefix
            continue
        for key in _keys(node):
            assert key in TEXT, key
            wanted = _fields(TEXT[key]["title"]["en"]) | _fields(TEXT[key]["message"]["en"])
            given = _given(kw["params"])
            optional = {"note", "amount"} if key in ("reminder", "recurring_due_amount", "ai_approval_amount") else set()
            assert wanted - optional <= given, (key, wanted - given)


def test_a_notification_renders_for_its_reader():
    params = {"number": "INV-7", "side": "receivable", "days": 12, "date": "2026-09-19"}
    assert render("invoice_overdue", params, "en") == ("Invoice INV-7 overdue",
                                                       "Receivable from the customer — 12 day(s) past due (2026-09-19)")
    title, message = render("invoice_overdue", params, "fa")
    assert title == "فاکتور ⁨INV-7⁩ سررسید گذشته"
    assert message.startswith("⁨دریافت از مشتری⁩ — ⁨12⁩ روز")
    assert render("invoice_overdue", params, "es")[0] == "Factura INV-7 vencida"
    # a phrase inside the message, an enumeration, a month, a season
    cheque = {"noun": "cheque", "seq": " 2/6", "title": "Rent", "amount": "5,000", "verb": "receive",
              "when": {"key": "commitment_bounced_receive", "params": {"date": "2026-09-01"}}}
    assert render("commitment", cheque, "en") == ("Cheque 2/6: Rent", "5,000 to receive — bounced — the customer owes it again (2026-09-01)")
    assert "برگشتی" in render("commitment", cheque, "fa")[1]
    assert render("budget_over", {"category": "Rent", "actual": "120", "limit": "100", "month": "2026-10", "spent": 120},
                  "fa")[1].startswith("⁨120⁩ از ⁨100⁩ در ⁨اکتبر ۲۰۲۶⁩")
    assert render("tax_ir_vat_in", {"season": "1405-2", "days": 5}, "en")[0] == "VAT return for Summer 1405 due in 5 day(s)"
    project = {"name": "Site", "parts": [{"key": "project_hours", "params": {"used": "90", "budget": "100", "pct": 90}},
                                         {"key": "project_amount", "params": {"used": "9,000", "budget": "10,000",
                                                                              "currency": "GBP", "pct": 90}}]}
    assert render("project_near", project, "en")[1] == "90 of 100 hours (90%) · 9,000 of 10,000 GBP (90%)"
    # a row without a key (written before this existed) shows its stored English
    assert render(None, None, "fa") is None and render("nope", {}, "fa") is None


def test_an_insight_renders_from_its_own_wording():
    from app.services.insight_service import _TEMPLATES
    kind = next(iter(_TEMPLATES))
    params = {f: "1" for part in ("title", "message") for said in _TEMPLATES[kind][part].values() for f in _fields(said)}
    assert render(f"insight:{kind}", params, "fa")[0] == _TEMPLATES[kind]["title"]["fa"].format(**params)


def test_the_bell_speaks_the_page_language(auth_client, db):
    from app.models.invoice import Invoice

    inv = Invoice(number=f"NT-{uuid.uuid4().hex[:6]}", kind="sales", status="issued",
                  issue_date=date.today() - timedelta(days=40), due_date=date.today() - timedelta(days=10), amount=1000)
    db.add(inv)
    db.commit()
    try:
        fa = auth_client.get("/notifications/feed", headers={"X-UI-Language": "fa"}).json()
        hit = next(i for i in fa if i["kind"] == "invoice_overdue" and inv.number in i["title"])
        assert hit["title"] == f"فاکتور ⁨{inv.number}⁩ سررسید گذشته"
        en = auth_client.get("/notifications/feed").json()
        assert any(i["title"] == f"Invoice {inv.number} overdue" for i in en)
    finally:
        inv.status = "paid"
        db.commit()
