"""Edit a draft and decide on approvals from the phone (scenarios N27, N28;
roadmap ROADMAP_ANDROID_CHAT P0.4)."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant import guardrails
from tests.test_ai_guardrails import _calls, _text, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import _draft, phone, scripted  # noqa: F401  (fixtures)

API = "/api/mobile/v1"


def _proposal(db, co, token):
    with use_company(co["cid"]):
        db.expire_all()
        return db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().first()


def _phone_for(client, user) -> dict:
    client.cookies.clear()
    r = client.post(f"{API}/auth/login", json={"username": user.username, "password": "x" * 12, "device_name": "Pixel"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _threshold(db, co, amount):
    with use_company(co["cid"]):
        guardrails.save_settings(db, approval_threshold=amount)
        db.commit()


def test_n27_edit_changes_the_date_the_words_and_the_amount(client, db, co, phone, scripted):
    first = _draft(client, phone, scripted)
    old = first["blocks"][0]["token"]
    r = client.post(f"{API}/proposals/{old}/edit", headers=phone,
                    json={"date": "۱۴۰۵/۰۶/۲۵", "description": "Office rent, Shahrivar", "amount": 85_000_000})
    assert r.status_code == 200, r.text
    body = r.json()
    new = body["block"]
    assert body["replaces"] == old and new["type"] == "proposal" and new["token"] != old
    assert new["amount"]["value"] == 85_000_000 and new["date"]["iso"] == "2026-09-16"
    assert new["title"] == "Office rent, Shahrivar"
    assert [(ln["debit"], ln["credit"]) for ln in new["lines"]] == [(85_000_000, 0), (0, 85_000_000)]
    assert _proposal(db, co, old).status == "cancelled"
    assert _proposal(db, co, new["token"]).user_message == _proposal(db, co, old).user_message
    # the old card can't be posted any more; the new one can
    assert client.post(f"{API}/proposals/{old}/confirm", headers=phone).status_code == 409
    assert client.post(f"{API}/proposals/{new['token']}/confirm", headers=phone).json()["state"] == "posted"
    # the thread keeps both: the old one withdrawn, the new one after it, posted
    msgs = client.get(f"{API}/threads/{first['thread_id']}/messages", headers=phone).json()
    cards = [b for m in msgs if m["role"] == "assistant" for b in m["blocks"] if b["type"] == "proposal"]
    assert [(c["token"], c["state"]) for c in cards] == [(old, "replaced"), (new["token"], "executed")]


def test_n27_edit_says_what_it_cannot_change(client, db, co, phone, scripted):
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    for change, words in [({}, "Nothing was changed"), ({"date": "someday"}, "couldn't be read"),
                          ({"date": "2099-01-01"}, "future"), ({"description": "  "}, "can't be empty")]:
        r = client.post(f"{API}/proposals/{token}/edit", headers=phone, json=change)
        assert r.status_code == 422 and words in r.json()["detail"], (change, r.text)
    assert _proposal(db, co, token).status == "pending"                     # a refused edit leaves the card as it was
    # a voucher of several lines keeps its amounts for the accountant to change
    scripted(_calls(("propose_create_transaction", {"date": "2026-09-20", "description": "Rent and fuel", "lines": [
        {"account_code": "6112", "debit": 600_000, "credit": 0}, {"account_code": "6112", "debit": 400_000, "credit": 0},
        {"account_code": "1110", "debit": 0, "credit": 1_000_000}]})), _text("Drafted."))
    reply = client.post(f"{API}/chat", headers=phone, json={"message": "record rent 600000 and fuel 400000"}).json()
    multi = next(b["token"] for b in reply["blocks"] if b["type"] == "proposal")
    r = client.post(f"{API}/proposals/{multi}/edit", headers=phone, json={"amount": 120})
    assert r.status_code == 422 and "several lines" in r.json()["detail"]
    # Persian on a Persian phone
    r = client.post(f"{API}/proposals/{token}/edit", headers={**phone, "X-UI-Language": "fa"}, json={})
    assert r.json()["detail"] == "چیزی تغییر نکرد."
    # someone else's card, and a posted one
    cfo = _phone_for(client, co["cfo"])
    assert client.post(f"{API}/proposals/{token}/edit", headers=cfo, json={"amount": 5}).status_code == 403
    client.post(f"{API}/proposals/{token}/confirm", headers=phone)
    assert client.post(f"{API}/proposals/{token}/edit", headers=phone, json={"amount": 5}).status_code == 409


def test_n27_an_edit_above_the_threshold_says_it_needs_approval(client, db, co, phone, scripted):
    token = _draft(client, phone, scripted, amount=500_000)["blocks"][0]["token"]
    _threshold(db, co, 1_000_000)
    new = client.post(f"{API}/proposals/{token}/edit", headers=phone, json={"amount": 2_000_000}).json()["block"]
    assert new["needs_approval"] is True


def test_n28_the_approver_finds_it_in_the_briefing_and_approves_it(client, db, co, phone, scripted, monkeypatch):
    from app.services import insight_service
    monkeypatch.setattr(insight_service, "briefing_text", lambda insights, lang: None)
    _threshold(db, co, 1_000_000)
    asked = _draft(client, phone, scripted)
    token = asked["blocks"][0]["token"]
    assert client.post(f"{API}/proposals/{token}/confirm", headers=phone).json()["state"] == "waiting_for_approval"
    # the one who asked gets no card to approve their own
    assert client.post(f"{API}/briefing", headers=phone, json={}).json()["blocks"] == []
    cfo = _phone_for(client, co["cfo"])
    cards = client.get(f"{API}/approvals", headers=cfo).json()
    assert [(c["type"], c["token"], c["requested_by"], c["actions"]) for c in cards] == [
        ("approval", token, co["owner"].username, ["approve", "reject"])]
    b = client.post(f"{API}/briefing", headers={**cfo, "X-UI-Language": "fa"}, json={}).json()
    assert [x["type"] for x in b["blocks"]] == ["text", "approval"]
    assert b["blocks"][1]["amount"]["value"] == 80_000_000 and b["blocks"][1]["token"] == token
    # said once a day: opening the app again doesn't repeat it
    assert client.post(f"{API}/briefing", headers=cfo, json={"thread_id": b["thread_id"]}).json()["blocks"] == []
    r = client.post(f"{API}/approvals/{token}/approve", headers=cfo)
    assert r.status_code == 200 and r.json()["state"] == "posted" and r.json()["block"]["undo_seconds"] > 0
    p = _proposal(db, co, token)
    assert p.status == "executed" and p.approved_by == str(co["cfo"].id)
    # both threads redraw it as posted
    for who, thread in ((phone, asked["thread_id"]), (cfo, b["thread_id"])):
        msgs = client.get(f"{API}/threads/{thread}/messages", headers=who).json()
        states = [x.get("state") for m in msgs if m["role"] == "assistant" for x in m["blocks"]
                  if x["type"] in ("proposal", "approval")]
        assert states == ["executed"], (who, states)


def test_n28_reject_with_a_reason_and_who_may_decide(client, db, co, phone, scripted):
    _threshold(db, co, 1_000_000)
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    client.post(f"{API}/proposals/{token}/confirm", headers=phone)
    assert client.post(f"{API}/approvals/{token}/approve", headers=phone).status_code == 403    # not your own
    accountant = _phone_for(client, co["accountant"])
    assert client.get(f"{API}/approvals", headers=accountant).status_code == 403             # no approval rights
    cfo = _phone_for(client, co["cfo"])
    r = client.post(f"{API}/approvals/{token}/reject", headers=cfo, json={"note": "Wrong month"})
    assert r.status_code == 200 and r.json() == {"state": "rejected"}
    p = _proposal(db, co, token)
    assert p.status == "cancelled" and p.approval_status == "rejected" and p.approval_note == "Wrong month"
    assert client.post(f"{API}/approvals/{token}/approve", headers=cfo).status_code == 409


def test_n16_briefings_on_different_days_keep_unique_block_ids(client, db, co, phone, monkeypatch):
    from app.models.ai_accountant import AIChatMessage
    from app.services import insight_service
    monkeypatch.setattr(insight_service, "briefing_text", lambda insights, lang: "Two invoices are overdue.")
    first = client.post(f"{API}/briefing", headers=phone, json={}).json()
    thread = first["thread_id"]
    with use_company(co["cid"]):                    # yesterday's briefing, as if the app opened then
        row = db.execute(select(AIChatMessage).where(AIChatMessage.session_id == uuid.UUID(thread))).scalars().first()
        from datetime import timedelta
        row.created_at = row.created_at - timedelta(days=1)
        db.commit()
    again = client.post(f"{API}/briefing", headers=phone, json={"thread_id": thread}).json()
    assert again["blocks"] and again["blocks"][0]["id"] != first["blocks"][0]["id"]
    ids = [b["id"] for m in client.get(f"{API}/threads/{thread}/messages", headers=phone).json() for b in m["blocks"]]
    assert len(ids) == len(set(ids)) == 2
