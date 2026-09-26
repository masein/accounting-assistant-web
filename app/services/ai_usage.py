"""AI usage: metering, budgets and per-user limits (roadmap 2026-09 §2.5).

Every provider call goes through ``metered_llm`` (five call sites: the
OpenAI-compatible post in ai_suggest, the AI accountant's OpenAI and Anthropic
clients, and the two OCR vision calls). It

* refuses the call when the caller's company or the caller is over its token
  budget for the last 24 hours (``AIBudgetExceeded``);
* times the call and counts it in Prometheus (``aa_llm_*``);
* writes one ``ai_usage_events`` row with the provider's token counts (or an
  estimate from the text length when the provider sends none) and an
  estimated cost from the price table.

``guard_ai_request`` runs at the top of each AI endpoint: per-user and
per-company requests per minute, then the same budget check, so a user gets a
clear answer before any work starts. Super-admins are metered but never
limited; background jobs (no user, no company) are metered only.

Limits live in the platform setting ``ai_limits`` (super-admin), with
per-company overrides on ``companies``. 0 means unlimited everywhere.
"""
from __future__ import annotations

import json
import logging
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger("app.ai_usage")

LIMITS_KEY = "ai_limits"
WINDOW = timedelta(hours=24)

DEFAULT_LIMITS: dict[str, int] = {
    # tokens (prompt + completion) over any 24 hours
    "company_daily_tokens": 10_000_000,
    "user_daily_tokens": 2_000_000,
    # AI requests (a chat message, an OCR, a suggestion) per minute
    "user_requests_per_minute": 20,
    "company_requests_per_minute": 60,
}

# USD per million tokens: (input, output, cached input). Public list prices,
# longest-prefix match on the model id. Gateways such as Metis bill in their
# own currency and rates, so every figure is an estimate; the super-admin can
# override or add models under "pricing" in the ai_limits setting. A model
# with no entry is recorded as unpriced (tokens still count to budgets).
DEFAULT_PRICING: dict[str, tuple[float, float, float]] = {
    "gpt-4.1-nano": (0.10, 0.40, 0.025),
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4o": (2.50, 10.00, 1.25),
    "gemini-2.5-flash": (0.30, 2.50, 0.075),
    "gemini-2.5-pro": (1.25, 10.00, 0.31),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
# A local model costs nothing.
FREE_PROVIDERS = frozenset({"lmstudio"})


class AIBudgetExceeded(RuntimeError):
    """The caller's company or the caller used its token budget."""

    def __init__(self, scope: str, used: int, limit: int, retry_after_seconds: int):
        self.scope = scope
        self.used = used
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        who = "Your company has" if scope == "company" else "You have"
        hours = max(1, math.ceil(retry_after_seconds / 3600))
        super().__init__(
            f"{who} used its AI allowance for the last 24 hours ({used:,} of {limit:,} tokens). "
            f"It frees up gradually; try again in about {hours} hour(s), or ask "
            + ("the platform administrator to raise the company budget." if scope == "company"
               else "the company owner to raise your daily AI budget (Settings → AI usage).")
        )


class AIRateLimited(RuntimeError):
    def __init__(self, scope: str):
        self.scope = scope
        super().__init__(
            "You're sending AI requests too quickly. Please wait a moment and try again."
            if scope == "user" else
            "Your company is sending AI requests too quickly. Please wait a moment and try again."
        )


# --- sessions (tests point this at their own database) ----------------------------

def _session_factory():
    from app.db.session import SessionLocal
    return SessionLocal


# --- limits and prices -----------------------------------------------------------

def load_settings(db=None) -> dict[str, Any]:
    """Platform limits merged over the defaults, plus the price table."""
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting

    own = db is None
    db = db or _session_factory()()
    try:
        with tenant_bypass():  # the platform row, whoever is asking
            row = db.execute(select(AppSetting).where(
                AppSetting.key == LIMITS_KEY, AppSetting.company_id.is_(None))).scalars().first()
        raw: dict[str, Any] = {}
        if row is not None and row.value:
            try:
                raw = json.loads(row.value)
            except ValueError:
                logger.warning("ai_limits setting is not valid JSON; using defaults")
    finally:
        if own:
            db.close()
    limits = {k: _nonneg_int(raw.get(k), v) for k, v in DEFAULT_LIMITS.items()}
    pricing = {k: tuple(v) for k, v in DEFAULT_PRICING.items()}
    for model, price in (raw.get("pricing") or {}).items():
        try:
            pin, pout, *rest = [float(x) for x in price]
            pricing[str(model).strip().lower()] = (pin, pout, float(rest[0]) if rest else pin)
        except (TypeError, ValueError):
            logger.warning("ai_limits pricing for %r is malformed; ignored", model)
    return {**limits, "pricing": pricing}


def _nonneg_int(value, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def price_for(model: str, pricing: dict[str, tuple[float, float, float]]) -> tuple[float, float, float] | None:
    name = (model or "").strip().lower()
    if "/" in name:  # "openai/gpt-4o" on some gateways
        name = name.split("/", 1)[1]
    best = None
    for prefix, price in pricing.items():
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, price)
    return best[1] if best else None


def cost_micros(usage: "Usage", model: str, provider: str, pricing) -> int | None:
    """Millionths of a dollar. Uncached prompt at the input rate, cache reads
    at the cached rate, cache writes at 1.25× input (Anthropic), completion at
    the output rate."""
    if provider in FREE_PROVIDERS:
        return 0
    price = price_for(model, pricing)
    if price is None:
        return None
    pin, pout, pcached = price
    fresh = max(0, usage.input_tokens - usage.cached_tokens - usage.cache_write_tokens)
    dollars_per_million = (fresh * pin + usage.cached_tokens * pcached
                           + usage.cache_write_tokens * pin * 1.25 + usage.output_tokens * pout)
    return int(round(dollars_per_million))  # $/M tokens × tokens = micro-dollars


# --- usage parsing -------------------------------------------------------------------

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = False

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def _i(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def usage_from_openai(body: dict | None) -> Usage | None:
    u = (body or {}).get("usage") or None
    if not isinstance(u, dict) or ("prompt_tokens" not in u and "completion_tokens" not in u):
        return None
    return Usage(input_tokens=_i(u.get("prompt_tokens")), output_tokens=_i(u.get("completion_tokens")),
                 cached_tokens=_i((u.get("prompt_tokens_details") or {}).get("cached_tokens")))


def usage_from_gemini(body: dict | None) -> Usage | None:
    u = (body or {}).get("usageMetadata") or None
    if not isinstance(u, dict):
        return None
    # thinking tokens are billed as output
    return Usage(input_tokens=_i(u.get("promptTokenCount")),
                 output_tokens=_i(u.get("candidatesTokenCount")) + _i(u.get("thoughtsTokenCount")),
                 cached_tokens=_i(u.get("cachedContentTokenCount")))


def usage_from_anthropic(u) -> Usage | None:
    if u is None:
        return None
    get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    read, write = _i(get("cache_read_input_tokens")), _i(get("cache_creation_input_tokens"))
    # Anthropic's input_tokens excludes cache reads and writes; store the whole prompt
    return Usage(input_tokens=_i(get("input_tokens")) + read + write, output_tokens=_i(get("output_tokens")),
                 cached_tokens=read, cache_write_tokens=write)


def estimate_usage(prompt: Any, output: Any) -> Usage:
    """About four characters a token — only when the provider reports nothing."""
    def chars(x) -> int:
        if x is None:
            return 0
        return len(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str))
    return Usage(input_tokens=math.ceil(chars(prompt) / 4), output_tokens=math.ceil(chars(output) / 4),
                 estimated=True)


# --- who is calling ---------------------------------------------------------------------

@dataclass
class _Caller:
    company_id: str | None
    user_id: str | None
    username: str | None
    exempt: bool


def _caller() -> _Caller:
    from app.core.request_context import get_current_actor
    from app.db.tenant import get_current_company
    actor = get_current_actor()
    return _Caller(
        company_id=get_current_company(),
        user_id=getattr(actor, "user_id", None),
        username=getattr(actor, "username", None),
        exempt=bool(getattr(actor, "is_superadmin", False)),
    )


def _uuid(v):
    import uuid
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def effective_budgets(db, company_id, limits: dict[str, Any]) -> tuple[int, int]:
    """(company budget, per-user budget) in tokens per 24 h; 0 = unlimited."""
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    company_budget, user_budget = limits["company_daily_tokens"], limits["user_daily_tokens"]
    cid = _uuid(company_id)
    if cid is not None:
        with tenant_bypass():
            row = db.get(Company, cid)
        if row is not None:
            if row.ai_daily_token_budget is not None:
                company_budget = int(row.ai_daily_token_budget)
            if row.ai_user_daily_token_budget is not None:
                user_budget = int(row.ai_user_daily_token_budget)
    return company_budget, user_budget


def _used_since(db, since: datetime, *, company_id=None, user_id=None) -> tuple[int, datetime | None]:
    from app.models.ai_usage import AIUsageEvent
    q = select(func.coalesce(func.sum(AIUsageEvent.input_tokens + AIUsageEvent.output_tokens), 0),
               func.min(AIUsageEvent.created_at)).where(AIUsageEvent.created_at > since)
    if company_id is not None:
        q = q.where(AIUsageEvent.company_id == company_id)
    if user_id is not None:
        q = q.where(AIUsageEvent.user_id == str(user_id))
    total, oldest = db.execute(q).one()
    return int(total or 0), oldest


def _retry_after(oldest: datetime | None, now: datetime) -> int:
    if oldest is None:
        return 3600
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return max(60, int((oldest + WINDOW - now).total_seconds()))


def check_budget(db=None, caller: _Caller | None = None) -> None:
    """Raise AIBudgetExceeded when the caller's company or the caller has used
    its 24-hour token budget. Super-admins and context-free jobs pass."""
    caller = caller or _caller()
    if caller.exempt or (caller.company_id is None and caller.user_id is None):
        return
    own = db is None
    db = db or _session_factory()()
    try:
        limits = load_settings(db)
        company_budget, user_budget = effective_budgets(db, caller.company_id, limits)
        now = _now()
        since = now - WINDOW
        cid = _uuid(caller.company_id)
        if company_budget and cid is not None:
            used, oldest = _used_since(db, since, company_id=cid)
            if used >= company_budget:
                raise AIBudgetExceeded("company", used, company_budget, _retry_after(oldest, now))
        if user_budget and caller.user_id:
            used, oldest = _used_since(db, since, company_id=cid, user_id=caller.user_id)
            if used >= user_budget:
                raise AIBudgetExceeded("user", used, user_budget, _retry_after(oldest, now))
    finally:
        if own:
            db.close()


def guard_ai_request(db) -> None:
    """Top of every AI endpoint: requests per minute (user, then company), then
    the token budget. Raises AIRateLimited / AIBudgetExceeded. Uses the
    request's session; the rate buckets commit it, so call this first."""
    from app.core.shared_state import DbRateLimiter
    caller = _caller()
    if caller.exempt:
        return
    limits = load_settings(db)
    per_user, per_company = limits["user_requests_per_minute"], limits["company_requests_per_minute"]
    if per_user and caller.user_id:
        user_bucket = DbRateLimiter("ai_user", max_requests=per_user, window_seconds=60)
        if not user_bucket.would_allow(db, str(caller.user_id)):
            raise AIRateLimited("user")
    if per_company and caller.company_id:
        company_bucket = DbRateLimiter("ai_company", max_requests=per_company, window_seconds=60)
        if not company_bucket.would_allow(db, str(caller.company_id)):
            raise AIRateLimited("company")
    check_budget(db, caller)
    if per_user and caller.user_id:
        DbRateLimiter("ai_user", max_requests=per_user, window_seconds=60).hit(db, str(caller.user_id))
    if per_company and caller.company_id:
        DbRateLimiter("ai_company", max_requests=per_company, window_seconds=60).hit(db, str(caller.company_id))


# --- metering ----------------------------------------------------------------------------

@dataclass
class Meter:
    provider: str
    model: str
    purpose: str
    usage: Usage | None = None
    failed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def ok(self, usage: Usage | None, *, prompt: Any = None, output: Any = None) -> None:
        """The call succeeded; ``usage`` from the provider, else an estimate."""
        self.usage = usage if usage is not None else estimate_usage(prompt, output)

    def error(self) -> None:
        self.failed = True


def record(meter: Meter, duration_ms: int, caller: _Caller | None = None) -> None:
    """Write the ledger row. Never raises: metering must not break a reply."""
    from app.core.observability import current_request_id
    from app.models.ai_usage import AIUsageEvent
    caller = caller or _caller()
    usage = meter.usage if (meter.usage is not None and not meter.failed) else Usage()
    try:
        s = _session_factory()()
        try:
            pricing = load_settings(s)["pricing"]
            s.add(AIUsageEvent(
                company_id=_uuid(caller.company_id), user_id=(str(caller.user_id)[:64] if caller.user_id else None),
                username=(caller.username or None), provider=(meter.provider or "unknown")[:32],
                model=(meter.model or "unknown")[:128], purpose=meter.purpose[:24],
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens, cache_write_tokens=usage.cache_write_tokens,
                estimated=usage.estimated,
                cost_micros=(cost_micros(usage, meter.model, meter.provider, pricing)
                             if meter.usage is not None and not meter.failed else 0),
                outcome="error" if (meter.failed or meter.usage is None) else "ok",
                duration_ms=int(duration_ms), request_id=(current_request_id() or None),
            ))
            s.commit()
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        logger.warning("could not record AI usage", exc_info=True)


@asynccontextmanager
async def metered_llm(provider: str, model: str, purpose: str, *, check: bool = True):
    """Wrap one provider request. The caller marks success with
    ``meter.ok(usage)``; leaving without it (an exception, a 429 retried, a
    5xx) records an error with no tokens."""
    from app.core import observability as obs
    caller = _caller()
    if check:
        check_budget(caller=caller)
    meter = Meter(provider=provider or "unknown", model=model or "unknown", purpose=purpose)
    started = time.perf_counter()
    try:
        yield meter
    except BaseException:
        meter.failed = True
        raise
    finally:
        seconds = time.perf_counter() - started
        ok = meter.usage is not None and not meter.failed
        if obs._PROM:
            obs.LLM_CALLS.labels(meter.provider, purpose, "ok" if ok else "error").inc()
            obs.LLM_LATENCY.labels(meter.provider, purpose).observe(seconds)
            if ok:
                obs.LLM_TOKENS.labels(meter.provider, purpose, "input").inc(meter.usage.input_tokens)
                obs.LLM_TOKENS.labels(meter.provider, purpose, "output").inc(meter.usage.output_tokens)
        record(meter, int(seconds * 1000), caller)


# --- reporting ------------------------------------------------------------------------------

def company_summary(db, company_id, *, days: int = 30) -> dict[str, Any]:
    """What an owner sees: the last 24 hours against the budgets, per user,
    and the last ``days`` by day, purpose and model with estimated cost."""
    from app.models.ai_usage import AIUsageEvent
    from app.models.user import User
    cid = _uuid(company_id)
    limits = load_settings(db)
    company_budget, user_budget = effective_budgets(db, cid, limits)
    now = _now()
    day_ago, since = now - WINDOW, now - timedelta(days=days)
    base = [AIUsageEvent.company_id == cid]
    tokens = AIUsageEvent.input_tokens + AIUsageEvent.output_tokens

    used_24h = int(db.execute(select(func.coalesce(func.sum(tokens), 0)).where(
        *base, AIUsageEvent.created_at > day_ago)).scalar() or 0)

    def grouped(col, since_at):
        rows = db.execute(select(col, func.coalesce(func.sum(tokens), 0), func.count(AIUsageEvent.id),
                                 func.coalesce(func.sum(AIUsageEvent.cost_micros), 0),
                                 func.count(AIUsageEvent.cost_micros))
                          .where(*base, AIUsageEvent.created_at > since_at).group_by(col)).all()
        return rows

    per_user_24h = {r[0]: int(r[1]) for r in grouped(AIUsageEvent.user_id, day_ago)}
    per_user_30d = {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in grouped(AIUsageEvent.user_id, since)}
    names = {str(u.id): u.username for u in db.execute(select(User).where(User.company_id == cid)).scalars()}
    user_ids = set(names) | {k for k in per_user_30d if k}
    users = sorted(({
        "user_id": uid, "username": names.get(uid) or uid,
        "tokens_24h": per_user_24h.get(uid, 0),
        "tokens_period": per_user_30d.get(uid, (0, 0, 0))[0],
        "requests_period": per_user_30d.get(uid, (0, 0, 0))[1],
        "cost_usd_period": round(per_user_30d.get(uid, (0, 0, 0))[2] / 1_000_000, 4),
    } for uid in user_ids), key=lambda u: (-u["tokens_24h"], -u["tokens_period"], u["username"]))

    def breakdown(col):
        return [{"key": r[0] or "—", "tokens": int(r[1]), "calls": int(r[2]),
                 "cost_usd": round(int(r[3]) / 1_000_000, 4), "unpriced_calls": int(r[2]) - int(r[4])}
                for r in sorted(grouped(col, since), key=lambda r: -int(r[1]))]

    day = func.date(AIUsageEvent.created_at)
    daily = [{"date": str(r[0]), "tokens": int(r[1]), "cost_usd": round(int(r[2]) / 1_000_000, 4)}
             for r in db.execute(select(day, func.coalesce(func.sum(tokens), 0),
                                        func.coalesce(func.sum(AIUsageEvent.cost_micros), 0))
                                 .where(*base, AIUsageEvent.created_at > since).group_by(day).order_by(day)).all()]
    total_cost = sum(d["cost_usd"] for d in daily)
    return {
        "window_hours": 24,
        "company": {"tokens_24h": used_24h, "budget": company_budget,
                    "remaining": (max(0, company_budget - used_24h) if company_budget else None)},
        "user_budget": user_budget,
        "user_budget_is_default": _company_user_budget(db, cid) is None,
        "requests_per_minute": {"user": limits["user_requests_per_minute"],
                                "company": limits["company_requests_per_minute"]},
        "users": users,
        "by_purpose": breakdown(AIUsageEvent.purpose),
        "by_model": breakdown(AIUsageEvent.model),
        "daily": daily,
        "period_days": days,
        "cost_usd_period": round(total_cost, 4),
        "cost_note": "Estimated from list prices; your provider's bill is the source of truth.",
    }


def _company_user_budget(db, cid):
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    with tenant_bypass():
        row = db.get(Company, cid)
    return None if row is None else row.ai_user_daily_token_budget


def platform_summary(db) -> list[dict[str, Any]]:
    """Per company, for the super-admin console."""
    from app.db.tenant import tenant_bypass
    from app.models.ai_usage import AIUsageEvent
    from app.models.company import Company
    limits = load_settings(db)
    now = _now()
    tokens = AIUsageEvent.input_tokens + AIUsageEvent.output_tokens
    used = dict(db.execute(select(AIUsageEvent.company_id, func.coalesce(func.sum(tokens), 0))
                           .where(AIUsageEvent.created_at > now - WINDOW)
                           .group_by(AIUsageEvent.company_id)).all())
    month = {r[0]: (int(r[1]), int(r[2])) for r in db.execute(
        select(AIUsageEvent.company_id, func.coalesce(func.sum(tokens), 0),
               func.coalesce(func.sum(AIUsageEvent.cost_micros), 0))
        .where(AIUsageEvent.created_at > now - timedelta(days=30)).group_by(AIUsageEvent.company_id)).all()}
    with tenant_bypass():
        companies = db.execute(select(Company).order_by(Company.name)).scalars().all()
    out = []
    for c in companies:
        budget = c.ai_daily_token_budget if c.ai_daily_token_budget is not None else limits["company_daily_tokens"]
        out.append({
            "company_id": str(c.id), "name": c.name,
            "tokens_24h": int(used.get(c.id, 0)), "budget": int(budget),
            "budget_is_default": c.ai_daily_token_budget is None,
            "tokens_30d": month.get(c.id, (0, 0))[0],
            "cost_usd_30d": round(month.get(c.id, (0, 0))[1] / 1_000_000, 4),
        })
    return out
