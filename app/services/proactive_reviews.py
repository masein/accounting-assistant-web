"""The accountant speaks first: a week's review, a month's, suggestions and a nudge (scenarios AL1–AL16).

From the owner (2026-10-20): «each week or month the AI gives me a report in the
chat, a message or notification about our spending, or suggestions» and «remind
me to save if I set a goal». Each review is worked out from the books — no
model call, so every figure is the books' own — once per company and period:

* ``weekly``  — last week (Saturday to Friday on the Jalali calendar, Monday to
  Sunday otherwise), made in the new week's first three days;
* ``monthly`` — last month of the company's calendar, made in the new month's
  first three days;
* ``nudge``   — a person's books with nothing recorded for four days, at most
  once a week.

The figures (``facts``) are kept; each reader gets them in their own language
(``render``): a message in the chat — words, a table of categories, and up to
two suggestions with a button each that asks the assistant — and a
notification (app/services/notification_service.py ``_reviews``). Who reads
the books sees it once (``ProactiveReviewSeen``); each person can switch a kind
off (``users.review_prefs``).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import average, grouped, grouped_fa, json_dumps, plain
from app.models.proactive_review import ProactiveReview, ProactiveReviewSeen

log = logging.getLogger(__name__)

KINDS = ("weekly", "monthly", "nudge")
SWITCHES = ("weekly", "monthly", "suggestions", "nudge")
BUSINESS_READERS = ("owner", "cfo", "accountant")
HISTORY_WEEKS = 4          # a "usual" week is the average of the four before it
GRACE_DAYS = 3             # a period's review is made in the three days after it ends, never later
SHOW_DAYS = 10             # and shown (and notified) for ten days after it is made
QUIET_DAYS = 4             # the nudge: four days with nothing recorded…
NUDGE_EVERY_DAYS = 7       # …at most once a week…
NUDGE_MIN_ENTRIES = 5      # …in books that are used
HOT = 1.5                  # a category at 1.5× its usual…
HOT_SHARE = 0.10           # …that is a tenth of the spending or more
OWED_QUIET_DAYS = 30
PACE_SLACK = 1.15          # a budget spending 15% faster than the days pass
MAX_SUGGESTIONS = 2
TOP = 4


# --- who, and what they switched off ------------------------------------------------------------------------

def switched_off(user) -> set[str]:
    return {k for k in (getattr(user, "review_prefs", None) or "").split(",") if k in SWITCHES}


def set_switch(user, kind: str, on: bool) -> set[str]:
    if kind not in SWITCHES:
        raise ValueError(f"no such kind: {kind}")
    off = switched_off(user)
    off.discard(kind) if on else off.add(kind)
    user.review_prefs = ",".join(k for k in SWITCHES if k in off) or None
    return off


def _personal(db: Session) -> bool:
    try:
        from app.services.fx_service import _current_company_row
        row = _current_company_row(db)
        return row is not None and (getattr(row, "kind", None) or "business") == "personal"
    except Exception:  # noqa: BLE001
        return False


def readers(db: Session) -> list:
    """The people of this company who read its books: a person's own, or a company's owner, CFO and accountant."""
    from app.db.tenant import get_current_company
    from app.models.user import User
    cid = get_current_company()
    if not cid:
        return []
    import uuid
    cid = cid if isinstance(cid, uuid.UUID) else uuid.UUID(str(cid))
    roles = ("personal",) if _personal(db) else BUSINESS_READERS
    return list(db.execute(select(User).where(User.company_id == cid, User.is_active.is_(True),
                                              User.role.in_(roles))).scalars())


def wants(user, review: ProactiveReview) -> bool:
    return review.kind not in switched_off(user)


# --- the books ------------------------------------------------------------------------------------------------

def _calendar(db: Session) -> str:
    from app.services.calendar_periods import company_calendar
    return company_calendar(db)


def _currency(db: Session) -> str:
    from app.services.fx_base import base_currency
    return base_currency(db)


def _flows(db: Session, start: date, end: date):
    from app.services.report_card import _flows as flows
    return flows(db, start, end)


def _entries(db: Session, start: date, end: date) -> int:
    from app.models.transaction import Transaction
    return int(db.execute(select(func.count()).select_from(Transaction).where(
        Transaction.date >= start, Transaction.date <= end, Transaction.deleted_at.is_(None))).scalar() or 0)


def _biggest(db: Session, start: date, end: date) -> dict | None:
    """The week's biggest spend: one expense line."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.reporting.common import EXPENSE, classify_account_code
    rows = db.execute(
        select(Transaction.description, Transaction.date, Account.code, Account.name, TransactionLine.base_debit)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Transaction.date >= start, Transaction.date <= end, Transaction.deleted_at.is_(None),
               TransactionLine.base_debit > 0)
        .order_by(TransactionLine.base_debit.desc()).limit(50)).all()
    for desc, on, code, name, amount in rows:
        if classify_account_code(code) == EXPENSE:
            return {"what": (desc or name or "").strip()[:80], "category": name, "amount": plain(amount),
                    "date": on.isoformat()}
    return None


def _usual(history: list[tuple], ccy: str) -> tuple[Any, dict[str, Any]]:
    """The average week (or month) of those with anything in them, and each category's."""
    active = [h for h in history if h[0] or h[1]]
    if len(active) < 2:
        return None, {}
    names = {name for _inc, _sp, by in active for name in by}
    return (average([h[1] for h in active], ccy),
            {name: average([by.get(name, 0) for _inc, _sp, by in active], ccy) for name in names})


def _top(cats: dict[str, Any], usual: dict[str, Any]) -> list[dict]:
    out = []
    for name, amount in sorted(cats.items(), key=lambda kv: -kv[1])[:TOP]:
        u = usual.get(name)
        out.append({"name": name, "amount": plain(amount), "usual": plain(u) if u else None})
    return out


def _company_position(db: Session) -> dict:
    """Cash, runway, overdue invoices and the bills soon due, for a company's review."""
    out: dict[str, Any] = {}
    try:
        from app.services.digest_service import build_daily_digest
        d = build_daily_digest(db)
        out.update(cash=plain(d["cash_on_hand"]), runway=d["runway_months"], receivables=plain(d["ar_outstanding"]),
                   payables=plain(d["ap_outstanding"]))
    except Exception:  # noqa: BLE001 — a review without these is still a review
        log.warning("review: no cash position", exc_info=True)
    try:
        from app.api.invoices import totals_for
        from app.models.entity import Entity
        from app.models.invoice import Invoice
        today = date.today()
        invoices = db.execute(select(Invoice).where(Invoice.status.in_(("issued", "partially_paid")),
                                                    Invoice.due_date.is_not(None))).scalars().all()
        left = {i: due for i, (_p, _c, due) in totals_for(db, list(invoices)).items()}
        ccy = _currency(db)
        overdue = [(inv, left.get(inv.id, 0)) for inv in invoices if inv.kind == "sales" and inv.due_date < today
                   and left.get(inv.id, 0) > 0 and (inv.currency or ccy) == ccy]
        due = [(inv, left.get(inv.id, 0)) for inv in invoices if inv.kind == "purchase" and left.get(inv.id, 0) > 0
               and today <= inv.due_date <= today + timedelta(days=7) and (inv.currency or ccy) == ccy]
        if overdue:
            oldest = min(overdue, key=lambda x: x[0].due_date)[0]
            who = db.get(Entity, oldest.entity_id) if oldest.entity_id else None
            out["overdue"] = {"count": len(overdue), "total": plain(sum(a for _i, a in overdue)),
                              "oldest": (who.name if who else oldest.number), "days": (today - oldest.due_date).days}
        if due:
            out["bills_due"] = {"count": len(due), "total": plain(sum(a for _i, a in due))}
    except Exception:  # noqa: BLE001
        log.warning("review: no invoices", exc_info=True)
    return out


# --- what to suggest -------------------------------------------------------------------------------------------

def _budget_rows(db: Session, today: date) -> list[dict]:
    from app.services.budget_service import budget_utilization
    from app.services.calendar_periods import month_key
    try:
        return budget_utilization(db, month_key(today, _calendar(db)))
    except Exception:  # noqa: BLE001
        return []


def _label(row: dict) -> str:
    return str(row.get("label") or row.get("category") or "").split(" — ", 1)[-1]


def _budget_pace(db: Session, today: date) -> dict | None:
    """A budget this month spending faster than the days pass, that will pass its limit before the month ends."""
    from app.services.calendar_periods import month_bounds_of
    start, end = month_bounds_of(today, _calendar(db))
    elapsed, days = (today - start).days + 1, (end - start).days + 1
    for row in _budget_rows(db, today):
        limit, spent = plain(row["limit_amount"]), plain(row["actual_amount"])
        if limit <= 0 or spent <= 0 or spent >= limit or elapsed >= days:
            continue
        if spent / limit <= elapsed / days * PACE_SLACK:
            continue
        crosses = start + timedelta(days=int(limit / (spent / elapsed)))
        if crosses <= end:
            return {"category": _label(row), "spent": spent, "limit": limit, "on": crosses.isoformat()}
    return None


def suggestions(db: Session, kind: str, facts: dict, today: date) -> list[dict]:
    """Up to two, the most pressing first (scenarios AL6–AL12)."""
    out: list[dict] = []
    personal = facts.get("personal")
    if personal:
        from app.services.personal_goals import list_goals
        goals = [g for g in list_goals(db, today=today) if not g["reached"]]
        behind = [g for g in goals if g["on_track"] is False and g.get("needed_per_month")]
        if behind:
            g = behind[0]
            out.append({"key": "goal_behind", "params": {"goal": g["name"], "needed": g["needed_per_month"],
                                                         "pace": max(0, plain(g["pace_per_month"] or 0)),
                                                         "by": g["target_date"]}})
        elif kind == "weekly" and facts.get("income", 0) > 0 and goals:
            g = goals[0]
            amount = g.get("needed_per_month") or None
            if amount:
                out.append({"key": "goal_payday", "params": {"goal": g["name"], "amount": amount,
                                                             "account": g.get("account_name") or g["name"]}})
    if not personal and (facts.get("runway") is not None) and 0 <= facts["runway"] < 3:
        out.append({"key": "runway", "params": {"months": round(float(facts["runway"]), 1)}})
    if not personal and facts.get("overdue"):
        o = facts["overdue"]
        out.append({"key": "overdue_invoices", "params": {"count": o["count"], "total": o["total"],
                                                          "oldest": o["oldest"], "days": o["days"]}})
    if kind == "weekly" and (pace := _budget_pace(db, today)):
        out.append({"key": "budget_pace", "params": pace})
    if personal:
        for p in facts.get("people") or []:
            if p.get("last_entry") and date.fromisoformat(p["last_entry"]) <= today - timedelta(days=OWED_QUIET_DAYS):
                side = "you_owe" if p.get("you_owe") else "owes_you"
                cur, amount = next(iter(p[side].items()))
                out.append({"key": f"owed_long_{side}", "params": {"person": p["person"], "amount": amount,
                                                                   "currency": cur, "last": p["last_entry"]}})
                break
    budgeted = {_label(r) for r in _budget_rows(db, today)}
    spent = facts.get("spent") or 0
    for c in facts.get("top") or []:
        # float(): books with pence or baisa hold Decimals, and a Decimal times a float is a TypeError
        if c.get("usual") and float(c["amount"]) >= HOT * float(c["usual"]) and spent \
                and float(c["amount"]) >= HOT_SHARE * float(spent) and c["name"] not in budgeted:
            # the button says how much, or the assistant reads the budgets, finds none, and stops (2026-10-20)
            out.append({"key": "hot_category", "params": {"category": c["name"],
                                                          "pct": round((c["amount"] / c["usual"] - 1) * 100),
                                                          "budget": _monthly_budget(db, c, kind, today)}})
            break
    if personal and kind == "monthly":
        try:
            from app.services.recurring_detection import detect_recurring
            subs = [c for c in detect_recurring(db, today=today)
                    if c.direction == "payment" and c.frequency == "monthly"]
        except Exception:  # noqa: BLE001
            subs = []
        if len(subs) >= 2:
            out.append({"key": "subscriptions", "params": {
                "count": len(subs), "total": plain(sum(c.typical_amount for c in subs)),
                "names": [c.description for c in subs[:3]]}})
    return out[:MAX_SUGGESTIONS]


def _nice(value) -> int:
    """Up to the next round figure, two digits kept: 7,333,333 is 7,400,000; 18.5 is 19."""
    import math
    x = float(value)
    if x <= 0:
        return 0
    step = 10 ** max(0, math.floor(math.log10(x)) - 1)
    return int(math.ceil(x / step) * step)


def _monthly_budget(db: Session, cat: dict, kind: str, today: date) -> int:
    """A budget a category could have: its average month of the last three, or four usual weeks."""
    from app.services.calendar_periods import bounds, month_of, shift
    cal = _calendar(db)
    y, m = month_of(today, cal)
    months = [_flows(db, *bounds(*shift(y, m, -i), cal))[2].get(cat["name"], 0) for i in range(1, 4)]
    months = [a for a in months if a]
    if months:
        return _nice(sum(months) / len(months))
    return _nice((cat.get("usual") or cat["amount"]) * (4 if kind == "weekly" else 1))


# --- the facts of a period -------------------------------------------------------------------------------------

def weekly_facts(db: Session, today: date) -> dict | None:
    from app.services.calendar_periods import week_start
    cal, ccy = _calendar(db), _currency(db)
    start = week_start(today, cal) - timedelta(days=7)
    end = start + timedelta(days=6)
    entries = _entries(db, start, end)
    if not entries:
        return None                                   # AL15: a quiet week is the nudge's, not a review
    income, spent, cats = _flows(db, start, end)
    history = [_flows(db, start - timedelta(days=7 * i), end - timedelta(days=7 * i)) for i in range(1, HISTORY_WEEKS + 1)]
    usual, usual_cats = _usual(history, ccy)
    active = [h for h in history if h[0] or h[1]]
    personal = _personal(db)
    facts: dict[str, Any] = {
        "personal": personal, "currency": ccy, "calendar": cal, "start": start.isoformat(), "end": end.isoformat(),
        "entries": entries, "income": plain(income), "spent": plain(spent),
        "usual": plain(usual) if usual is not None else None, "top": _top(cats, usual_cats),
        "biggest": _biggest(db, start, end),
        "usual_income": plain(average([h[0] for h in active], ccy)) if len(active) >= 2 else None,
        # the bars of the card's chart: the four weeks before, then this one (oldest first)
        "series": [{"start": (start - timedelta(days=7 * i)).isoformat(), "spent": plain(h[1]), "income": plain(h[0])}
                   for i, h in reversed(list(enumerate(history, start=1)))]
                  + [{"start": start.isoformat(), "spent": plain(spent), "income": plain(income)}],
    }
    if personal:
        from app.services.ai_accountant.personal_tools import people_balances
        facts["people"] = [p for p in people_balances(db, with_last=True)["people"]][:3]
    else:
        facts.update(_company_position(db))
    return facts


def monthly_facts(db: Session, today: date) -> dict | None:
    from app.services.calendar_periods import key_bounds, month_label, previous_month_key, shift
    cal, ccy = _calendar(db), _currency(db)
    key = previous_month_key(today, cal)
    start, end = key_bounds(key)
    entries = _entries(db, start, end)
    if not entries:
        return None
    personal = _personal(db)
    y, m = int(key[:4]), int(key[5:7])
    prev_key = "%04d-%02d" % shift(y, m, -1)
    facts: dict[str, Any] = {"personal": personal, "currency": ccy, "calendar": cal, "month": key, "previous_month": prev_key,
                             "start": start.isoformat(), "end": end.isoformat(), "entries": entries}
    if personal:
        from app.services.report_card import report_card
        rc = report_card(db, key, lang="en", today=today)
        facts.update(income=plain(rc["income"]), spent=plain(rc["spending"]), saved=plain(rc["saved"]),
                     rate=rc["savings_rate"], previous={k: rc["previous"][k] for k in ("income", "spending", "saved")},
                     top=[{"name": c["category"], "amount": plain(c["amount"]),
                           "usual": plain(c["previous"]) if c["previous"] else None} for c in rc["categories"][:TOP]],
                     budgets=rc["budgets"], net_worth_change=plain(rc["net_worth"]["change"]),
                     goals=[{"name": g["name"], "percent": g["percent"]} for g in rc["goals"][:2]])
    else:
        income, spent, cats = _flows(db, start, end)
        p_income, p_spent, p_cats = _flows(db, *key_bounds(prev_key))
        facts.update(income=plain(income), spent=plain(spent), profit=plain(income - spent),
                     previous={"income": plain(p_income), "spending": plain(p_spent), "profit": plain(p_income - p_spent)},
                     top=[{"name": n, "amount": plain(a), "usual": plain(p_cats[n]) if p_cats.get(n) else None}
                          for n, a in sorted(cats.items(), key=lambda kv: -kv[1])[:TOP]])
        facts.update(_company_position(db))
    series = []
    for i in range(3, -1, -1):
        k = "%04d-%02d" % shift(y, m, -i)
        inc, sp, _c = _flows(db, *key_bounds(k))
        series.append({"month": k, "spent": plain(sp), "income": plain(inc)})
    facts["series"] = series
    facts["label"] = month_label(key, "en")
    return facts


def nudge_facts(db: Session, today: date) -> dict | None:
    """AL13: a person's books, used, with nothing recorded for four days."""
    if not _personal(db):
        return None
    from app.models.transaction import Transaction
    count, last = db.execute(select(func.count(), func.max(Transaction.created_at))
                             .where(Transaction.deleted_at.is_(None))).one()
    if (count or 0) < NUDGE_MIN_ENTRIES or last is None:
        return None
    last_day = last.date() if isinstance(last, datetime) else last
    if (today - last_day).days < QUIET_DAYS:
        return None
    since = datetime.now(timezone.utc) - timedelta(days=NUDGE_EVERY_DAYS)
    if db.execute(select(ProactiveReview.id).where(ProactiveReview.kind == "nudge",
                                                   ProactiveReview.created_at >= since)).first():
        return None
    return {"personal": True, "last": last_day.isoformat(), "calendar": _calendar(db)}


# --- making them ------------------------------------------------------------------------------------------------

def _made(db: Session, kind: str, key: str) -> bool:
    return db.execute(select(ProactiveReview.id).where(ProactiveReview.kind == kind,
                                                       ProactiveReview.period_key == key)).first() is not None


def _make(db: Session, kind: str, key: str, facts: dict, today: date) -> ProactiveReview | None:
    if kind != "nudge":
        facts["suggestions"] = suggestions(db, kind, facts, today)
    review = ProactiveReview(kind=kind, period_key=key, facts=_jsonable(facts))
    db.add(review)
    try:
        db.flush()
    except IntegrityError:                           # another worker made it first
        db.rollback()
        return None
    return review


def _jsonable(value):
    """Kept as JSON with an amount as a number: 2459.97, not the text "2459.97" (deep test 2026-10-20)."""
    import json
    return json.loads(json_dumps(value, fallback=str))


_TEXT_AMOUNT = re.compile(r"-?\d+\.\d+")


def _figures(value):
    """The facts with their amounts as numbers. Those kept on 2026-10-20, before the fix, held an amount with
    places as text ("2459.97"), and the card compared it with a number. A date or a code has no point in it."""
    if isinstance(value, str):
        return float(value) if _TEXT_AMOUNT.fullmatch(value) else value
    if isinstance(value, dict):
        return {k: _figures(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_figures(v) for v in value]
    return value


def generate_due(db: Session, today: date | None = None) -> list[str]:
    """The reviews due today for the current company (the scheduler, once a day). Idempotent."""
    from app.services.calendar_periods import key_bounds, previous_month_key, week_start
    today = today or date.today()
    cal = _calendar(db)
    made: list[str] = []
    this_week = week_start(today, cal)
    if (today - this_week).days < GRACE_DAYS:
        key = (this_week - timedelta(days=7)).isoformat()
        if not _made(db, "weekly", key) and (facts := weekly_facts(db, today)) and _make(db, "weekly", key, facts, today):
            made.append("weekly")
    key = previous_month_key(today, cal)
    if (today - key_bounds(key)[1]).days <= GRACE_DAYS:
        if not _made(db, "monthly", key) and (facts := monthly_facts(db, today)) and _make(db, "monthly", key, facts, today):
            made.append("monthly")
    if (facts := nudge_facts(db, today)) and not _made(db, "nudge", today.isoformat()):
        if _make(db, "nudge", today.isoformat(), facts, today):
            made.append("nudge")
    db.commit()
    return made


def unseen(db: Session, user) -> list[ProactiveReview]:
    """The reviews of the last ten days this person reads and hasn't been shown, oldest first."""
    if user is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(days=SHOW_DAYS)
    seen = select(ProactiveReviewSeen.review_id).where(ProactiveReviewSeen.user_id == _uid(user))
    rows = db.execute(select(ProactiveReview).where(ProactiveReview.created_at >= since,
                                                    ProactiveReview.id.not_in(seen))
                      .order_by(ProactiveReview.created_at)).scalars().all()
    return [r for r in rows if wants(user, r)]


def mark_seen(db: Session, user, reviews: list[ProactiveReview]) -> None:
    for r in reviews:
        db.merge(ProactiveReviewSeen(review_id=r.id, user_id=_uid(user)))


def _uid(user):
    import uuid
    raw = getattr(user, "id", None) or getattr(user, "user_id", None)
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def first_time(db: Session, user) -> bool:
    return db.execute(select(ProactiveReviewSeen.review_id).where(ProactiveReviewSeen.user_id == _uid(user))
                      ).first() is None


# --- saying them ------------------------------------------------------------------------------------------------

def _n(value, lang: str) -> str:
    return grouped_fa(value) if lang == "fa" else grouped(value)


def _pct(value, lang: str) -> str:
    text = f"{abs(int(round(value)))}"
    if lang == "fa":
        from app.utils.jalali import to_persian_digits
        return to_persian_digits(text) + "٪"
    return text + ("٪" if lang == "ar" else "%")


def _day(iso: str | None, cal: str, lang: str) -> str:
    from app.services.ai_accountant.blocks import display_date
    return display_date(iso, cal, lang) or ""


def _money(value, ccy: str, lang: str) -> str:
    """In the currency's own places: 41.5 Omani rials is «۴۱٫۵۰۰ ریال عمان»."""
    from app.core.currencies import name
    from app.core.money import exponent, persian_figures, to_decimal
    places = exponent(ccy) if ccy else 0
    text = f"{to_decimal(value):,.{places}f}" if places else grouped(value)
    return f"{persian_figures(text) if lang == 'fa' else text} {name(ccy, lang)}"


def _span(start: str, end: str, cal: str, lang: str) -> tuple[str, str]:
    """A week's two days, the year said once: «۲۵ مهر تا ۱ آبان ۱۴۰۵»."""
    a, b = _day(start, cal, lang), _day(end, cal, lang)
    if a.split()[-1:] == b.split()[-1:]:
        a = " ".join(a.split()[:-1])
    return a, b


_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "fa": ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"),
    "es": ("el lunes", "el martes", "el miércoles", "el jueves", "el viernes", "el sábado", "el domingo"),
    "ar": ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
}

W: dict[str, dict[str, str]] = {
    "weekly_title": {"en": "Your week, {start} – {end}", "fa": "مرور هفته، {start} تا {end}",
                     "es": "Tu semana, {start} – {end}", "ar": "أسبوعك، {start} – {end}"},
    "weekly_title_co": {"en": "The week, {start} – {end}", "fa": "مرور هفته، {start} تا {end}",
                        "es": "La semana, {start} – {end}", "ar": "الأسبوع، {start} – {end}"},
    "monthly_title": {"en": "Your month: {month}", "fa": "مرور ماه: {month}", "es": "Tu mes: {month}",
                      "ar": "شهرك: {month}"},
    "monthly_title_co": {"en": "The month: {month}", "fa": "مرور ماه: {month}", "es": "El mes: {month}",
                         "ar": "الشهر: {month}"},
    "spent_more": {"en": "You spent {spent} this week — {pct} more than your usual week ({usual}).",
                   "fa": "این هفته {spent} خرج کردید؛ {pct} بیشتر از هفته‌های معمولتان ({usual}).",
                   "es": "Esta semana gastaste {spent}: un {pct} más que en una semana normal ({usual}).",
                   "ar": "أنفقت هذا الأسبوع {spent}، أي أكثر بـ{pct} من أسبوعك المعتاد ({usual})."},
    "spent_less": {"en": "You spent {spent} this week — {pct} less than your usual week ({usual}).",
                   "fa": "این هفته {spent} خرج کردید؛ {pct} کمتر از هفته‌های معمولتان ({usual}).",
                   "es": "Esta semana gastaste {spent}: un {pct} menos que en una semana normal ({usual}).",
                   "ar": "أنفقت هذا الأسبوع {spent}، أي أقل بـ{pct} من أسبوعك المعتاد ({usual})."},
    "spent_same": {"en": "You spent {spent} this week, about as usual ({usual}).",
                   "fa": "این هفته {spent} خرج کردید، تقریباً به اندازهٔ هفته‌های معمولتان ({usual}).",
                   "es": "Esta semana gastaste {spent}, como de costumbre ({usual}).",
                   "ar": "أنفقت هذا الأسبوع {spent}، كالمعتاد تقريبًا ({usual})."},
    "spent": {"en": "You spent {spent} this week.", "fa": "این هفته {spent} خرج کردید.",
              "es": "Esta semana gastaste {spent}.", "ar": "أنفقت هذا الأسبوع {spent}."},
    "income": {"en": "Income: {income}.", "fa": "درآمد: {income}.", "es": "Ingresos: {income}.", "ar": "الدخل: {income}."},
    "biggest": {"en": "Biggest spend: {what}, {amount} ({date}).", "fa": "بیشترین خرج: {what}، {amount} ({date}).",
                "es": "El mayor gasto: {what}, {amount} ({date}).", "ar": "أكبر إنفاق: {what}، {amount} ({date})."},
    "you_owe": {"en": "You owe {person} {amount}.", "fa": "به {person} {amount} بدهکارید.",
                "es": "Le debes {amount} a {person}.", "ar": "تدين لـ{person} بمبلغ {amount}."},
    "owes_you": {"en": "{person} owes you {amount}.", "fa": "{person} {amount} به شما بدهکار است.",
                 "es": "{person} te debe {amount}.", "ar": "{person} مدين لك بمبلغ {amount}."},
    "co_week": {"en": "This week: {income} of revenue and {spent} of expenses.",
                "fa": "این هفته {income} درآمد و {spent} هزینه ثبت شد.",
                "es": "Esta semana: {income} de ingresos y {spent} de gastos.",
                "ar": "هذا الأسبوع: إيرادات {income} ومصروفات {spent}."},
    "co_vs": {"en": "Expenses are {pct} {dir} a usual week.", "fa": "هزینه‌ها {pct} {dir} از هفته‌های معمول است.",
              "es": "Los gastos están un {pct} {dir} de una semana normal.", "ar": "المصروفات {dir} بـ{pct} من الأسبوع المعتاد."},
    "cash_runway": {"en": "Cash: {cash}, about {months} months at this burn.",
                    "fa": "موجودی نقد {cash}؛ با این روند حدود {months} ماه دوام دارد.",
                    "es": "Caja: {cash}, unos {months} meses a este ritmo.",
                    "ar": "النقد: {cash}، يكفي نحو {months} أشهر بهذا المعدل."},
    "cash": {"en": "Cash: {cash}.", "fa": "موجودی نقد: {cash}.", "es": "Caja: {cash}.", "ar": "النقد: {cash}."},
    "overdue": {"en": "Overdue invoices: {count}, {total} in all; the oldest is {oldest}, {days} days.",
                "fa": "{count} فاکتور سررسیدگذشته، روی هم {total}؛ قدیمی‌ترین: {oldest}، {days} روز.",
                "es": "Facturas vencidas: {count}, {total} en total; la más antigua, {oldest}, {days} días.",
                "ar": "{count} فواتير متأخرة بإجمالي {total}؛ أقدمها {oldest} منذ {days} يومًا."},
    "bills_due": {"en": "Bills due in the next 7 days: {count}, {total}.",
                  "fa": "{count} صورت‌حساب خرید تا ۷ روز دیگر سررسید می‌شود: {total}.",
                  "es": "Facturas de compra que vencen en 7 días: {count}, {total}.",
                  "ar": "{count} فواتير شراء تستحق خلال 7 أيام: {total}."},
    "month_flows": {"en": "Income {income}, spending {spent}, saved {saved} ({rate} of income).",
                    "fa": "درآمد {income}، خرج {spent}، پس‌انداز {saved} ({rate} از درآمد).",
                    "es": "Ingresos {income}, gastos {spent}, ahorro {saved} ({rate} de los ingresos).",
                    "ar": "الدخل {income}، الإنفاق {spent}، الادخار {saved} ({rate} من الدخل)."},
    "month_spent": {"en": "Spending {spent}.", "fa": "خرج {spent}.", "es": "Gastos {spent}.", "ar": "الإنفاق {spent}."},
    "month_vs": {"en": "Spending is {pct} {dir} than {month}.", "fa": "خرج نسبت به {month} {pct} {dir} است.",
                 "es": "El gasto está un {pct} {dir} que en {month}.", "ar": "الإنفاق {dir} بـ{pct} من {month}."},
    "budgets": {"en": "{kept} of {set} budgets kept.", "fa": "{kept} از {set} بودجه رعایت شد.",
                "es": "{kept} de {set} presupuestos cumplidos.", "ar": "تم الالتزام بـ{kept} من {set} ميزانيات."},
    "budgets_over": {"en": "Over: {names}.", "fa": "بیش از بودجه: {names}.", "es": "Superados: {names}.",
                     "ar": "تجاوز: {names}."},
    "net_worth": {"en": "Net worth {dir} by {amount}.", "fa": "دارایی خالص {amount} {dir}.",
                  "es": "Tu patrimonio neto {dir} {amount}.", "ar": "صافي ثروتك {dir} بمقدار {amount}."},
    "goals": {"en": "Goals: {list}.", "fa": "هدف‌ها: {list}.", "es": "Metas: {list}.", "ar": "الأهداف: {list}."},
    "co_month": {"en": "Revenue {income}, expenses {spent}, profit {profit} (last month {prev}).",
                 "fa": "درآمد {income}، هزینه {spent}، سود {profit} (ماه قبل {prev}).",
                 "es": "Ingresos {income}, gastos {spent}, beneficio {profit} (el mes anterior {prev}).",
                 "ar": "الإيرادات {income}، المصروفات {spent}، الربح {profit} (الشهر السابق {prev})."},
    "receivables": {"en": "Customers owe {ar}; you owe suppliers {ap}.", "fa": "طلب از مشتریان {ar}؛ بدهی به تأمین‌کنندگان {ap}.",
                    "es": "Los clientes deben {ar}; debes a proveedores {ap}.", "ar": "مستحق من العملاء {ar}؛ ومستحق للموردين {ap}."},
    "categories": {"en": "Where it went:", "fa": "کجا خرج شد:", "es": "En qué se fue:", "ar": "أين ذهب:"},
    "cat_more": {"en": "{pct} more than usual", "fa": "{pct} بیشتر از معمول", "es": "{pct} más de lo normal",
                 "ar": "أكثر بـ{pct} من المعتاد"},
    "cat_less": {"en": "{pct} less than usual", "fa": "{pct} کمتر از معمول", "es": "{pct} menos de lo normal",
                 "ar": "أقل بـ{pct} من المعتاد"},
    "cat_same": {"en": "as usual", "fa": "معمول", "es": "lo normal", "ar": "كالمعتاد"},
    "cat_new": {"en": "new", "fa": "تازه", "es": "nuevo", "ar": "جديد"},
    "cat_more_m": {"en": "{pct} more than last month", "fa": "{pct} بیشتر از ماه قبل", "es": "{pct} más que el mes anterior",
                   "ar": "أكثر بـ{pct} من الشهر السابق"},
    "cat_less_m": {"en": "{pct} less than last month", "fa": "{pct} کمتر از ماه قبل", "es": "{pct} menos que el mes anterior",
                   "ar": "أقل بـ{pct} من الشهر السابق"},
    "more": {"en": "more", "fa": "بیشتر", "es": "más", "ar": "أكثر"},
    "less": {"en": "less", "fa": "کمتر", "es": "menos", "ar": "أقل"},
    "above": {"en": "above", "fa": "بیشتر", "es": "por encima", "ar": "أعلى"},
    "below": {"en": "below", "fa": "کمتر", "es": "por debajo", "ar": "أقل"},
    "rose": {"en": "rose", "fa": "بیشتر شد", "es": "subió", "ar": "ارتفع"},
    "fell": {"en": "fell", "fa": "کمتر شد", "es": "bajó", "ar": "انخفض"},
    "ideas": {"en": "Ideas:", "fa": "پیشنهادها:", "es": "Ideas:", "ar": "اقتراحات:"},
    "first_time": {"en": "You'll get one of these each week and month. To stop them, say “stop the weekly review”.",
                   "fa": "هر هفته و هر ماه یکی از این‌ها می‌آید. اگر نمی‌خواهید، بگویید «مرور هفتگی نفرست».",
                   "es": "Recibirás uno cada semana y cada mes. Para dejar de recibirlos, di «no más resumen semanal».",
                   "ar": "سيصلك واحد كل أسبوع وكل شهر. لإيقافه قل «أوقف المراجعة الأسبوعية»."},
    "nudge": {"en": "Nothing recorded since {day} — anything to add?", "fa": "از {day} چیزی ثبت نشده — خرجی بوده که بنویسیم؟",
              "es": "No se registra nada desde {day}. ¿Algo que añadir?", "ar": "لم يُسجَّل شيء منذ {day} — هل هناك ما نضيفه؟"},
}

# the card's own words (scenario AL17): a headline, its change, the chart, the ring
C: dict[str, dict[str, str]] = {
    "spent_week": {"en": "Spent this week", "fa": "خرج این هفته", "es": "Gastado esta semana", "ar": "إنفاق هذا الأسبوع"},
    "spent_month": {"en": "Spent in {month}", "fa": "خرج {month}", "es": "Gastado en {month}", "ar": "إنفاق {month}"},
    "revenue_week": {"en": "Revenue this week", "fa": "درآمد این هفته", "es": "Ingresos esta semana", "ar": "إيرادات هذا الأسبوع"},
    "profit_month": {"en": "Profit in {month}", "fa": "سود {month}", "es": "Beneficio en {month}", "ar": "ربح {month}"},
    "weeks": {"en": "The last five weeks", "fa": "پنج هفتهٔ اخیر", "es": "Las últimas cinco semanas", "ar": "آخر خمسة أسابيع"},
    "months": {"en": "The last four months", "fa": "چهار ماه اخیر", "es": "Los últimos cuatro meses", "ar": "آخر أربعة أشهر"},
    "this_week": {"en": "This week", "fa": "این هفته", "es": "Esta semana", "ar": "هذا الأسبوع"},
    "usual": {"en": "usual", "fa": "معمول", "es": "lo normal", "ar": "المعتاد"},
    "spending": {"en": "Spending", "fa": "خرج", "es": "Gastos", "ar": "الإنفاق"},
    "income": {"en": "Income", "fa": "درآمد", "es": "Ingresos", "ar": "الدخل"},
    "revenue": {"en": "Revenue", "fa": "درآمد", "es": "Ingresos", "ar": "الإيرادات"},
    "expenses": {"en": "Expenses", "fa": "هزینه", "es": "Gastos", "ar": "المصروفات"},
    "saved": {"en": "Saved", "fa": "پس‌انداز", "es": "Ahorrado", "ar": "المدّخر"},
    "of_income": {"en": "of income", "fa": "از درآمد", "es": "de los ingresos", "ar": "من الدخل"},
    "where": {"en": "Where it went", "fa": "کجا خرج شد", "es": "En qué se fue", "ar": "أين ذهب"},
    "ideas": {"en": "Ideas", "fa": "پیشنهادها", "es": "Ideas", "ar": "اقتراحات"},
}
_EMOJI = {"good_weekly": "🎉", "good_monthly": "🏆", "watch": "👀", "calm": "🌿", "nudge": "✏️"}
_IDEA_ICONS = {"goal_behind": "🎯", "goal_payday": "💰", "budget_pace": "⏳", "hot_category": "🔥",
               "owed_long_you_owe": "🤝", "owed_long_owes_you": "🤝", "subscriptions": "🔁",
               "overdue_invoices": "🧾", "runway": "🛟"}

S: dict[str, dict[str, dict[str, str]]] = {
    "goal_behind": {
        "say": {"en": "To reach “{goal}” by {by}, it needs {needed} a month; the last three months put aside {pace} a month.",
                "fa": "برای رسیدن به «{goal}» تا {by} ماهی {needed} لازم است؛ این سه ماه ماهی {pace} گذاشتید.",
                "es": "Para llegar a «{goal}» en {by} hacen falta {needed} al mes; estos tres meses apartaste {pace} al mes.",
                "ar": "للوصول إلى «{goal}» بحلول {by} يلزم {needed} شهريًا؛ ادّخرت في الأشهر الثلاثة الأخيرة {pace} شهريًا."},
        "ask": {"en": "How can I catch up on my {goal} goal?", "fa": "چطور به هدف {goal} برسم؟",
                "es": "¿Cómo alcanzo mi meta {goal}?", "ar": "كيف ألحق بهدف {goal}؟"}},
    "goal_payday": {
        "say": {"en": "Money came in this week — put {amount} aside for “{goal}”?",
                "fa": "این هفته درآمد داشتید — {amount} برای «{goal}» کنار بگذارید؟",
                "es": "Esta semana entró dinero: ¿apartas {amount} para «{goal}»?",
                "ar": "دخلك مال هذا الأسبوع — هل تدّخر {amount} لـ«{goal}»؟"},
        "ask": {"en": "Move {amount} to {account} for my {goal} goal", "fa": "{amount} برای هدف {goal} به {account} منتقل کن",
                "es": "Mueve {amount} a {account} para mi meta {goal}", "ar": "انقل {amount} إلى {account} لهدف {goal}"}},
    "budget_pace": {
        "say": {"en": "At this pace “{category}” passes its budget around {on} ({spent} of {limit}).",
                "fa": "با این روند، «{category}» حدود {on} از بودجه‌اش می‌گذرد ({spent} از {limit}).",
                "es": "A este ritmo «{category}» supera su presupuesto hacia el {on} ({spent} de {limit}).",
                "ar": "بهذا المعدل تتجاوز «{category}» ميزانيتها نحو {on} ({spent} من {limit})."},
        "ask": {"en": "How can I spend less on {category}?", "fa": "چطور خرج {category} را کم کنم؟",
                "es": "¿Cómo gasto menos en {category}?", "ar": "كيف أنفق أقل على {category}؟"}},
    "hot_category": {
        "say": {"en": "“{category}” was {pct} above usual — set a monthly budget for it?",
                "fa": "خرج «{category}» {pct} بیشتر از معمول بود — بودجهٔ ماهانه برایش بگذارید؟",
                "es": "«{category}» estuvo un {pct} por encima de lo normal: ¿le pones un presupuesto mensual?",
                "ar": "كانت «{category}» أعلى بـ{pct} من المعتاد — هل تضع لها ميزانية شهرية؟"},
        "ask": {"en": "Set a monthly budget of {budget} for {category}", "fa": "برای {category} بودجهٔ ماهانهٔ {budget} بگذار",
                "es": "Pon un presupuesto mensual de {budget} para {category}", "ar": "ضع ميزانية شهرية قدرها {budget} لـ{category}"}},
    "owed_long_you_owe": {
        "say": {"en": "You still owe {person} {amount} (last entry {last}).",
                "fa": "هنوز به {person} {amount} بدهکارید (آخرین بار {last}).",
                "es": "Aún le debes {amount} a {person} (último movimiento {last}).",
                "ar": "ما زلت تدين لـ{person} بمبلغ {amount} (آخر قيد {last})."},
        "ask": {"en": "Record paying {person} back", "fa": "پس دادن پول {person} را ثبت کن",
                "es": "Registra que le devolví el dinero a {person}", "ar": "سجّل سدادي لـ{person}"}},
    "owed_long_owes_you": {
        "say": {"en": "{person} still owes you {amount} (last entry {last}).",
                "fa": "{person} هنوز {amount} به شما بدهکار است (آخرین بار {last}).",
                "es": "{person} aún te debe {amount} (último movimiento {last}).",
                "ar": "ما زال {person} مدينًا لك بمبلغ {amount} (آخر قيد {last})."},
        "ask": {"en": "Record {person} paying me back", "fa": "پس گرفتن پول از {person} را ثبت کن",
                "es": "Registra que {person} me devolvió el dinero", "ar": "سجّل سداد {person} لي"}},
    "subscriptions": {
        "say": {"en": "{count} payments repeat every month, {total} a month in all: {names}.",
                "fa": "{count} پرداخت ماهانهٔ تکراری دارید، روی هم ماهی {total}: {names}.",
                "es": "{count} pagos se repiten cada mes, {total} al mes en total: {names}.",
                "ar": "{count} مدفوعات تتكرر كل شهر، بإجمالي {total} شهريًا: {names}."},
        "ask": {"en": "Show my repeating payments", "fa": "پرداخت‌های تکراری‌ام را نشان بده",
                "es": "Muéstrame mis pagos recurrentes", "ar": "اعرض مدفوعاتي المتكررة"}},
    "overdue_invoices": {
        "say": {"en": "Overdue invoices: {count}, {total} in all — {oldest} for {days} days.",
                "fa": "{count} فاکتور سررسیدگذشته دارید، روی هم {total} — {oldest} {days} روز.",
                "es": "Facturas vencidas: {count}, {total} en total; {oldest}, {days} días.",
                "ar": "{count} فواتير متأخرة بإجمالي {total} — {oldest} منذ {days} يومًا."},
        "ask": {"en": "Which invoices are overdue?", "fa": "کدام فاکتورها سررسیدشان گذشته؟",
                "es": "¿Qué facturas están vencidas?", "ar": "ما الفواتير المتأخرة؟"}},
    "runway": {
        "say": {"en": "Cash lasts about {months} months at this burn.", "fa": "نقدینگی با این روند حدود {months} ماه دوام دارد.",
                "es": "La caja dura unos {months} meses a este ritmo.", "ar": "يكفي النقد نحو {months} أشهر بهذا المعدل."},
        "ask": {"en": "How can we make our cash last longer?", "fa": "چطور دوام نقدینگی را بیشتر کنم؟",
                "es": "¿Cómo hacemos que la caja dure más?", "ar": "كيف نجعل النقد يدوم أطول؟"}},
}


_RTL_LETTER = re.compile(r"[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _own_direction(value, lang: str):
    """A name written the other way keeps its own order inside the sentence, between isolates: an English
    review read «the oldest is 10 ,(فروشگاه مهرگان (شعبه ۲ days» (deep test 2026-10-20)."""
    if not isinstance(value, str):
        return value
    other = _LATIN_LETTER.search(value) and not _RTL_LETTER.search(value) if lang in ("fa", "ar") \
        else _RTL_LETTER.search(value)
    return f"\u2068{value}\u2069" if other else value


def _say(table: dict, key: str, lang: str, *, isolate: bool = True, **params) -> str:
    """``isolate=False`` for a button's question: it is sent as the user's own words, isolates and all."""
    if isolate:
        params = {k: _own_direction(v, lang) for k, v in params.items()}
    return table[key][lang if lang in table[key] else "en"].format(**params)


def _sep(lang: str) -> str:
    return "، " if lang in ("fa", "ar") else ", "


def _digits(text: str, lang: str) -> str:
    if lang != "fa":
        return text
    from app.utils.jalali import to_persian_digits
    return to_persian_digits(text)


def _month_name(key: str, lang: str) -> str:
    from app.services.calendar_periods import month_label
    return month_label(key, lang)


def _suggestion(s: dict, lang: str, ccy: str, cal: str) -> tuple[str, str]:
    p = dict(s.get("params") or {})
    for k in ("needed", "pace", "amount", "spent", "limit", "total", "budget"):
        if k in p and p[k] is not None:
            p[k] = _money(p[k], p.get("currency") if k == "amount" and p.get("currency") else ccy, lang)
    for k in ("by", "on", "last"):
        if p.get(k):
            p[k] = _day(p[k], cal, lang)
    if "pct" in p:
        p["pct"] = _pct(p["pct"], lang)
    for k in ("count", "days"):
        if k in p:
            p[k] = _digits(str(p[k]), lang)
    if "months" in p:
        p["months"] = _digits(f"{p['months']:g}", lang)
    if "names" in p:
        p["names"] = _sep(lang).join(p["names"])
    table = S[s["key"]]
    return _say(table, "say", lang, **p), _say(table, "ask", lang, isolate=False, **p)


def render(review: ProactiveReview, lang: str, *, first: bool = False, ideas: bool = True) -> dict:
    """What a reader is shown: a headline (the notification), the chat's words for the web, the phone's
    blocks (words, a table of categories, the ideas with a button each), and the buttons' questions."""
    lang = lang if lang in ("en", "fa", "es", "ar") else "en"
    f = _figures(review.facts or {})
    cal, ccy = f.get("calendar", "gregorian"), f.get("currency", "")
    personal = f.get("personal")
    lines: list[str] = []
    rows: list[dict] = []
    headline = ""
    m = lambda v: _money(v, ccy, lang)        # noqa: E731

    if review.kind == "nudge":
        last = date.fromisoformat(f["last"])
        day = (_WEEKDAYS[lang][last.weekday()] if (date.today() - last).days < 7 else _day(f["last"], cal, lang))
        text = _say(W, "nudge", lang, day=day)
        card = _card(review, lang, title=text, lines=[], rows=[], picked=[], ideas=[], note=None)
        return {"headline": text, "title": text, "text": text, "choices": [], "card": card,
                "blocks": [{"type": "review", "id": f"review:{review.id}", **card, "fallback_text": text}]}

    if review.kind == "weekly":
        start, end = _span(f["start"], f["end"], cal, lang)
        title = _say(W, "weekly_title" if personal else "weekly_title_co", lang, start=start, end=end)
        spent, usual = f.get("spent") or 0, f.get("usual")
        if personal:
            if usual:
                change = (spent - usual) / usual * 100
                key = "spent_same" if abs(change) < 5 else ("spent_more" if change > 0 else "spent_less")
                lines.append(_say(W, key, lang, spent=m(spent), usual=m(usual), pct=_pct(change, lang)))
            else:
                lines.append(_say(W, "spent", lang, spent=m(spent)))
            headline = lines[0]
            if f.get("income"):
                lines.append(_say(W, "income", lang, income=m(f["income"])))
            if f.get("biggest"):
                b = f["biggest"]
                lines.append(_say(W, "biggest", lang, what=b["what"] or b["category"], amount=m(b["amount"]),
                                  date=_day(b["date"], cal, lang)))
            for p in (f.get("people") or [])[:2]:
                for side in ("you_owe", "owes_you"):
                    for cur, amount in (p.get(side) or {}).items():
                        lines.append(_say(W, side, lang, person=p["person"], amount=_money(amount, cur, lang)))
        else:
            lines.append(_say(W, "co_week", lang, income=m(f.get("income") or 0), spent=m(spent)))
            headline = lines[0]
            if usual and abs((spent - usual) / usual * 100) >= 5:
                change = (spent - usual) / usual * 100
                lines.append(_say(W, "co_vs", lang, pct=_pct(change, lang), dir=W["above" if change > 0 else "below"][lang]))
            lines.extend(_company_lines(f, lang, m))
        for c in f.get("top") or []:
            sub = _against(c, lang, monthly=False)
            rows.append({"label": c["name"], "sub": sub, "value": c["amount"], "currency": ccy})
    else:
        month = _month_name(f["month"], lang)
        title = _say(W, "monthly_title" if personal else "monthly_title_co", lang, month=month)
        prev_month = _month_name(f["previous_month"], lang)
        if personal:
            if f.get("income"):
                lines.append(_say(W, "month_flows", lang, income=m(f["income"]), spent=m(f["spent"]), saved=m(f["saved"]),
                                  rate=_pct(f["rate"] or 0, lang)))
            else:
                lines.append(_say(W, "month_spent", lang, spent=m(f["spent"])))
            headline = lines[0]
            prev = (f.get("previous") or {}).get("spending")
            if prev:
                change = (f["spent"] - prev) / prev * 100
                if abs(change) >= 5:
                    lines.append(_say(W, "month_vs", lang, pct=_pct(change, lang), month=prev_month,
                                      dir=W["more" if change > 0 else "less"][lang]))
            b = f.get("budgets") or {}
            if b.get("set"):
                said = _say(W, "budgets", lang, kept=_digits(str(b["kept"]), lang), set=_digits(str(b["set"]), lang))
                if b.get("over"):
                    said += " " + _say(W, "budgets_over", lang, names=_sep(lang).join(b["over"]))
                lines.append(said)
            if f.get("net_worth_change"):
                ch = f["net_worth_change"]
                lines.append(_say(W, "net_worth", lang, amount=m(abs(ch)), dir=W["rose" if ch > 0 else "fell"][lang]))
            if f.get("goals"):
                lines.append(_say(W, "goals", lang, list=_sep(lang).join(
                    f"{g['name']} {_pct(g['percent'], lang)}" for g in f["goals"])))
        else:
            lines.append(_say(W, "co_month", lang, income=m(f["income"]), spent=m(f["spent"]), profit=m(f["profit"]),
                              prev=m((f.get("previous") or {}).get("profit") or 0)))
            headline = lines[0]
            lines.extend(_company_lines(f, lang, m))
        for c in f.get("top") or []:
            rows.append({"label": c["name"], "sub": _against(c, lang, monthly=True), "value": c["amount"], "currency": ccy})

    picked = list(f.get("suggestions") or []) if ideas else []
    ideas = [_suggestion(s, lang, ccy, cal) for s in picked]
    choices = [ask for _say_, ask in ideas]
    words = "\n".join(lines)
    web = f"**{title}**\n{words}"
    if rows:
        web += f"\n{W['categories'][lang]}\n" + "\n".join(f"• {r['label']}: {m(r['value'])} ({r['sub']})" for r in rows)
    idea_text = "\n".join(said for said, _ask in ideas)
    if idea_text:
        web += f"\n{idea_text}"
    if first:
        web += f"\n{W['first_time'][lang]}"
    card = _card(review, lang, title=title, lines=lines[1:], rows=rows, picked=picked, ideas=ideas,
                 note=W["first_time"][lang] if first else None)
    plain_text = web.replace("**", "")
    # one block the phone draws as a card (an older app says its words: fallback_text)
    blocks: list[dict] = [{"type": "review", "id": f"review:{review.id}", **card, "fallback_text": plain_text}]
    return {"headline": headline, "title": title, "text": web, "choices": choices, "blocks": blocks, "card": card}


def _mood(review: ProactiveReview) -> str:
    f = _figures(review.facts or {})
    if review.kind == "nudge":
        return "nudge"
    spent, income = f.get("spent") or 0, f.get("income") or 0
    if not f.get("personal"):
        if not spent and not income:
            return "calm"
        return "good" if income >= spent else "watch"
    if review.kind == "monthly":
        prev = (f.get("previous") or {}).get("spending")
        if f.get("rate") is not None and f["rate"] >= 10:
            return "good"
        return "watch" if prev and spent > prev * 1.15 else "calm"
    usual = f.get("usual")
    if not usual:
        return "calm"
    return "good" if spent <= usual * 0.95 else ("watch" if spent >= usual * 1.15 else "calm")


def _short(label: str) -> str:
    """«۲۵ مهر ۱۴۰۵» → «۲۵ مهر»; "Oct 2026" → "Oct": a bar's label."""
    parts = label.split()
    return " ".join(parts[:-1]) if len(parts) > 1 else label


def _delta(value, usual, lang: str, *, monthly: bool, lower_is_good: bool) -> dict | None:
    if not usual:
        return None
    change = (value - usual) / usual * 100
    if abs(change) < 5:
        return {"pct": 0, "dir": "same", "good": True, "text": W["cat_same"][lang]}
    up = change > 0
    key = ("cat_more" if up else "cat_less") + ("_m" if monthly else "")
    return {"pct": int(round(abs(change))), "dir": "up" if up else "down", "good": (not up) if lower_is_good else up,
            "text": _say(W, key, lang, pct=_pct(change, lang))}


def _card(review: ProactiveReview, lang: str, *, title: str, lines: list[str], rows: list[dict], picked: list[dict],
          ideas: list[tuple[str, str]], note: str | None) -> dict:
    """The review as a card for the phone and the web (scenario AL17): its mood and emoji, a headline that
    counts up, its change against the usual, a chart, the categories as bars, the ideas with a button each.
    Words come said; numbers come raw, for the bars to be drawn to scale."""
    f = _figures(review.facts or {})
    cal, ccy = f.get("calendar", "gregorian"), f.get("currency", "")
    m = lambda v: _money(v, ccy, lang)        # noqa: E731
    mood = _mood(review)
    emoji = _EMOJI["good_" + review.kind] if mood == "good" else _EMOJI[mood]
    card: dict[str, Any] = {"kind": review.kind, "mood": mood, "emoji": emoji, "title": title, "currency": ccy}
    if review.kind == "nudge":
        return {**card, "headline": None, "chart": None, "ring": None, "categories": [], "facts": [], "ideas": [],
                "note": None}
    personal = f.get("personal")
    monthly = review.kind == "monthly"
    month = _month_name(f["month"], lang) if monthly else None
    if personal:
        value, usual = f.get("spent") or 0, (f.get("usual") if not monthly else (f.get("previous") or {}).get("spending"))
        label = _say(C, "spent_month", lang, month=month) if monthly else C["spent_week"][lang]
        delta = _delta(value, usual, lang, monthly=monthly, lower_is_good=True)
    elif monthly:
        value, usual = f.get("profit") or 0, (f.get("previous") or {}).get("profit")
        label = _say(C, "profit_month", lang, month=month)
        delta = _delta(value, usual, lang, monthly=True, lower_is_good=False) if usual and usual > 0 else None
    else:
        value, usual = f.get("income") or 0, f.get("usual_income")
        label = C["revenue_week"][lang]
        delta = _delta(value, usual, lang, monthly=False, lower_is_good=False)
    card["headline"] = {"label": label, "value": value, "display": m(value), "delta": delta}

    series = f.get("series") or []
    if len(series) >= 2:
        bars = []
        for i, point in enumerate(series):
            current = i == len(series) - 1
            if monthly:
                name = _short(_month_name(point["month"], lang))
            else:
                name = C["this_week"][lang] if current else _short(_day(point["start"], cal, lang))
            bar = {"label": name, "value": point["spent"], "display": m(point["spent"]), "current": current}
            if not personal:
                bar.update(value=point["income"], display=m(point["income"]), value2=point["spent"], display2=m(point["spent"]))
            bars.append(bar)
        card["chart"] = {"title": C["months" if monthly else "weeks"][lang], "bars": bars,
                         "usual": (f.get("usual") if personal and not monthly else None), "usual_label": C["usual"][lang],
                         "legend": ([C["revenue"][lang], C["expenses"][lang]] if not personal else [C["spending"][lang]])}
    else:
        card["chart"] = None
    card["ring"] = None
    if personal and monthly and f.get("income") and f.get("rate") is not None:
        rate = max(0.0, min(100.0, float(f["rate"])))
        card["ring"] = {"pct": rate, "label": C["saved"][lang], "display": m(f.get("saved") or 0),
                        "text": f"{_pct(f['rate'], lang)} {C['of_income'][lang]}"}
    top = max([r["value"] for r in rows] + [c.get("usual") or 0 for c in f.get("top") or []] + [0])
    usuals = {c["name"]: c.get("usual") for c in f.get("top") or []}
    def way(name: str, value) -> str:
        u = usuals.get(name)
        if not u:
            return "new"
        change = (float(value) - float(u)) / float(u) * 100
        return "same" if abs(change) < 10 else ("up" if change > 0 else "down")
    card["categories"] = [{"name": r["label"], "value": r["value"], "display": m(r["value"]), "sub": r["sub"],
                           "dir": way(r["label"], r["value"]),
                           "width": round(float(r["value"]) / float(top), 4) if top else 0,
                           "usual_at": (round(float(usuals[r["label"]]) / float(top), 4)
                                        if top and usuals.get(r["label"]) else None)} for r in rows]
    card["categories_title"] = C["where"][lang]
    card["facts"] = lines
    card["ideas"] = [{"key": s["key"], "icon": _IDEA_ICONS.get(s["key"], "💡"), "text": said, "ask": ask}
                     for s, (said, ask) in zip(picked, ideas)]
    card["ideas_title"] = C["ideas"][lang]
    card["note"] = note
    return card


def _against(c: dict, lang: str, *, monthly: bool) -> str:
    if not c.get("usual"):
        return W["cat_new"][lang]
    change = (c["amount"] - c["usual"]) / c["usual"] * 100
    if abs(change) < 10:
        return W["cat_same"][lang]
    key = ("cat_more" if change > 0 else "cat_less") + ("_m" if monthly else "")
    return _say(W, key, lang, pct=_pct(change, lang))


def _company_lines(f: dict, lang: str, m) -> list[str]:
    out = []
    if f.get("cash") is not None:
        runway = f.get("runway")
        out.append(_say(W, "cash_runway", lang, cash=m(f["cash"]), months=_digits(f"{round(float(runway), 1):g}", lang))
                   if runway is not None and runway >= 0 else _say(W, "cash", lang, cash=m(f["cash"])))
    if f.get("overdue"):
        o = f["overdue"]
        out.append(_say(W, "overdue", lang, count=_digits(str(o["count"]), lang), total=m(o["total"]), oldest=o["oldest"],
                        days=_digits(str(o["days"]), lang)))
    if f.get("bills_due"):
        b = f["bills_due"]
        out.append(_say(W, "bills_due", lang, count=_digits(str(b["count"]), lang), total=m(b["total"])))
    if f.get("receivables") is not None and f.get("payables") is not None and (f["receivables"] or f["payables"]):
        out.append(_say(W, "receivables", lang, ar=m(f["receivables"]), ap=m(f["payables"])))
    return out


# --- where they go ----------------------------------------------------------------------------------------------

def user_row(db: Session, session_user):
    from app.models.user import User
    try:
        return db.get(User, _uid(session_user))
    except (ValueError, TypeError):
        return None


def for_reader(db: Session, session_user) -> list[ProactiveReview]:
    """This reader's unseen reviews, when they read these books (a company's owner, CFO, accountant; a person's own)."""
    user = user_row(db, session_user)
    if user is None or user.role not in (("personal",) if _personal(db) else BUSINESS_READERS):
        return []
    return unseen(db, user)


def feed_items(db: Session, session_user, lang: str) -> list[dict]:
    """The phone's background sync shows each as a notification once (its key); the tap opens the chat (AL5)."""
    from app.services.notification_text import TEXT
    out = []
    for review in for_reader(db, session_user):
        title = TEXT[f"review_{review.kind}"]["title"].get(lang) or TEXT[f"review_{review.kind}"]["title"]["en"]
        out.append({"key": f"review:{review.id}", "kind": "review", "title": title, "body": render(review, lang)["headline"]})
    return out


def take_for_chat(db: Session, session_user, lang: str) -> list[dict]:
    """The unseen reviews as the chat says them, marked seen for this reader (the caller commits): [{text, blocks, choices}]."""
    user = user_row(db, session_user)
    reviews = for_reader(db, session_user)
    if not reviews:
        return []
    first = first_time(db, user)
    ideas = "suggestions" not in switched_off(user)
    out = []
    for i, review in enumerate(reviews):
        said = render(review, lang, first=first and i == len(reviews) - 1 and review.kind != "nudge", ideas=ideas)
        out.append({"review_id": str(review.id), "kind": review.kind, "text": said["text"], "blocks": said["blocks"],
                    "choices": said["choices"], "card": said["card"]})
    mark_seen(db, user, reviews)
    return out
