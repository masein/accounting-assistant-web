"""The AI review queue (roadmap §5.5, part 2).

About one chat turn in ten — web and the messenger bots — is kept for the
company's owner to review. It never leaves the company: only the owner reads
it, the platform sees counts, and it goes with its conversation or after 90
days. (conftest turns sampling off for the rest of the suite.)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.tenant import tenant_bypass, use_company
from app.models.ai_accountant import AIChatSession, AIReviewSample
from app.services import ai_review
from app.services.ai_accountant.orchestrator import ChatResult
from tests.test_ai_guardrails import _login, co  # noqa: F401 — the company fixture


@pytest.fixture()
def sampling(monkeypatch):
    monkeypatch.setattr(ai_review, "SAMPLE_RATE", 1.0)


def _result(session_id=None, **kw):
    return ChatResult(session_id=session_id or str(uuid.uuid4()), text=kw.pop("text", "Drafted: 200,000 for food."),
                      proposals=kw.pop("proposals", [{
                          "confirmation_token": str(uuid.uuid4()), "tool_name": "propose_create_transaction",
                          "summary": "Food 200,000", "preview": {}, "amount_in_base": 200_000}]),
                      tool_calls=kw.pop("tool_calls", [
                          {"name": "search_accounts", "input": {"query": "food"}, "result": {"matches": []}},
                          {"name": "find_entity", "input": {"query": "Nobody"}},            # failed: no result
                          {"name": "propose_create_transaction", "input": {"lines": [{"account_code": "6112"}]},
                           "result": {"confirmation_token": "x"}}]),
                      stop_reason="end_turn", turns=3, **kw)


def _session(db, cid, user):
    with use_company(cid):
        s = AIChatSession(id=uuid.uuid4(), user_id=str(user.id), title="t")
        db.add(s)
        db.commit()
        return s


def _keep(db, cid, user, *, message="we paid 200000 for food", lang="en", session=True, **kw):
    sid = str(_session(db, cid, user).id) if session else None
    with use_company(cid):
        return ai_review.maybe_sample(db, _result(sid, **kw), user_id=str(user.id), username=user.username,
                                      message=message, lang=lang, channel="web", latency_ms=1234, draw=lambda: 0.0)


# --- keeping a turn ---------------------------------------------------------------------------------------------

def test_a_kept_turn_is_a_snapshot_of_what_happened(db, co, sampling):
    s = _keep(db, co["cid"], co["cfo"])
    assert s is not None and s.company_id == uuid.UUID(co["cid"]) and s.session_id is not None
    assert (s.user_message, s.reply, s.lang, s.channel, s.latency_ms, s.turns, s.stop_reason) == (
        "we paid 200000 for food", "Drafted: 200,000 for food.", "en", "web", 1234, 3, "end_turn")
    assert [(t["name"], t["ok"]) for t in s.tools] == [
        ("search_accounts", True), ("find_entity", False), ("propose_create_transaction", True)]
    assert s.tools[0]["input"] == {"query": "food"} and s.tool_errors == 1
    assert s.cards == [{"tool": "propose_create_transaction", "summary": "Food 200,000", "amount": 200_000}]
    assert s.verdict is None and s.username == co["cfo"].username


def test_about_one_turn_in_ten_is_kept_and_the_owner_can_stop_it(db, co, monkeypatch):
    user = co["owner"]
    with use_company(co["cid"]):
        keep = lambda d: ai_review.maybe_sample(db, _result(), user_id=str(user.id), username=user.username,  # noqa: E731
                                                message="hi", lang="en", draw=lambda: d)
        monkeypatch.setattr(ai_review, "SAMPLE_RATE", 0.1)
        assert keep(0.05) is not None and keep(0.1) is None and keep(0.7) is None
        ai_review.save_settings(db, enabled=False)
        db.commit()
        assert keep(0.0) is None and ai_review.get_settings(db)["enabled"] is False
        assert ai_review.maybe_sample(db, _result(), user_id="u", username="u", message="hi", lang="en",
                                      personal=True, draw=lambda: 0.0) is None          # personal tenants: never
    assert ai_review.SAMPLE_RATE == 0.1 and ai_review.RETENTION_DAYS == 90


def test_keeping_a_turn_never_breaks_it(db, co, sampling):
    @dataclass
    class Odd:                                          # a result missing most fields (the bot's fakes)
        text: str = "ok"
        session_id: str = "not-a-uuid"
        proposals: list = field(default_factory=list)
    with use_company(co["cid"]):
        assert ai_review.maybe_sample(db, Odd(), user_id="u", username="u", message="x", lang="en",
                                      draw=lambda: 0.0) is None
        # a session id that isn't in the table: kept, just not linked
        s = ai_review.maybe_sample(db, _result(), user_id="u", username="u", message="x", lang="en", draw=lambda: 0.0)
        assert s is not None and s.session_id is None
        db.execute(select(AIReviewSample)).all()        # the session is still usable


def test_a_turn_goes_with_its_conversation_and_after_90_days(db, co, sampling):
    from app.jobs.scheduler import job_ai_review_purge
    s = _keep(db, co["cid"], co["owner"])
    sid, keep_id = s.session_id, s.id
    def exists(i):                                       # a column query reads the table, not the session
        return db.execute(select(AIReviewSample.id).where(AIReviewSample.id == i)).first() is not None
    with use_company(co["cid"]):
        db.delete(db.get(AIChatSession, sid))
        db.commit()
        assert not exists(keep_id)                                           # cascade
    old = _keep(db, co["cid"], co["owner"])
    new = _keep(db, co["cid"], co["owner"])
    old_id, new_id = old.id, new.id
    with use_company(co["cid"]):
        old.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db.commit()
        assert job_ai_review_purge(db, date.today()) == {"deleted": 1}
        assert not exists(old_id) and exists(new_id)


# --- the owner's queue --------------------------------------------------------------------------------------------

def test_the_owner_reviews_the_queue(client, db, co, sampling):
    a = _keep(db, co["cid"], co["cfo"], message="first")
    b = _keep(db, co["cid"], co["accountant"], message="second", lang="fa")
    api = _login(client, co["cid"], co["owner"])
    d = api.get("/ai-accountant/review-samples").json()
    assert [i["message"] for i in d["items"]] == ["second", "first"]                      # newest first
    assert d["counts"] == {"new": 2, "good": 0, "bad": 0, "total": 2} and d["settings"]["enabled"] is True
    assert d["items"][0]["tools"][1] == {"name": "find_entity", "input": {"query": "Nobody"}, "ok": False}
    r = api.patch(f"/ai-accountant/review-samples/{a.id}", json={"verdict": "good"})
    assert r.status_code == 200 and r.json()["counts"]["good"] == 1
    r = api.patch(f"/ai-accountant/review-samples/{b.id}", json={"verdict": "bad", "note": "should have asked which bank"})
    s = r.json()["sample"]
    assert (s["verdict"], s["note"], s["reviewed_by"]) == ("bad", "should have asked which bank", co["owner"].username)
    assert [i["message"] for i in api.get("/ai-accountant/review-samples?status=bad").json()["items"]] == ["second"]
    assert api.get("/ai-accountant/review-samples?status=new").json()["items"] == []
    assert len(api.get("/ai-accountant/review-samples?status=all").json()["items"]) == 2
    back = api.patch(f"/ai-accountant/review-samples/{b.id}", json={"verdict": None}).json()["sample"]
    assert back["verdict"] is None and back["reviewed_by"] is None and back["reviewed_at"] is None
    assert api.patch(f"/ai-accountant/review-samples/{b.id}", json={"verdict": "meh"}).status_code == 422
    assert api.get("/ai-accountant/review-samples?status=weird").status_code == 422
    assert api.delete(f"/ai-accountant/review-samples/{a.id}").json()["counts"]["total"] == 1
    assert api.patch(f"/ai-accountant/review-samples/{a.id}", json={"verdict": "good"}).status_code == 404
    assert api.get("/ai-accountant/review-samples/not-an-id/scenario").status_code == 404


def test_only_the_owner_reads_the_queue(client, db, co, sampling):
    s = _keep(db, co["cid"], co["owner"])
    for role in ("cfo", "accountant"):
        api = _login(client, co["cid"], co[role])
        assert api.get("/ai-accountant/review-samples").status_code == 403, role
        assert api.patch(f"/ai-accountant/review-samples/{s.id}", json={"verdict": "good"}).status_code == 403
        assert api.get(f"/ai-accountant/review-samples/{s.id}/scenario").status_code == 403
        assert api.put("/ai-accountant/review-settings", json={"enabled": False}).status_code == 403


def test_another_company_never_sees_the_samples(client, db, co, sampling):
    from app.core.auth import hash_password
    from app.models.user import User
    from tests.test_admin_audit import _purge_company
    from tests.test_cheque_lifecycle import _company
    sid = str(_keep(db, co["cid"], co["owner"]).id)
    other = _company(db, "ir", "IRR")
    try:
        with tenant_bypass():
            ph, salt = hash_password("x" * 12)
            boss = User(username=f"boss-{uuid.uuid4().hex[:6]}", password_hash=ph, password_salt=salt, role="owner",
                        is_admin=True, is_active=True, company_id=uuid.UUID(other))
            db.add(boss)
            db.commit()
            db.refresh(boss)
        api = _login(client, other, boss)
        assert api.get("/ai-accountant/review-samples?status=all").json()["items"] == []
        assert api.patch(f"/ai-accountant/review-samples/{sid}", json={"verdict": "bad"}).status_code == 404
        assert api.delete(f"/ai-accountant/review-samples/{sid}").status_code == 404
        with use_company(co["cid"]):                # and it is still there, untouched
            row = db.execute(select(AIReviewSample.verdict).where(AIReviewSample.id == uuid.UUID(sid))).first()
        assert row is not None and row[0] is None
    finally:
        from sqlalchemy import inspect
        for r in ("owner", "cfo", "accountant"):       # switching tenants detached the fixture's users
            if inspect(co[r]).detached:
                db.add(co[r])
        _purge_company(db, other)


def test_the_owner_can_stop_sampling_and_it_is_audited(client, db, co, sampling):
    from app.models.audit_log import AuditLog
    api = _login(client, co["cid"], co["owner"])
    assert api.put("/ai-accountant/review-settings", json={"enabled": False}).json()["enabled"] is False
    assert api.get("/ai-accountant/review-samples").json()["settings"]["enabled"] is False
    with tenant_bypass():
        row = db.execute(select(AuditLog).where(AuditLog.entity_type == "ai_review")
                         .order_by(AuditLog.timestamp.desc())).scalars().first()
    assert row is not None and json.loads(row.detail) == {"from": True, "to": False}


# --- where turns come from --------------------------------------------------------------------------------------

def test_the_web_chat_keeps_a_turn(client, db, co, sampling, monkeypatch):
    from app.api import ai_accountant as api_mod
    sess = _session(db, co["cid"], co["cfo"])

    async def fake_turn(db, **kw):
        return _result(str(sess.id))
    monkeypatch.setattr(api_mod, "run_chat_turn", fake_turn)
    r = _login(client, co["cid"], co["cfo"]).post("/ai-accountant/chat", json={"message": "we paid 200000 for food"})
    assert r.status_code == 200, r.text
    with use_company(co["cid"]):
        s = db.execute(select(AIReviewSample)).scalars().one()
    assert (s.channel, s.user_message, s.user_id, s.session_id) == ("web", "we paid 200000 for food", str(co["cfo"].id), sess.id)
    assert s.latency_ms is not None and s.latency_ms >= 0


def test_a_bot_turn_is_kept_with_its_channel(db, co, sampling, monkeypatch):
    import asyncio

    from app.models.messenger import MessengerLink
    from app.services import messenger
    from app.services.ai_accountant import orchestrator

    async def fake_turn(db, **kw):
        return _result(None, text="ok")
    monkeypatch.setattr(orchestrator, "run_chat_turn", fake_turn)
    with use_company(co["cid"]):
        link = MessengerLink(platform="telegram", user_id=co["accountant"].id, status="active", chat_id="777")
        db.add(link)
        db.commit()
    asyncio.run(messenger._run_turn(db, link, "۵۰ هزار نان", "fa", messenger._Reply("777")))
    with use_company(co["cid"]):
        s = db.execute(select(AIReviewSample)).scalars().one()
    assert (s.channel, s.lang, s.user_message) == ("telegram", "fa", "۵۰ هزار نان")


# --- what leaves the queue ----------------------------------------------------------------------------------------

def test_a_reviewed_turn_downloads_as_an_eval_scenario(client, db, co, sampling):
    from tests.test_ai_eval import _CARD_KEYS, _EXPECT_KEYS
    good = _keep(db, co["cid"], co["owner"])
    bad = _keep(db, co["cid"], co["owner"], message="اجاره رو ثبت کن", lang="fa")
    api = _login(client, co["cid"], co["owner"])
    api.patch(f"/ai-accountant/review-samples/{good.id}", json={"verdict": "good"})
    api.patch(f"/ai-accountant/review-samples/{bad.id}", json={"verdict": "bad", "note": "asked twice"})
    r = api.get(f"/ai-accountant/review-samples/{good.id}/scenario")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    sc = r.json()
    assert sc["message"] == "we paid 200000 for food" and sc["lang"] == "en"
    assert sc["expect"] == {"proposals": 1, "tools_all": ["propose_create_transaction"],
                            "card": {"tool": "propose_create_transaction"}, "reply_lang": "en"}
    # the trajectory that worked, then the reply — failed calls left out
    assert [st.get("tool") for st in sc["replay"][:-1]] == ["search_accounts", "propose_create_transaction"]
    assert sc["replay"][-1] == {"text": "Drafted: 200,000 for food."}
    # it fits the eval set as-is
    assert set(sc["expect"]) <= _EXPECT_KEYS and set(sc["expect"]["card"]) <= _CARD_KEYS
    sb = api.get(f"/ai-accountant/review-samples/{bad.id}/scenario").json()
    assert sb["expect"] == {} and sb["review"]["note"] == "asked twice" and sb["lang"] == "fa"


def test_the_platform_sees_counts_never_text(client, db, co, sampling):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    a = _keep(db, co["cid"], co["owner"])
    _keep(db, co["cid"], co["owner"], message="secret supplier prices")
    api = _login(client, co["cid"], co["owner"])
    api.patch(f"/ai-accountant/review-samples/{a.id}", json={"verdict": "good"})
    tok = create_session_token(user_id=str(uuid.uuid4()), username="platform", is_admin=True, is_superadmin=True,
                               role="owner")
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, generate_csrf_token())
    rows = client.get("/admin/companies/ai-review").json()
    mine = next(r for r in rows if r["company_id"] == co["cid"])
    assert (mine["samples"], mine["good"], mine["bad"], mine["new"]) == (2, 1, 0, 1)
    assert sum(m["good"] + m["new"] for m in mine["by_model"].values()) == 2
    assert "secret" not in json.dumps(rows) and "food" not in json.dumps(rows)
    # an owner may not read it
    assert _login(client, co["cid"], co["owner"]).get("/admin/companies/ai-review").status_code == 403


# --- the Settings panel -------------------------------------------------------------------------------------------

def test_the_settings_panel_and_console_column_are_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ('id="ai-review-section"', 'id="ai-rev-enabled"', 'id="ai-rev-tabs"', 'id="ai-rev-list"',
               'data-i18n="companiesAiReview"'):
        assert el in html, el
    admin = open("app/static/js/04-admin-settings.js", encoding="utf-8").read()
    page = admin.split("function loadSettingsPage() {", 1)[1].split("\n    }\n", 1)[0]
    owner = page.split("if (currentRole === 'owner') {", 1)[1].split("}", 1)[0]
    assert "loadAIReview()" in owner                                   # with the Settings page, owner only
    fn = admin.split("async function loadAIReview(status) {", 1)[1].split("\n    }\n", 1)[0]
    assert "sec.style.display = 'none'" in fn                          # hidden unless the API answers
    js = admin + open("app/static/js/13-companies-products.js", encoding="utf-8").read()
    keys = {"aiRevTitle", "aiRevDesc", "aiRevEnabled", "aiRevTabNew", "aiRevTabBad", "aiRevTabGood", "aiRevTabAll",
            "aiRevEmpty", "aiRevToolFailed", "aiRevNoTools", "aiRevReviewedBy", "aiRevGood", "aiRevBad", "aiRevReset",
            "aiRevDownload", "aiRevDiscard", "aiRevDiscardConfirm", "aiRevNotePrompt", "aiRevReply", "aiRevSaved",
            "companiesAiReview", "companiesAiReviewCell"}
    text = i18n_text()
    for k in keys:
        assert text.count(f"{k}:") == 4, k                             # en, fa, es, ar
        assert k in js or k in html, k
    # the reply and question are escaped, and nothing the user typed is put in an attribute unescaped
    assert "escapeHtml(s.message)" in fn or "escapeHtml(s.message)" in admin
    assert "escapeHtml(s.reply || '—')" in admin
