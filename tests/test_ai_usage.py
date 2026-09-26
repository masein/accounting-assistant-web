"""AI usage metering, budgets and per-user limits (roadmap 2026-09 §2.5).

Parsing each provider's usage block, the price table, the ledger row written
around every provider call, the rolling 24-hour budgets (company, user,
overrides, super-admin exemption), the per-minute request limits, the HTTP
answers when a limit is hit, and the owner / super-admin endpoints.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, SessionUser, create_session_token, generate_csrf_token
from app.core.config import settings
from app.core.request_context import clear_current_user, set_current_user
from app.db.tenant import clear_current_company, set_current_company
from app.models.ai_usage import AIUsageEvent
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.user import User
from app.services import ai_suggest, ai_usage
from app.services.ai_suggest import _post_lm_studio as _REAL_POST  # before conftest stubs it
from app.services.ai_usage import (
    AIBudgetExceeded,
    AIRateLimited,
    Usage,
    check_budget,
    cost_micros,
    estimate_usage,
    guard_ai_request,
    metered_llm,
    price_for,
    usage_from_anthropic,
    usage_from_gemini,
    usage_from_openai,
)

PRICES = dict(ai_usage.DEFAULT_PRICING)


# --- fixtures ----------------------------------------------------------------------------

@pytest.fixture()
def company(db):
    c = Company(id=uuid.uuid4(), name="AI Co", slug=f"ai-{uuid.uuid4().hex[:8]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    yield c
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


@pytest.fixture()
def as_user(company):
    """Run code as a user of ``company`` (the contextvars a request sets)."""
    def _set(user_id=None, *, superadmin=False, role="accountant", company_id=None):
        uid = user_id or str(uuid.uuid4())
        set_current_company(str(company_id or company.id))
        set_current_user(SessionUser(user_id=uid, username=f"u-{uid[:4]}", is_admin=False,
                                     company_id=str(company_id or company.id), is_superadmin=superadmin,
                                     role=role))
        return uid
    yield _set
    clear_current_company()
    clear_current_user()


def _event(db, company, *, user_id=None, tokens=1000, hours_ago=0.0, model="gpt-4.1-mini", purpose="chat",
           cost=100):
    e = AIUsageEvent(company_id=company.id, user_id=user_id, username="x", provider="metis", model=model,
                     purpose=purpose, input_tokens=tokens, output_tokens=0, cached_tokens=0,
                     cache_write_tokens=0, estimated=False, cost_micros=cost, outcome="ok", duration_ms=5,
                     created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    db.add(e)
    db.commit()
    return e


def _set_limits(db, **values):
    with __import__("app.db.tenant", fromlist=["tenant_bypass"]).tenant_bypass():
        row = db.execute(select(AppSetting).where(AppSetting.key == "ai_limits",
                                                  AppSetting.company_id.is_(None))).scalars().first()
        if row is None:
            row = AppSetting(key="ai_limits", value="{}", company_id=None)
            db.add(row)
        row.value = json.dumps(values)
        db.commit()


@pytest.fixture(autouse=True)
def _clean_limits(db):
    yield
    from app.db.tenant import tenant_bypass
    from sqlalchemy import delete
    db.rollback()
    with tenant_bypass():
        db.execute(delete(AppSetting).where(AppSetting.key == "ai_limits"))
        db.commit()


def _client_as(client, company, role="owner", *, superadmin=False, user_id=None):
    tok = create_session_token(user_id=user_id or str(uuid.uuid4()), username=f"{role}-u", is_admin=(role == "owner"),
                               company_id=str(company.id) if company else None, role=role,
                               is_superadmin=superadmin)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    from tests.conftest import _CSRFTestClient
    return _CSRFTestClient(client, csrf)


# --- parsing provider usage ----------------------------------------------------------------

def test_openai_usage_with_cached_prompt():
    u = usage_from_openai({"usage": {"prompt_tokens": 1200, "completion_tokens": 300,
                                     "prompt_tokens_details": {"cached_tokens": 1000}}})
    assert (u.input_tokens, u.output_tokens, u.cached_tokens, u.estimated) == (1200, 300, 1000, False)
    assert usage_from_openai({"choices": []}) is None
    assert usage_from_openai(None) is None


def test_gemini_usage_counts_thinking_as_output():
    u = usage_from_gemini({"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 100,
                                              "thoughtsTokenCount": 50, "cachedContentTokenCount": 10}})
    assert (u.input_tokens, u.output_tokens, u.cached_tokens) == (900, 150, 10)
    assert usage_from_gemini({}) is None


def test_anthropic_usage_adds_cache_reads_and_writes_to_the_prompt():
    class U:
        input_tokens, output_tokens = 100, 40
        cache_read_input_tokens, cache_creation_input_tokens = 2000, 500
    u = usage_from_anthropic(U())
    assert (u.input_tokens, u.cached_tokens, u.cache_write_tokens, u.output_tokens) == (2600, 2000, 500, 40)
    assert usage_from_anthropic({"input_tokens": 5, "output_tokens": 7}).total == 12
    assert usage_from_anthropic(None) is None


def test_estimate_when_the_provider_reports_nothing():
    u = estimate_usage("x" * 400, {"text": "y" * 80})
    assert u.estimated and u.input_tokens == 100 and u.output_tokens > 0


# --- prices --------------------------------------------------------------------------------

@pytest.mark.parametrize("model, expected", [
    ("gpt-4.1-mini", (0.40, 1.60, 0.10)),
    ("gpt-4.1-mini-2025-04-14", (0.40, 1.60, 0.10)),      # dated id, longest prefix wins over gpt-4.1
    ("gpt-4.1", (2.00, 8.00, 0.50)),
    ("GPT-4o-mini", (0.15, 0.60, 0.075)),
    ("openai/gpt-4o", (2.50, 10.00, 1.25)),                # gateway vendor prefix
    ("some-unknown-model", None),
])
def test_price_lookup(model, expected):
    assert price_for(model, PRICES) == expected


def test_cost_maths_in_micro_dollars():
    # 1,000 prompt + 500 completion on gpt-4.1-mini: 1000×0.40 + 500×1.60
    assert cost_micros(Usage(1000, 500), "gpt-4.1-mini", "metis", PRICES) == 1200
    # 400 of the prompt cached: 600×0.40 + 400×0.10 + 500×1.60
    assert cost_micros(Usage(1000, 500, cached_tokens=400), "gpt-4.1-mini", "metis", PRICES) == 1080
    # Anthropic cache write at 1.25× input: 100×3 + 500×3×1.25 + 40×15
    assert cost_micros(Usage(600, 40, cache_write_tokens=500), "claude-sonnet-4-5", "anthropic", PRICES) == 2775
    assert cost_micros(Usage(10_000, 10_000), "qwen3", "lmstudio", PRICES) == 0   # local model
    assert cost_micros(Usage(10, 10), "mystery", "metis", PRICES) is None         # unpriced


def test_settings_merge_over_defaults_and_ignore_bad_values(db):
    assert ai_usage.load_settings(db)["user_daily_tokens"] == ai_usage.DEFAULT_LIMITS["user_daily_tokens"]
    _set_limits(db, user_daily_tokens=5, company_daily_tokens=-3,
                pricing={"my-model": [1, 2], "gpt-4o": [9, 9, 9], "broken": "x"})
    cur = ai_usage.load_settings(db)
    assert cur["user_daily_tokens"] == 5
    assert cur["company_daily_tokens"] == ai_usage.DEFAULT_LIMITS["company_daily_tokens"]  # negative → default
    assert cur["pricing"]["my-model"] == (1.0, 2.0, 1.0)      # cached defaults to the input price
    assert cur["pricing"]["gpt-4o"] == (9.0, 9.0, 9.0)
    assert "broken" not in cur["pricing"]


def test_platform_limits_are_found_from_inside_a_company_request(db, company, as_user):
    """The platform row has no company; a tenant-scoped read would never see it."""
    _set_limits(db, user_daily_tokens=7)
    as_user()
    assert ai_usage.load_settings()["user_daily_tokens"] == 7


# --- metering --------------------------------------------------------------------------------

async def test_metered_call_writes_a_ledger_row(db, company, as_user):
    uid = as_user()
    from app.core.observability import request_id_var
    token = request_id_var.set("req-metered-1")
    try:
        async with metered_llm("metis", "gpt-4.1-mini", "chat") as meter:
            meter.ok(Usage(1000, 500))
    finally:
        request_id_var.reset(token)
    row = db.execute(select(AIUsageEvent).where(AIUsageEvent.request_id == "req-metered-1")).scalars().one()
    assert row.company_id == company.id and row.user_id == uid
    assert (row.provider, row.model, row.purpose, row.outcome) == ("metis", "gpt-4.1-mini", "chat", "ok")
    assert (row.input_tokens, row.output_tokens, row.cost_micros, row.estimated) == (1000, 500, 1200, False)


async def test_failed_and_retried_calls_are_errors_with_no_tokens(db, company, as_user):
    as_user()
    with pytest.raises(RuntimeError):
        async with metered_llm("metis", "gpt-4o", "ocr"):
            raise RuntimeError("provider down")
    async with metered_llm("metis", "gpt-4o", "ocr"):
        pass  # e.g. a 429 the caller retries: never marked ok
    rows = db.execute(select(AIUsageEvent).where(AIUsageEvent.company_id == company.id)).scalars().all()
    assert len(rows) == 2 and all(r.outcome == "error" and r.input_tokens == 0 and r.cost_micros == 0 for r in rows)


async def test_a_background_job_is_metered_without_a_company(db):
    clear_current_company()
    clear_current_user()
    async with metered_llm("lmstudio", "qwen3", "categorize") as meter:
        meter.ok(None, prompt="p" * 40, output="o" * 8)
    row = db.execute(select(AIUsageEvent).where(AIUsageEvent.model == "qwen3").order_by(
        AIUsageEvent.created_at.desc())).scalars().first()
    assert row.company_id is None and row.user_id is None and row.estimated is True and row.cost_micros == 0
    db.delete(row)
    db.commit()


async def test_recording_never_breaks_the_reply(company, as_user, monkeypatch):
    as_user()
    def _broken():
        raise RuntimeError("database is gone")
    monkeypatch.setattr(ai_usage, "_session_factory", lambda: _broken)
    monkeypatch.setattr(ai_usage, "check_budget", lambda *a, **k: None)
    async with metered_llm("metis", "gpt-4o", "chat") as meter:
        meter.ok(Usage(1, 1))  # no exception escapes


async def test_the_real_openai_post_is_metered_with_its_purpose(db, company, as_user, monkeypatch):
    """ai_suggest._post_lm_studio end to end against a mock provider."""
    uid = as_user()
    monkeypatch.setattr(ai_suggest, "_post_lm_studio", _REAL_POST)
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 2000, "completion_tokens": 100}})

    monkeypatch.setattr(ai_suggest.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    body = await ai_suggest._post_lm_studio("http://ai.test/v1/chat/completions",
                                            {"model": "gpt-4o-mini", "messages": []}, "http://ai.test",
                                            purpose="categorize")
    assert body["usage"]["prompt_tokens"] == 2000
    row = db.execute(select(AIUsageEvent).where(AIUsageEvent.company_id == company.id)).scalars().one()
    assert (row.purpose, row.model, row.user_id, row.input_tokens, row.output_tokens) == \
        ("categorize", "gpt-4o-mini", uid, 2000, 100)
    assert row.cost_micros == 2000 * 0.15 + 100 * 0.60   # 360


# --- budgets -----------------------------------------------------------------------------------

def test_user_over_budget_is_refused_but_others_are_not(db, company, as_user):
    _set_limits(db, user_daily_tokens=5000, company_daily_tokens=0)
    heavy = str(uuid.uuid4())
    _event(db, company, user_id=heavy, tokens=6000)
    as_user(heavy)
    with pytest.raises(AIBudgetExceeded) as ex:
        check_budget()
    assert ex.value.scope == "user" and ex.value.used == 6000 and ex.value.limit == 5000
    assert "owner" in str(ex.value)
    as_user()  # a colleague
    check_budget()


def test_company_budget_covers_everyone(db, company, as_user):
    _set_limits(db, company_daily_tokens=10_000, user_daily_tokens=0)
    _event(db, company, user_id="a", tokens=6000)
    _event(db, company, user_id="b", tokens=5000)
    as_user()
    with pytest.raises(AIBudgetExceeded) as ex:
        check_budget()
    assert ex.value.scope == "company" and ex.value.used == 11_000
    assert ex.value.retry_after_seconds > 23 * 3600


def test_the_window_is_the_last_24_hours(db, company, as_user):
    _set_limits(db, user_daily_tokens=5000)
    uid = str(uuid.uuid4())
    _event(db, company, user_id=uid, tokens=9000, hours_ago=25)   # yesterday's, no longer counted
    _event(db, company, user_id=uid, tokens=4000, hours_ago=1)
    as_user(uid)
    check_budget()


def test_company_overrides_and_zero_means_unlimited(db, company, as_user):
    _set_limits(db, user_daily_tokens=1000, company_daily_tokens=1000)
    uid = str(uuid.uuid4())
    _event(db, company, user_id=uid, tokens=50_000)
    company.ai_daily_token_budget = 0          # unlimited for this company
    company.ai_user_daily_token_budget = 100_000
    db.commit()
    as_user(uid)
    check_budget()
    company.ai_user_daily_token_budget = 40_000
    db.commit()
    with pytest.raises(AIBudgetExceeded):
        check_budget()


def test_super_admins_are_metered_but_never_limited(db, company, as_user):
    _set_limits(db, user_daily_tokens=1, company_daily_tokens=1)
    _event(db, company, tokens=10)
    as_user(superadmin=True)
    check_budget()
    guard_ai_request(db)


async def test_a_long_turn_stops_at_the_budget_mid_way(db, company, as_user):
    _set_limits(db, user_daily_tokens=1500)
    as_user()
    async with metered_llm("metis", "gpt-4.1-mini", "chat") as meter:
        meter.ok(Usage(1000, 600))
    with pytest.raises(AIBudgetExceeded):
        async with metered_llm("metis", "gpt-4.1-mini", "chat"):
            pytest.fail("the provider must not be called past the budget")


# --- per-minute request limits -------------------------------------------------------------------

def test_user_request_limit(db, company, as_user):
    _set_limits(db, user_requests_per_minute=2, company_requests_per_minute=0)
    as_user()
    guard_ai_request(db)
    guard_ai_request(db)
    with pytest.raises(AIRateLimited) as ex:
        guard_ai_request(db)
    assert ex.value.scope == "user"
    as_user()  # someone else still gets through
    guard_ai_request(db)


def test_company_request_limit(db, company, as_user):
    _set_limits(db, user_requests_per_minute=0, company_requests_per_minute=2)
    for _ in range(2):
        as_user()
        guard_ai_request(db)
    as_user()
    with pytest.raises(AIRateLimited) as ex:
        guard_ai_request(db)
    assert ex.value.scope == "company"


def test_a_refused_request_does_not_use_a_slot(db, company, as_user):
    _set_limits(db, user_requests_per_minute=1, user_daily_tokens=100)
    uid = str(uuid.uuid4())
    _event(db, company, user_id=uid, tokens=500)
    as_user(uid)
    with pytest.raises(AIBudgetExceeded):
        guard_ai_request(db)
    company.ai_user_daily_token_budget = 0
    db.commit()
    guard_ai_request(db)  # the budget refusal did not burn the one slot


# --- HTTP ------------------------------------------------------------------------------------------

def test_endpoints_answer_429_with_a_reason(db, company, client):
    _set_limits(db, user_daily_tokens=100)
    uid = str(uuid.uuid4())
    _event(db, company, user_id=uid, tokens=500)
    api = _client_as(client, company, role="accountant", user_id=uid)
    r = api.post("/transactions/suggest", json={"user_message": "paid rent 500"})
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "ai_budget_exceeded" and body["scope"] == "user"
    assert "24 hours" in body["detail"] and r.headers["retry-after"]
    r = api.post("/ai-accountant/chat", json={"message": "what is my cash?"})
    assert r.status_code == 429 and r.json()["code"] == "ai_budget_exceeded"


def test_the_classic_chat_explains_in_the_conversation(db, company, client):
    _set_limits(db, user_requests_per_minute=1)
    uid = str(uuid.uuid4())
    api = _client_as(client, company, role="accountant", user_id=uid)
    from app.core.shared_state import DbRateLimiter
    DbRateLimiter("ai_user", max_requests=1, window_seconds=60).hit(db, uid)
    r = api.post("/transactions/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and "too quickly" in r.json()["message"]


def test_rate_limit_answers_429_on_other_endpoints(db, company, client):
    _set_limits(db, user_requests_per_minute=1)
    uid = str(uuid.uuid4())
    api = _client_as(client, company, role="accountant", user_id=uid)
    from app.core.shared_state import DbRateLimiter
    DbRateLimiter("ai_user", max_requests=1, window_seconds=60).hit(db, uid)
    r = api.post("/transactions/suggest", json={"user_message": "paid rent"})
    assert r.status_code == 429 and r.json()["code"] == "ai_rate_limited"


# --- owner view and per-user budget ------------------------------------------------------------------

def test_owner_sees_usage_per_user_purpose_and_model(db, company, client):
    alice = User(username=f"alice-{uuid.uuid4().hex[:5]}", password_hash="x", password_salt="x", role="accountant",
                 company_id=company.id, is_active=True)
    db.add(alice)
    db.commit()
    _event(db, company, user_id=str(alice.id), tokens=3000, purpose="chat", model="gpt-4.1-mini", cost=1200)
    _event(db, company, user_id=str(alice.id), tokens=2000, purpose="ocr", model="gemini-3.7-flash", cost=None)
    _event(db, company, user_id=str(alice.id), tokens=9999, hours_ago=24 * 40)   # outside 30 days
    api = _client_as(client, company)
    r = api.get("/admin/ai-usage")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["company"]["tokens_24h"] == 5000
    row = next(u for u in d["users"] if u["user_id"] == str(alice.id))
    assert row["username"] == alice.username and row["tokens_24h"] == 5000 and row["requests_period"] == 2
    assert row["cost_usd_period"] == 0.0012
    purposes = {p["key"]: p for p in d["by_purpose"]}
    assert purposes["chat"]["tokens"] == 3000 and purposes["ocr"]["unpriced_calls"] == 1
    assert {m["key"] for m in d["by_model"]} == {"gpt-4.1-mini", "gemini-3.7-flash"}
    assert sum(x["tokens"] for x in d["daily"]) == 5000
    assert d["user_budget_is_default"] is True


def test_owner_sets_the_per_user_budget_and_it_is_audited(db, company, client):
    api = _client_as(client, company)
    r = api.put("/admin/ai-usage/user-budget", json={"user_daily_tokens": 250_000})
    assert r.status_code == 200 and r.json()["user_budget"] == 250_000 and r.json()["user_budget_is_default"] is False
    db.refresh(company)
    assert company.ai_user_daily_token_budget == 250_000
    row = db.execute(select(AuditLog).where(AuditLog.entity_type == "ai_budget",
                                            AuditLog.entity_id == str(company.id))).scalars().first()
    assert row is not None and json.loads(row.detail)["user_daily_tokens"] == 250_000
    assert api.put("/admin/ai-usage/user-budget", json={"user_daily_tokens": None}).json()["user_budget_is_default"]
    assert api.put("/admin/ai-usage/user-budget", json={"user_daily_tokens": -1}).status_code == 422


def test_who_may_see_and_change_usage(db, company, client):
    assert _client_as(client, company, role="cfo").get("/admin/ai-usage").status_code == 200
    assert _client_as(client, company, role="cfo").put("/admin/ai-usage/user-budget",
                                                       json={"user_daily_tokens": 1}).status_code == 403
    assert _client_as(client, company, role="accountant").get("/admin/ai-usage").status_code == 403
    assert _client_as(client, company, role="owner").get("/admin/ai-limits").status_code == 403


def test_usage_never_crosses_companies(db, company, client):
    other = Company(id=uuid.uuid4(), name="Other", slug=f"o-{uuid.uuid4().hex[:8]}", locale="uk",
                    base_currency="GBP", status="active", token_version=0)
    db.add(other)
    db.commit()
    try:
        _event(db, other, user_id="z", tokens=77_777)
        d = _client_as(client, company).get("/admin/ai-usage").json()
        assert d["company"]["tokens_24h"] == 0 and all(u["user_id"] != "z" for u in d["users"])
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(other.id))


# --- super-admin: platform limits and company budgets ------------------------------------------------

def test_super_admin_edits_platform_limits_as_a_platform_row(db, company, client):
    api = _client_as(client, company, superadmin=True)  # a super-admin who also sits in a company
    body = {"company_daily_tokens": 3_000_000, "user_daily_tokens": 400_000, "user_requests_per_minute": 9,
            "company_requests_per_minute": 30, "pricing": {"gemini-3.7-flash": [0.3, 2.5, 0.075], "My-Model": [1, 2]}}
    r = api.put("/admin/ai-limits", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user_requests_per_minute"] == 9 and d["pricing"]["gemini-3.7-flash"] == [0.3, 2.5, 0.075]
    assert d["pricing"]["my-model"] == [1.0, 2.0, 1.0]
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        rows = db.execute(select(AppSetting).where(AppSetting.key == "ai_limits")).scalars().all()
    assert len(rows) == 1 and rows[0].company_id is None
    # saving again updates the same row
    assert api.put("/admin/ai-limits", json={**body, "user_requests_per_minute": 10}).status_code == 200
    with tenant_bypass():
        assert db.execute(select(AppSetting).where(AppSetting.key == "ai_limits")).scalars().all().__len__() == 1


@pytest.mark.parametrize("pricing", [{"x": [1]}, {"x": [1, 2, 3, 4]}, {"x": [-1, 2]}, {"": [1, 2]}])
def test_bad_prices_are_refused(db, company, client, pricing):
    api = _client_as(client, company, superadmin=True)
    body = {"company_daily_tokens": 1, "user_daily_tokens": 1, "user_requests_per_minute": 1,
            "company_requests_per_minute": 1, "pricing": pricing}
    assert api.put("/admin/ai-limits", json=body).status_code == 422


def test_super_admin_sets_a_company_budget(db, company, client):
    _event(db, company, tokens=1234)
    api = _client_as(client, company, superadmin=True)
    r = api.put(f"/admin/companies/{company.id}/ai-budget", json={"daily_tokens": 50_000})
    assert r.status_code == 200, r.text
    assert r.json()["budget"] == 50_000 and r.json()["budget_is_default"] is False
    listed = {c["company_id"]: c for c in api.get("/admin/companies/ai-usage").json()}
    assert listed[str(company.id)]["tokens_24h"] == 1234
    db.refresh(company)
    assert company.ai_daily_token_budget == 50_000
    assert api.put(f"/admin/companies/{uuid.uuid4()}/ai-budget", json={"daily_tokens": 1}).status_code == 404
    assert _client_as(client, company).put(f"/admin/companies/{company.id}/ai-budget",
                                           json={"daily_tokens": 1}).status_code == 403


def test_ai_limits_is_a_platform_setting_the_boot_fold_skips():
    from app.models.app_setting import PLATFORM_SETTING_KEYS
    assert "ai_limits" in PLATFORM_SETTING_KEYS


# --- owner notification near the allowance -------------------------------------------------------

def _ai_notes(db, company):
    from app.db.tenant import use_company
    from app.models.notification import Notification
    with use_company(company.id):
        return db.execute(select(Notification).where(Notification.kind == "ai_budget",
                                                     Notification.dismissed_at.is_(None))).scalars().all()


def test_owner_is_warned_at_80_percent_and_alerted_when_used_up(db, company):
    from datetime import date
    from app.db.tenant import use_company
    from app.services.notification_service import KIND_ROLES, refresh_notifications
    company.ai_daily_token_budget = 10_000
    db.commit()
    today = date.today()
    _event(db, company, tokens=7_000)
    with use_company(company.id):
        refresh_notifications(db, today=today)
        db.commit()
    assert _ai_notes(db, company) == []                       # 70 %: quiet
    _event(db, company, tokens=1_500)
    with use_company(company.id):
        refresh_notifications(db, today=today)
        db.commit()
    [note] = _ai_notes(db, company)
    assert note.level == "warning" and "85%" in note.title and note.link_page == "settings"
    _event(db, company, tokens=2_000)
    with use_company(company.id):
        refresh_notifications(db, today=today)
        db.commit()
    [note] = _ai_notes(db, company)
    assert note.level == "high" and "used up" in note.title
    assert KIND_ROLES["ai_budget"] == ("owner", "cfo")


def test_no_warning_when_the_company_is_unlimited(db, company):
    from datetime import date
    from app.db.tenant import use_company
    from app.services.notification_service import refresh_notifications
    company.ai_daily_token_budget = 0
    db.commit()
    _event(db, company, tokens=50_000_000)
    with use_company(company.id):
        refresh_notifications(db, today=date.today())
        db.commit()
    assert _ai_notes(db, company) == []
