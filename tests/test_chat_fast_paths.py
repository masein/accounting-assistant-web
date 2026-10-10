"""Common questions answered from the books without the model (scenarios
N10, N11, roadmap ROADMAP_ANDROID_CHAT P0.6)."""
from __future__ import annotations

import pytest

from app.services.ai_accountant.fast_paths import match
from tests.test_ai_guardrails import D, _login, co  # noqa: F401  (fixture)


@pytest.mark.parametrize("message, intent", [
    ("موجودی چقدره؟", "cash"),
    ("how much cash do we have?", "cash"),
    ("What's in the bank?", "cash"),
    ("نقدینگی", "cash"),
    ("who owes us?", "receivables"),
    ("کی بهم بدهکاره؟", "receivables"),
    ("فاکتورهای پرداخت‌نشده", "receivables"),
    ("what do we owe?", "payables"),
    ("به کی بدهکاریم؟", "payables"),
    ("how much did we spend this month?", "spending"),
    ("این ماه چقدر خرج کردم؟", "spending"),
    ("بودجه چقدر مونده؟", "budget"),
    ("how's my budget looking?", "budget"),
    ("¿Cuánto dinero tenemos?", "cash"),
    ("¿Quién nos debe?", "receivables"),
    ("¿Qué debemos?", "payables"),
    ("¿Cuánto gastamos este mes?", "spending"),
    ("كم لدينا من المال؟", "cash"),
    ("من يدين لنا؟", "receivables"),
    ("ماذا علينا؟", "payables"),
    ("كم أنفقنا هذا الشهر؟", "spending"),
])
def test_n10_these_questions_are_answered_from_the_books(message, intent):
    assert match(message) == intent


@pytest.mark.parametrize("message", [
    "record 500 cash for lunch",                          # an amount: recording
    "۲۵۰ هزار تومن ناهار از موجودی کارت",                  # Persian digits too
    "how much cash will we have next month?",            # the future: the forecast
    "پیش‌بینی نقدینگی ماه بعد",
    "I moved some money between the bank accounts yesterday and want to check that the bank balance is right",
    "invoice Aria for consulting",
    "bank balance, and record the rent",                  # two requests
    "موجودی رو ببین و اجاره رو ثبت کن",
    "registra el alquiler y dime cuánto dinero tenemos",
    "¿cuánto dinero tendremos el mes que viene?",
    "سجّل الإيجار من الرصيد النقدي",
    "",
])
def test_n11_nothing_else_is_taken_from_the_model(message):
    assert match(message) is None


@pytest.fixture()
def no_model(monkeypatch):
    """The model must not be asked."""
    from app.api import ai_accountant as api_mod

    async def refuse(*a, **kw):
        raise AssertionError("the model was called for a question the books answer")
    monkeypatch.setattr(api_mod, "run_chat_turn", refuse)


def _seed(web):
    for body in (
        {"date": D, "description": "Capital", "lines": [{"account_code": "1110", "debit": 50_000_000, "credit": 0},
                                                         {"account_code": "3110", "debit": 0, "credit": 50_000_000}]},
        {"date": D, "description": "Taxi", "lines": [{"account_code": "6130", "debit": 400_000, "credit": 0},
                                                      {"account_code": "1110", "debit": 0, "credit": 400_000}]},
    ):
        r = web.post("/transactions", json=body)
        assert r.status_code in (200, 201), r.text


def test_n10_cash_comes_back_as_a_block_with_no_model(client, db, co, no_model):
    web = _login(client, co["cid"], co["owner"])
    _seed(web)
    r = web.post("/ai-accountant/chat", json={"message": "how much cash do we have?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Cash and bank today: 49,600,000 IRR."
    assert [c["name"] for c in body["tool_calls"]] == ["get_cash_position"]
    # the phone draws it as a figure, with each account
    r = web.post("/api/mobile/v1/chat", json={"message": "how much cash do we have?", "thread_id": body["session_id"]})
    assert r.status_code == 200, r.text
    figure, words = r.json()["blocks"]
    assert figure["type"] == "figure" and figure["value"] == 49_600_000 and figure["label"] == "Cash and bank"
    assert any(row["value"] == 49_600_000 for row in figure["breakdown"])
    assert words["text"] == "Cash and bank today: 49,600,000 IRR."
    # both turns are in the thread
    msgs = web.get(f"/api/mobile/v1/threads/{body['session_id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


def test_n10_in_persian_with_persian_digits(client, db, co, no_model):
    web = _login(client, co["cid"], co["owner"])
    _seed(web)
    assert web.patch("/auth/preferences", json={"language": "fa"}).status_code == 200
    r = web.post("/ai-accountant/chat", json={"message": "موجودی چقدره؟"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "موجودی نقد و بانک امروز: ۴۹٬۶۰۰٬۰۰۰ ریال."
    r = web.post("/ai-accountant/chat", json={"message": "این ماه چقدر خرج کردم؟"})
    assert r.json()["text"].startswith("خرج این ماه تا امروز:")


def test_n10_open_invoices_per_currency(client, db, co, no_model):
    web = _login(client, co["cid"], co["owner"])
    cust = web.post("/entities", json={"type": "client", "name": "Aria"}).json()
    for n, amt in (("S-1", 3_000_000), ("S-2", 2_000_000)):
        r = web.post("/invoices", json={"number": n, "kind": "sales", "status": "issued", "amount": amt,
                                        "issue_date": D, "due_date": D, "entity_id": cust["id"]})
        assert r.status_code == 201, r.text
    r = web.post("/ai-accountant/chat", json={"message": "who owes us?"})
    assert r.json()["text"] == "2 sales invoices are still open: 5,000,000 IRR to collect."
    r = web.post("/ai-accountant/chat", json={"message": "what do we owe?"})
    assert r.json()["text"] == "No supplier bill is waiting to be paid."


def test_n11_a_recording_still_goes_to_the_model(client, db, co, monkeypatch):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import ChatResult
    seen = []

    async def fake(db, **kw):
        seen.append(kw["user_message"])
        return ChatResult(session_id=kw.get("session_id") or "00000000-0000-0000-0000-000000000001", text="ok",
                          proposals=[], tool_calls=[], stop_reason="end_turn", turns=1)
    monkeypatch.setattr(api_mod, "run_chat_turn", fake)
    web = _login(client, co["cid"], co["owner"])
    web.post("/ai-accountant/chat", json={"message": "record 500 cash for lunch"})
    assert seen == ["record 500 cash for lunch"]
