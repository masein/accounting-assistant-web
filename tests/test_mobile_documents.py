"""An invoice comes back to the phone as a file (scenario N22, roadmap
ROADMAP_ANDROID_CHAT P0.7, P2.4)."""
from __future__ import annotations

from functools import partial

from tests.test_ai_guardrails import _calls, _FakeClient, _login, _text, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _script(monkeypatch, *responses):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import run_chat_turn
    monkeypatch.setattr(api_mod, "run_chat_turn", partial(run_chat_turn, client=_FakeClient(list(responses))))


def test_n22_a_confirmed_invoice_carries_its_pdf_and_asking_for_one_answers_with_it(client, co, phone, monkeypatch):
    web = _login(client, co["cid"], co["owner"])
    assert web.post("/entities", json={"type": "client", "name": "Aria Co"}).status_code in (200, 201)
    client.cookies.clear()
    _script(monkeypatch, _calls(("propose_create_invoice", {
        "party": "Aria Co", "number": "INV-1042", "lines": [{"description": "Consulting", "quantity": 3, "unit_price": 20_000_000}]})),
        _text("Drafted the invoice."))
    blocks = client.post(f"{API}/chat", headers=phone, json={"message": "invoice Aria 3 h consulting"}).json()["blocks"]
    token = next(b["token"] for b in blocks if b["type"] == "proposal")
    posted = client.post(f"{API}/proposals/{token}/confirm", headers=phone).json()
    assert posted["state"] == "posted", posted
    doc = posted["block"]["file"]
    assert doc["name"] == "invoice-INV-1042.pdf" and doc["mime"] == "application/pdf"
    r = client.get(doc["path"], headers=phone)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF"
    # asking for the invoice answers with the same file
    _script(monkeypatch, _calls(("get_invoice", {"invoice": "INV-1042"})), _text("Here it is."))
    blocks = client.post(f"{API}/chat", headers=phone, json={"message": "show invoice INV-1042"}).json()["blocks"]
    assert [b["type"] for b in blocks] == ["file", "text"] and blocks[0]["path"] == doc["path"]


def test_n22_a_posting_that_makes_no_document_carries_none(client, co, phone, monkeypatch):
    from tests.test_ai_guardrails import _txn
    _script(monkeypatch, _calls(_txn(5_000_000)), _text("Drafted."))
    token = client.post(f"{API}/chat", headers=phone, json={"message": "record the rent"}).json()["blocks"][0]["token"]
    assert client.post(f"{API}/proposals/{token}/confirm", headers=phone).json()["block"]["file"] is None


def test_n22_someone_elses_invoice_is_not_found(client, co, phone):
    import uuid
    assert client.get(f"{API}/documents/invoices/{uuid.uuid4()}", headers=phone).status_code == 404
