"""Guardrails on what the assistant proposes (roadmap 2026-09 §5.6):
two-person approval above a company threshold, no proposals into a closed
period, and a tool / proposal budget per chat message."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.models.audit_log import AuditLog
from app.models.transaction import Transaction
from app.services.ai_accountant import guardrails
from app.services.ai_accountant.llm_protocol import ChatMessage, LLMResponse, LLMUsage, ToolCall
from app.services.ai_accountant.orchestrator import run_chat_turn
from tests.test_cheque_lifecycle import _company

D = "2026-09-20"


class _FakeClient:
    shape = "fake"

    def __init__(self, scripted):
        self._queue = list(scripted)
        self.sent = []

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        self.sent.append(list(messages))
        if not self._queue:
            raise AssertionError("scripted responses exhausted")
        return self._queue.pop(0)


def _calls(*calls):
    return LLMResponse(message=ChatMessage(role="assistant", text=None, tool_calls=[
        ToolCall(id=f"c{i}_{uuid.uuid4().hex[:4]}", name=n, input=a) for i, (n, a) in enumerate(calls)]),
        stop_reason="tool_use", usage=LLMUsage())


def _text(t):
    return LLMResponse(message=ChatMessage(role="assistant", text=t), stop_reason="end_turn", usage=LLMUsage())


def _txn(amount, *, desc="Office rent", when=D, currency=None):
    body = {"date": when, "description": desc, "lines": [
        {"account_code": "6112", "debit": amount, "credit": 0},
        {"account_code": "1110", "debit": 0, "credit": amount}]}
    if currency:
        body["currency"] = currency
    return ("propose_create_transaction", body)


def _login(client, cid, user):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(user.id), username=user.username, is_admin=user.role == "owner",
                               role=user.role, company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def co(client, db):
    """An Iranian company with an owner (asks), a CFO (approves) and an accountant."""
    from app.core.auth import hash_password
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    from tests.test_admin_audit import _purge_company
    cid = _company(db, "ir", "IRR")
    people = {}
    with tenant_bypass():
        for role in ("owner", "cfo", "accountant"):
            ph, salt = hash_password("x" * 12)
            u = User(username=f"{role}-{uuid.uuid4().hex[:6]}", password_hash=ph, password_salt=salt,
                     role=role, is_admin=role == "owner", is_active=True, company_id=uuid.UUID(cid))
            db.add(u)
            people[role] = u
        db.commit()
        for u in people.values():
            db.refresh(u)
    yield {"cid": cid, **people}
    client.cookies.clear()
    db.rollback()
    with tenant_bypass():
        for u in people.values():
            db.delete(db.get(User, u.id))
        db.commit()
    _purge_company(db, cid)


def _chat(db, co, *responses, user="owner", message="record it"):
    with use_company(co["cid"]):
        return asyncio.run(run_chat_turn(db, user_id=str(co[user].id), username=co[user].username,
                                         user_message=message, client=_FakeClient(list(responses))))


def _threshold(db, co, amount):
    with use_company(co["cid"]):
        guardrails.save_settings(db, approval_threshold=amount)
        db.commit()


def _proposal(db, co, token):
    with use_company(co["cid"]):
        db.expire_all()
        return db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().first()


# ─── What a proposal moves ───────────────────────────────────────────────────

def test_the_amount_and_date_of_each_kind_of_proposal(db, co):
    with use_company(co["cid"]):
        amt = lambda tool, p, r=None: guardrails.proposal_amount(db, tool, p, r)  # noqa: E731
        assert amt(*_txn(1_500)) == 1_500
        assert amt("propose_create_invoice", {"lines": [{"quantity": 2, "unit_price": 1_000, "tax_rate": 10}],
                                              "issue_date": D}) == 2_200
        assert amt("propose_record_invoice_payment", {"amount": 700, "date": D}) == 700
        assert amt("propose_declare_dividend", {"total_amount": 9_000, "date": D}) == 9_000
        assert amt("propose_create_invoice_from_time", {"invoice_date": D}, {"preview": {"total": 4_400}}) == 4_400
        assert amt("propose_create_entity", {"name": "x"}) is None
        assert amt(*_txn(100, currency="USD")) is None                       # no USD rate on file
        assert guardrails.posting_date("propose_settle_commitment", {"date": "2026-01-05"}) == date(2026, 1, 5)
        assert guardrails.posting_date("propose_create_cheque", {"due_date": "2026-12-01"}) == date.today()
        assert guardrails.posting_date("propose_log_time", {"date": D}) is None


# ─── Two-person approval ─────────────────────────────────────────────────────

def test_below_the_threshold_nothing_changes(client, db, co):
    _threshold(db, co, 1_000_000)
    res = _chat(db, co, _calls(_txn(999_999)), _text("Drafted."))
    card = res.proposals[0]
    assert not card.get("needs_approval")
    api = _login(client, co["cid"], co["owner"])
    r = api.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]})
    assert r.status_code == 200 and r.json()["transaction_id"]


def test_above_the_threshold_a_second_person_approves(client, db, co):
    _threshold(db, co, 1_000_000)
    res = _chat(db, co, _calls(_txn(2_500_000, desc="Warehouse deposit")), _text("Drafted."))
    card = res.proposals[0]
    assert card["needs_approval"] is True and card["approval_threshold"] == 1_000_000
    assert card["amount_in_base"] == 2_500_000
    token = card["confirmation_token"]

    owner = _login(client, co["cid"], co["owner"])
    r = owner.post("/ai-accountant/execute", json={"confirmation_token": token})
    assert r.status_code == 202 and r.json()["status"] == "awaiting_approval" and r.json()["approvers"] == 1
    with use_company(co["cid"]):
        assert db.execute(select(Transaction).where(Transaction.description == "Warehouse deposit")).first() is None
    assert owner.post("/ai-accountant/execute", json={"confirmation_token": token}).status_code == 202   # still waiting
    mine = owner.get("/ai-accountant/approvals").json()["items"]
    assert [i["mine"] for i in mine] == [True] and "Warehouse deposit" in mine[0]["summary"]
    assert owner.post(f"/ai-accountant/approvals/{token}/approve").status_code == 403       # not their own

    accountant = _login(client, co["cid"], co["accountant"])
    assert accountant.get("/ai-accountant/approvals").status_code == 403                    # can't approve at all

    cfo = _login(client, co["cid"], co["cfo"])
    waiting = cfo.get("/ai-accountant/approvals").json()["items"]
    assert len(waiting) == 1 and waiting[0]["mine"] is False and waiting[0]["amount"] == 2_500_000
    assert waiting[0]["requested_by"] == co["owner"].username
    r = cfo.post(f"/ai-accountant/approvals/{token}/approve")
    assert r.status_code == 200 and r.json()["transaction_id"]
    p = _proposal(db, co, token)
    assert p.status == "executed" and p.approval_status == "approved" and p.approved_by == str(co["cfo"].id)
    with use_company(co["cid"]):
        audit = db.execute(select(AuditLog).where(AuditLog.entity_type == "ai_proposal",
                                                  AuditLog.action == "approve")).scalars().all()
    assert any(str(co["owner"].id) in (a.detail or "") for a in audit)
    # the requester's card: already done
    again = _login(client, co["cid"], co["owner"]).post("/ai-accountant/execute", json={"confirmation_token": token})
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert cfo.get("/ai-accountant/approvals").json()["items"] == []


def test_a_rejection_cancels_it(client, db, co):
    _threshold(db, co, 1_000)
    token = _chat(db, co, _calls(_txn(5_000)), _text("Drafted.")).proposals[0]["confirmation_token"]
    owner = _login(client, co["cid"], co["owner"])
    owner.post("/ai-accountant/execute", json={"confirmation_token": token})
    cfo = _login(client, co["cid"], co["cfo"])
    r = cfo.post(f"/ai-accountant/approvals/{token}/reject", json={"note": "wrong supplier"})
    assert r.status_code == 200
    p = _proposal(db, co, token)
    assert p.status == "cancelled" and p.approval_status == "rejected" and p.approval_note == "wrong supplier"
    r = _login(client, co["cid"], co["owner"]).post("/ai-accountant/execute", json={"confirmation_token": token})
    assert r.status_code == 409 and "rejected" in r.json()["detail"]
    assert _login(client, co["cid"], co["cfo"]).post(f"/ai-accountant/approvals/{token}/approve").status_code == 409


def test_waiting_outlives_the_ten_minutes_but_not_a_week(client, db, co):
    _threshold(db, co, 1_000)
    token = _chat(db, co, _calls(_txn(5_000)), _text("Drafted.")).proposals[0]["confirmation_token"]
    _login(client, co["cid"], co["owner"]).post("/ai-accountant/execute", json={"confirmation_token": token})
    with use_company(co["cid"]):
        p = _proposal(db, co, token)
        p.created_at = datetime.now(timezone.utc) - timedelta(hours=3)          # far past the 10-minute window
        db.commit()
    token2 = _chat(db, co, _calls(_txn(6_000)), _text("Drafted.")).proposals[0]["confirmation_token"]
    _login(client, co["cid"], co["owner"]).post("/ai-accountant/execute", json={"confirmation_token": token2})
    with use_company(co["cid"]):
        p2 = _proposal(db, co, token2)
        p2.approval_requested_at = datetime.now(timezone.utc) - timedelta(days=8)
        db.commit()
    cfo = _login(client, co["cid"], co["cfo"])
    assert [i["confirmation_token"] for i in cfo.get("/ai-accountant/approvals").json()["items"]] == [token]
    assert cfo.post(f"/ai-accountant/approvals/{token}/approve").status_code == 200
    assert cfo.post(f"/ai-accountant/approvals/{token2}/approve").status_code == 410


def test_an_amount_that_cannot_be_converted_needs_approval(db, co):
    _threshold(db, co, 10_000_000)
    card = _chat(db, co, _calls(_txn(5, currency="USD")), _text("Drafted.")).proposals[0]
    assert card["needs_approval"] is True and card["amount_in_base"] is None


def test_no_threshold_no_approval(client, db, co):
    card = _chat(db, co, _calls(_txn(900_000_000)), _text("Drafted.")).proposals[0]
    assert not card.get("needs_approval")


def test_approvers_see_it_in_the_bell(client, db, co):
    _threshold(db, co, 1_000)
    token = _chat(db, co, _calls(_txn(5_000, desc="Big purchase")), _text("Drafted.")).proposals[0]["confirmation_token"]
    _login(client, co["cid"], co["owner"]).post("/ai-accountant/execute", json={"confirmation_token": token})
    cfo = _login(client, co["cid"], co["cfo"])
    titles = [i["title"] for i in cfo.get("/notifications/feed").json()]
    assert any(t.startswith("Approval needed:") and "Big purchase" in t for t in titles)
    cfo.post(f"/ai-accountant/approvals/{token}/approve")
    assert not any(t.startswith("Approval needed:") for t in (i["title"] for i in cfo.get("/notifications/feed").json()))


def test_the_threshold_setting(client, db, co):
    owner = _login(client, co["cid"], co["owner"])
    d = owner.get("/ai-accountant/guardrails").json()
    assert d["approval_threshold"] is None and {a["role"] for a in d["approvers"]} == {"owner", "cfo"}
    d = owner.put("/ai-accountant/guardrails", json={"approval_threshold": 50_000_000}).json()
    assert d["approval_threshold"] == 50_000_000
    assert owner.put("/ai-accountant/guardrails", json={"approval_threshold": -1}).status_code == 422
    assert owner.put("/ai-accountant/guardrails", json={"approval_threshold": None}).json()["approval_threshold"] is None
    accountant = _login(client, co["cid"], co["accountant"])
    assert accountant.put("/ai-accountant/guardrails", json={"approval_threshold": 1}).status_code == 403


# ─── Closed periods ──────────────────────────────────────────────────────────

def test_a_proposal_into_a_closed_period_is_refused_when_made(db, co):
    from app.services.period_service import set_closed_period
    with use_company(co["cid"]):
        set_closed_period(db, date(2026, 8, 31))
        db.commit()
    client = _FakeClient([_calls(_txn(100, when="2026-08-15"),
                                 ("propose_record_invoice_payment", {"invoice": "none", "amount": 1})),
                          _text("I can't, the period is closed.")])
    with use_company(co["cid"]):
        res = asyncio.run(run_chat_turn(db, user_id=str(co["owner"].id), username="o",
                                        user_message="record the rent I paid on 2026-08-15", client=client))
    assert res.proposals == []
    fed_back = [m for m in client.sent[-1] if m.role == "tool"]
    assert any("closed through 2026-08-31" in (m.text or "") for m in fed_back)


def test_every_posting_proposal_is_checked_not_only_entries(db, co):
    from app.services.period_service import set_closed_period
    with use_company(co["cid"]):
        set_closed_period(db, date(2026, 8, 31))
        db.commit()
        for tool, payload in (("propose_settle_commitment", {"commitment_id": str(uuid.uuid4()), "date": "2026-08-01"}),
                              ("propose_declare_dividend", {"total_amount": 5, "date": "2026-07-01"}),
                              ("propose_cheque_step", {"action": "deposit", "date": "2026-08-30"})):
            token = uuid.uuid4()
            db.add(AIProposal(confirmation_token=token, user_id=str(co["owner"].id), tool_name=tool,
                              tool_input=payload, status="pending"))
            db.commit()
            with pytest.raises(guardrails.ProposalRefused):
                guardrails.review(db, {"confirmation_token": str(token), "summary": "x"})
            assert _proposal(db, co, str(token)).status == "cancelled"
        # not posting → not checked
        token = uuid.uuid4()
        db.add(AIProposal(confirmation_token=token, user_id=str(co["owner"].id), tool_name="propose_log_time",
                          tool_input={"date": "2026-08-01"}, status="pending"))
        db.commit()
        assert guardrails.review(db, {"confirmation_token": str(token)}) == {}


# ─── Budget per message ──────────────────────────────────────────────────────

def test_the_tool_budget_stops_a_runaway_message(db, co, monkeypatch):
    from app.services import ai_usage
    real = ai_usage.load_settings
    monkeypatch.setattr(ai_usage, "load_settings", lambda db=None: {**real(db), "tool_calls_per_message": 3})
    many = _calls(*[("get_account_balance", {"account_code": "1110"})] * 5)
    client = _FakeClient([many, _text("Here is what I found so far.")])
    with use_company(co["cid"]):
        res = asyncio.run(run_chat_turn(db, user_id=str(co["owner"].id), username="o", user_message="x", client=client))
    tool_msgs = [m for m in client.sent[-1] if m.role == "tool"]
    assert sum("Tool budget for this message is used up (3 calls)" in (m.text or "") for m in tool_msgs) == 2
    assert res.text == "Here is what I found so far."


def test_a_model_that_keeps_calling_is_stopped_and_every_call_answered(db, co, monkeypatch):
    from app.services import ai_usage
    real = ai_usage.load_settings
    monkeypatch.setattr(ai_usage, "load_settings", lambda db=None: {**real(db), "tool_calls_per_message": 1})
    call = ("get_account_balance", {"account_code": "1110"})
    client = _FakeClient([_calls(call, call), _calls(call)])
    with use_company(co["cid"]):
        res = asyncio.run(run_chat_turn(db, user_id=str(co["owner"].id), username="o", user_message="x", client=client))
    assert res.stop_reason == "tool_budget" and "stopped" in res.text
    from app.models.ai_accountant import AIChatMessage
    with use_company(co["cid"]):
        rows = db.execute(select(AIChatMessage).where(AIChatMessage.session_id == uuid.UUID(res.session_id))
                          .order_by(AIChatMessage.created_at)).scalars().all()
    asked = {c["id"] for r in rows if r.role == "assistant" for c in (r.content.get("tool_calls") or [])}
    answered = {r.content.get("tool_call_id") for r in rows if r.role == "tool"}
    assert asked <= answered                                # the next request stays valid


def test_the_proposal_budget(db, co, monkeypatch):
    from app.services import ai_usage
    real = ai_usage.load_settings
    monkeypatch.setattr(ai_usage, "load_settings", lambda db=None: {**real(db), "proposals_per_message": 2})
    res = _chat(db, co, _calls(_txn(1), _txn(2), _txn(3)), _text("Two drafted."))
    assert len(res.proposals) == 2


def test_the_messenger_says_it_in_both_languages():
    from app.services import messenger
    assert "awaiting_approval" in messenger.TEXTS["en"] and "awaiting_approval" in messenger.TEXTS["fa"]


def test_the_chat_response_carries_the_approval_flag(client, db, co, monkeypatch):
    """The card can only say so if the API passes it on."""
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import ChatResult

    async def fake_turn(db, **kw):
        return ChatResult(session_id=str(uuid.uuid4()), text="Drafted.", proposals=[{
            "confirmation_token": str(uuid.uuid4()), "tool_name": "propose_create_transaction",
            "summary": "Warehouse deposit", "preview": {}, "needs_approval": True,
            "approval_threshold": 1_000, "amount_in_base": 2_500}], tool_calls=[], stop_reason="end_turn", turns=1)
    monkeypatch.setattr(api_mod, "run_chat_turn", fake_turn)
    r = _login(client, co["cid"], co["owner"]).post("/ai-accountant/chat", json={"message": "x"})
    assert r.status_code == 200, r.text
    card = r.json()["proposals"][0]
    assert card["needs_approval"] is True and card["approval_threshold"] == 1_000 and card["amount_in_base"] == 2_500
