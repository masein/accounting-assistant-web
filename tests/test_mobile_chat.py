"""The phone's chat (scenarios N6–N9, roadmap ROADMAP_ANDROID_CHAT P0.3,
P0.4): replies as typed blocks built from the tools' results, vouchers
confirmed, cancelled and undone from the phone, threads that redraw their
cards, and a web Cancel that reaches the server."""
from __future__ import annotations

import uuid
from datetime import date
from functools import partial

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant import guardrails
from app.services.ai_accountant.blocks import display_date
from tests.test_ai_guardrails import D, _calls, _FakeClient, _login, _text, _txn, co  # noqa: F401  (fixture)

API = "/api/mobile/v1"
PASSWORD = "x" * 12


@pytest.fixture()
def phone(client, co):
    """The owner signed in on a phone: (client, bearer headers)."""
    client.cookies.clear()
    r = client.post(f"{API}/auth/login", json={"username": co["owner"].username, "password": PASSWORD,
                                               "device_name": "Pixel"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def scripted(monkeypatch):
    """Run the real orchestrator and tools with a scripted model."""
    from app.api import ai_accountant as api_mod
    real = api_mod.run_chat_turn

    def script(*responses):
        monkeypatch.setattr(api_mod, "run_chat_turn", partial(real, client=_FakeClient(list(responses))))
    return script


def _proposal(db, co, token):
    with use_company(co["cid"]):
        db.expire_all()
        return db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().first()


def _draft(client, phone, scripted, amount=80_000_000, desc="Office rent", thread=None):
    scripted(_calls(_txn(amount, desc=desc)), _text("Drafted it."))
    r = client.post(f"{API}/chat", headers=phone, json={"message": f"record {desc}", "thread_id": thread})
    assert r.status_code == 200, r.text
    return r.json()


# --- N6: blocks ------------------------------------------------------------------------------

def test_n6_a_reply_is_cards_first_then_the_words(client, db, co, phone, scripted):
    scripted(_calls(("get_account_balance", {"account_code": "1110"})),
             _calls(_txn(80_000_000)), _text("Here is the balance, and the rent to confirm."))
    r = client.post(f"{API}/chat", headers=phone, json={"message": "bank balance, and record the rent"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [b["type"] for b in body["blocks"]] == ["figure", "proposal", "text"]
    figure, voucher, text = body["blocks"]
    assert figure["tool"] == "get_account_balance" and figure["value"] == 0 and "1110" in figure["label"]
    assert voucher["amount"] == {"value": 80_000_000, "currency": "IRR"} and voucher["title"] == "Office rent"
    # no date in the message: the tool anchors it to today, shown in the company's calendar
    today = date.today().isoformat()
    assert voucher["date"] == {"iso": today, "display": display_date(today, "jalali", "en")}
    assert [(ln["account"], ln["debit"], ln["credit"]) for ln in voucher["lines"]] == [
        ("6112", 80_000_000, 0), ("1110", 0, 80_000_000)]
    assert all(ln["name"] and ln["name"] != ln["account"] for ln in voucher["lines"])   # names from the chart
    assert voucher["actions"] == ["confirm", "cancel"] and voucher["fallback_text"]
    assert text["text"] == "Here is the balance, and the rent to confirm."
    # the blocks are kept on the thread's message
    msgs = client.get(f"{API}/threads/{body['thread_id']}/messages", headers=phone).json()
    assert msgs[-1]["blocks"][1]["token"] == voucher["token"]


# --- N7: confirm, cancel, undo --------------------------------------------------------------

def test_n7_confirm_twice_then_undo(client, db, co, phone, scripted):
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    r = client.post(f"{API}/proposals/{token}/confirm", headers=phone)
    assert r.status_code == 200, r.text
    posted = r.json()
    assert posted["state"] == "posted" and posted["block"]["undo_seconds"] == 120
    assert "voucher" in posted["block"] and posted["block"]["date"]["iso"] == date.today().isoformat()
    assert not (posted["block"]["voucher"] or "").startswith(posted["block"]["transaction_id"][:8])   # never an id
    again = client.post(f"{API}/proposals/{token}/confirm", headers=phone).json()
    assert again["state"] == "posted" and again["block"]["undo_seconds"] == 0
    assert again["block"]["audit_log_id"] == posted["block"]["audit_log_id"]       # the same posting
    r = client.post(f"{API}/postings/{posted['block']['audit_log_id']}/undo", headers=phone)
    assert r.status_code == 200 and r.json()["state"] == "undone"


def test_n7_a_cancelled_card_cannot_be_confirmed(client, db, co, phone, scripted):
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    assert client.post(f"{API}/proposals/{token}/cancel", headers=phone).json() == {"state": "cancelled"}
    assert client.post(f"{API}/proposals/{token}/cancel", headers=phone).status_code == 200   # twice is fine
    assert client.post(f"{API}/proposals/{token}/confirm", headers=phone).status_code == 409
    assert _proposal(db, co, token).status == "cancelled"
    # and a confirmed one can't be cancelled
    other = _draft(client, phone, scripted, desc="Fuel")["blocks"][0]["token"]
    client.post(f"{API}/proposals/{other}/confirm", headers=phone)
    assert client.post(f"{API}/proposals/{other}/cancel", headers=phone).status_code == 409


def test_n7_above_the_threshold_it_waits_for_a_second_person(client, db, co, phone, scripted):
    with use_company(co["cid"]):
        guardrails.save_settings(db, approval_threshold=1_000_000)
        db.commit()
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    r = client.post(f"{API}/proposals/{token}/confirm", headers=phone)
    assert r.status_code == 200 and r.json()["state"] == "waiting_for_approval"
    assert _proposal(db, co, token).approval_status == "requested"


def test_n7_someone_elses_card_is_not_yours(client, db, co, phone, scripted):
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    cfo = _login(client, co["cid"], co["cfo"])
    assert cfo.post(f"{API}/proposals/{token}/cancel").status_code == 403
    assert cfo.post(f"{API}/proposals/{token}/confirm").status_code == 403


# --- N8: threads -----------------------------------------------------------------------------

def test_n8_a_thread_redraws_its_cards_with_their_state(client, db, co, phone, scripted):
    first = _draft(client, phone, scripted)
    thread = first["thread_id"]
    token = first["blocks"][0]["token"]
    client.post(f"{API}/proposals/{token}/confirm", headers=phone)
    second = _draft(client, phone, scripted, desc="Fuel", thread=thread)
    client.post(f"{API}/proposals/{second['blocks'][0]['token']}/cancel", headers=phone)

    threads = client.get(f"{API}/threads", headers=phone).json()
    assert threads[0]["id"] == thread and threads[0]["message_count"] >= 4
    msgs = client.get(f"{API}/threads/{thread}/messages", headers=phone).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]   # no tool steps
    assert msgs[0]["text"] == "record Office rent"
    states = [b["state"] for m in msgs if m["role"] == "assistant" for b in m["blocks"] if b["type"] == "proposal"]
    assert states == ["executed", "cancelled"]
    cfo = _login(client, co["cid"], co["cfo"])
    assert cfo.get(f"{API}/threads/{thread}/messages").status_code == 404


# --- N9: the web -----------------------------------------------------------------------------

def test_n9_the_webs_cancel_reaches_the_server_and_new_parties_reach_the_card(client, db, co, scripted):
    web = _login(client, co["cid"], co["owner"])
    body = {"date": D, "description": "Consulting for Aria", "lines": [
        {"account_code": "1112", "debit": 5_000_000, "credit": 0},
        {"account_code": "4110", "debit": 0, "credit": 5_000_000}],
        "new_entities": [{"type": "client", "name": "Aria Co", "role": "client"}]}
    scripted(_calls(("propose_create_transaction", body)), _text("Drafted."))
    r = web.post("/ai-accountant/chat", json={"message": "invoice Aria"})
    assert r.status_code == 200, r.text
    card = r.json()["proposals"][0]
    assert [e["name"] for e in card["new_entities"]] == ["Aria Co"]
    assert web.post(f"/ai-accountant/proposals/{card['confirmation_token']}/cancel").json()["status"] == "cancelled"
    assert _proposal(db, co, card["confirmation_token"]).status == "cancelled"
    assert web.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]}).status_code == 409
