"""The AI review queue (roadmap §5.5, part 2): real chat turns kept for review.

About one assistant turn in ten — web chat and the Telegram/Bale bots — is
kept as a snapshot: the question, the answer, each tool call (name, input,
whether it worked) and the cards it made, with the model and how long it
took. The eval set (``app/services/ai_eval/``) says whether the assistant
still does what we expect; this says what it actually does with the
company's own books and phrasing.

Privacy: a sample never leaves its company. Only the owner reviews
(``Perm.AI_REVIEW``) — marking a turn good or needs work, with a note — and
may download one as a draft eval scenario. The platform sees counts per
verdict and model, never text. A sample goes when its conversation is
deleted (cascade), and after ``RETENTION_DAYS``. The owner can stop the
sampling in Settings; personal tenants are never sampled (nobody else would
review them).
"""
from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIReviewSample
from app.models.app_setting import AppSetting

log = logging.getLogger(__name__)

SAMPLE_RATE = 0.1
RETENTION_DAYS = 90
SETTINGS_KEY = "ai_review"
VERDICTS = ("good", "bad")
_TEXT_LIMIT = 8000
_INPUT_LIMIT = 2000


# --- settings -------------------------------------------------------------------------------------------------

def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).scalars().first()


def get_settings(db: Session) -> dict:
    enabled = True
    row = _row(db)
    if row and row.value:
        try:
            enabled = bool((json.loads(row.value) or {}).get("enabled", True))
        except ValueError:
            pass
    return {"enabled": enabled, "sample_rate": SAMPLE_RATE, "retention_days": RETENTION_DAYS}


def save_settings(db: Session, *, enabled: bool) -> dict:
    row = _row(db)
    data = json.dumps({"enabled": bool(enabled)})
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=data))
    else:
        row.value = data
    db.flush()
    return get_settings(db)


# --- keeping a turn -------------------------------------------------------------------------------------------

def _current_model(db: Session) -> str | None:
    try:
        from app.core.ai_runtime import resolve_active_ai_backend, resolve_anthropic_config
        from app.services.ai_accountant.orchestrator import _resolve_chat_shape
        cfg = resolve_anthropic_config() if _resolve_chat_shape(db) == "anthropic" else resolve_active_ai_backend()
        return (str(cfg.get("model") or "").strip() or None)
    except Exception:  # noqa: BLE001 — the model name is a label, not a reason to fail
        return None


def _clip_input(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= _INPUT_LIMIT else {"_truncated": text[:_INPUT_LIMIT]}


def snapshot(result, *, message: str) -> dict:
    """What a sample keeps of one ``ChatResult``."""
    tools = [{"name": tc.get("name"), "input": _clip_input(tc.get("input") or {}), "ok": "result" in tc}
             for tc in (getattr(result, "tool_calls", None) or [])]
    cards = [{"tool": p.get("tool_name") or "", "summary": (p.get("summary") or "")[:500],
              "amount": p.get("amount_in_base")}
             for p in (getattr(result, "proposals", None) or [])]
    return {"user_message": (message or "")[:_TEXT_LIMIT], "reply": (getattr(result, "text", "") or "")[:_TEXT_LIMIT],
            "tools": tools, "cards": cards, "tool_errors": sum(1 for t in tools if not t["ok"]),
            "turns": int(getattr(result, "turns", 1) or 1), "stop_reason": getattr(result, "stop_reason", None) or None}


def maybe_sample(db: Session, result, *, user_id: str, username: str | None, message: str, lang: str,
                 channel: str = "web", latency_ms: int | None = None, personal: bool = False,
                 draw: Callable[[], float] = random.random) -> AIReviewSample | None:
    """Keep this turn for review about one time in ten. Never raises: the
    user's turn has already happened, a failure here only loses the sample."""
    if personal or not message:
        return None
    try:
        if not get_settings(db)["enabled"] or draw() >= SAMPLE_RATE:
            return None
        session_id = uuid.UUID(str(result.session_id)) if getattr(result, "session_id", None) else None
        if session_id is not None:
            from app.models.ai_accountant import AIChatSession
            if db.get(AIChatSession, session_id) is None:
                session_id = None                      # nothing to cascade from
        sample = AIReviewSample(session_id=session_id, user_id=str(user_id), username=username,
                                channel=channel, lang=(lang or "en")[:8], model=_current_model(db),
                                latency_ms=latency_ms, **snapshot(result, message=message))
        db.add(sample)
        db.commit()
        return sample
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("ai review sample not kept", exc_info=True)
        return None


# --- the owner's queue ----------------------------------------------------------------------------------------

def _status_filter(status: str):
    if status == "new":
        return AIReviewSample.verdict.is_(None)
    if status in VERDICTS:
        return AIReviewSample.verdict == status
    return None


def counts(db: Session) -> dict:
    # grouped on the column itself: a coalesce with a bound 'new' is two
    # different expressions to PostgreSQL in SELECT and GROUP BY
    rows = {(v or "new"): n for v, n in db.execute(
        select(AIReviewSample.verdict, func.count()).group_by(AIReviewSample.verdict)).all()}
    out = {k: int(rows.get(k, 0)) for k in ("new", "good", "bad")}
    out["total"] = sum(out.values())
    return out


def to_dict(s: AIReviewSample) -> dict:
    return {"id": str(s.id), "created_at": s.created_at.isoformat() if s.created_at else None,
            "username": s.username, "channel": s.channel, "lang": s.lang, "message": s.user_message,
            "reply": s.reply, "tools": s.tools or [], "cards": s.cards or [], "tool_errors": int(s.tool_errors or 0),
            "turns": int(s.turns or 1), "stop_reason": s.stop_reason, "model": s.model, "latency_ms": s.latency_ms,
            "verdict": s.verdict, "note": s.note, "reviewed_by": s.reviewed_by,
            "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None}


def list_samples(db: Session, *, status: str = "new", limit: int = 50, offset: int = 0) -> dict:
    q = select(AIReviewSample).order_by(AIReviewSample.created_at.desc(), AIReviewSample.id)
    cond = _status_filter(status)
    if cond is not None:
        q = q.where(cond)
    items = db.execute(q.limit(max(1, min(int(limit), 200))).offset(max(0, int(offset)))).scalars().all()
    return {"items": [to_dict(s) for s in items], "counts": counts(db), "settings": get_settings(db)}


def get_sample(db: Session, sample_id: str) -> AIReviewSample | None:
    try:
        return db.get(AIReviewSample, uuid.UUID(str(sample_id)))
    except (ValueError, TypeError):
        return None


def review(db: Session, sample: AIReviewSample, *, verdict: str | None, note: str | None,
           reviewer: str | None) -> AIReviewSample:
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError("verdict must be 'good', 'bad' or null")
    sample.verdict = verdict
    sample.note = (note or "").strip()[:2000] or None
    sample.reviewed_by = reviewer if verdict else None
    sample.reviewed_at = datetime.now(timezone.utc) if verdict else None
    db.flush()
    return sample


def as_scenario(s: AIReviewSample) -> dict:
    """A draft entry for ``app/services/ai_eval/scenarios.json``. A good turn
    becomes what the set should expect, with its trajectory as the replay; a
    turn that needs work keeps the question and the owner's note, and the
    expectations are left to write. Names and ids are this company's — adapt
    them to the eval books before adding the scenario."""
    tools = [t for t in (s.tools or []) if t.get("ok")]
    names = list(dict.fromkeys(t["name"] for t in tools if t.get("name")))
    proposal_tools = [n for n in names if n.startswith("propose_")]
    expect: dict[str, Any] = {}
    if s.verdict == "good":
        expect["proposals"] = len(s.cards or [])
        if proposal_tools:
            expect["tools_all"] = proposal_tools
            expect["card"] = {"tool": (s.cards or [{}])[0].get("tool") or proposal_tools[0]}
        elif names:
            expect["tools_any"] = names
        expect["reply_lang"] = s.lang if s.lang in ("fa", "en") else "en"
    return {
        "id": f"review-{(s.created_at or datetime.now(timezone.utc)):%Y%m%d}-{str(s.id)[:8]}",
        "lang": s.lang,
        "message": s.user_message,
        "expect": expect,
        "replay": [{"tool": t["name"], "input": t.get("input") or {}} for t in tools]
                  + [{"text": s.reply or ""}],
        "review": {"verdict": s.verdict, "note": s.note, "model": s.model,
                   "about": "Draft from a reviewed turn: adapt names and ids to the eval books "
                            "(app/services/ai_eval/fixture.py), then add it to scenarios.json."},
    }


def purge_expired(db: Session, today: date) -> dict:
    """This company's samples older than ``RETENTION_DAYS``."""
    cutoff = datetime.combine(today - timedelta(days=RETENTION_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    n = db.execute(delete(AIReviewSample).where(AIReviewSample.created_at < cutoff)
                   .execution_options(synchronize_session=False)).rowcount
    db.commit()
    return {"deleted": int(n or 0)}


# --- what the platform sees: counts only ----------------------------------------------------------------------

def platform_stats(db: Session) -> list[dict]:
    """Per company: samples kept, reviewed, good / needs work, by model. No text."""
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    with tenant_bypass():
        names = dict(db.execute(select(Company.id, Company.name)).all())
        rows = db.execute(
            select(AIReviewSample.company_id, AIReviewSample.model, AIReviewSample.verdict, func.count())
            .group_by(AIReviewSample.company_id, AIReviewSample.model, AIReviewSample.verdict)
        ).all()
    out: dict[str, dict] = {}
    for cid, model, verdict, n in rows:
        verdict = verdict or "new"
        c = out.setdefault(str(cid), {"company_id": str(cid), "company": names.get(cid), "samples": 0,
                                      "good": 0, "bad": 0, "new": 0, "by_model": {}})
        c["samples"] += int(n)
        c[verdict] += int(n)
        m = c["by_model"].setdefault(model or "unknown", {"good": 0, "bad": 0, "new": 0})
        m[verdict] += int(n)
    return sorted(out.values(), key=lambda c: (-c["samples"], c["company"] or ""))
