"""Low-cash / daily digest for Owner + CFO.

Reuses the owner-dashboard computation (company-scoped via the tenant context)
and reduces it to a short cash-health summary: cash on hand, AR/AP outstanding
and overdue, runway, and a low-cash flag. Deliberately contains NO salary or
bank-account detail — it is safe to send to the Owner/CFO over Slack/Telegram/
email.

Per-company settings live in the ``app_settings`` table (tenant-scoped):
``digest.enabled``, ``digest.cash_threshold``, ``digest.runway_months``,
``digest.channel``.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.digest_setting import DigestSetting

_DEFAULTS = {"enabled": False, "cash_threshold": 0, "runway_months": 3.0, "channel": "all"}
_CHANNELS = {"all", "slack", "telegram", "email"}


def _company_id(db: Session):
    from app.db.tenant import get_current_company
    cid = get_current_company()
    if not cid:
        return None
    try:
        return uuid.UUID(str(cid))
    except (ValueError, TypeError):
        return None


def _row(db: Session) -> DigestSetting | None:
    cid = _company_id(db)
    return db.get(DigestSetting, cid) if cid else None


def get_digest_settings(db: Session) -> dict:
    row = _row(db)
    if row is None:
        return dict(_DEFAULTS)
    return {
        "enabled": bool(row.enabled),
        "cash_threshold": int(row.cash_threshold),
        "runway_months": float(row.runway_months),
        "channel": row.channel or "all",
    }


def set_digest_settings(db: Session, *, enabled=None, cash_threshold=None,
                        runway_months=None, channel=None) -> dict:
    cid = _company_id(db)
    if cid is None:
        raise ValueError("No company in context")
    if cash_threshold is not None and int(cash_threshold) < 0:
        raise ValueError("cash_threshold must be >= 0")
    if runway_months is not None and float(runway_months) < 0:
        raise ValueError("runway_months must be >= 0")
    if channel is not None and channel not in _CHANNELS:
        raise ValueError(f"channel must be one of {sorted(_CHANNELS)}")

    row = db.get(DigestSetting, cid)
    if row is None:
        row = DigestSetting(company_id=cid, **_DEFAULTS)
        db.add(row)
    if enabled is not None:
        row.enabled = bool(enabled)
    if cash_threshold is not None:
        row.cash_threshold = int(cash_threshold)
    if runway_months is not None:
        row.runway_months = float(runway_months)
    if channel is not None:
        row.channel = channel
    db.commit()
    return get_digest_settings(db)


def build_daily_digest(db: Session) -> dict:
    """Compute the cash-health digest for the current company context."""
    from app.api.reports import get_owner_dashboard
    dash = get_owner_dashboard(currency=None, db=db)
    kpi = {k.key: k for k in dash.kpis}
    currency = kpi["cash_on_hand"].unit if "cash_on_hand" in kpi else ""
    cash = int(kpi["cash_on_hand"].value) if "cash_on_hand" in kpi else 0
    runway = float(kpi["runway_months"].value) if "runway_months" in kpi else -1.0
    ar_total = sum(r.total for r in dash.ar_aging)
    ap_total = sum(r.total for r in dash.ap_aging)
    ar_overdue = sum(r.days_60_plus for r in dash.ar_aging)
    ap_overdue = sum(r.days_60_plus for r in dash.ap_aging)

    settings = get_digest_settings(db)
    reasons = []
    if settings["cash_threshold"] > 0 and cash < settings["cash_threshold"]:
        reasons.append("cash_below_threshold")
    if 0 <= runway < settings["runway_months"]:
        reasons.append("runway_short")
    low_cash = bool(reasons)

    return {
        "currency": currency,
        "cash_on_hand": cash,
        "runway_months": runway if runway >= 0 else None,
        "ar_outstanding": ar_total,
        "ap_outstanding": ap_total,
        "ar_overdue": ar_overdue,
        "ap_overdue": ap_overdue,
        "low_cash": low_cash,
        "low_cash_reasons": reasons,
        "settings": settings,
    }


def format_digest(company_name: str, d: dict) -> str:
    """Plain-text digest body. Cash/AR/AP only — no salary or bank detail."""
    cur = d["currency"]
    lines = [f"Daily digest — {company_name}"]
    if d["low_cash"]:
        why = ", ".join(d["low_cash_reasons"])
        lines.append(f"⚠️ LOW CASH ({why})")
    lines += [
        f"Cash on hand: {d['cash_on_hand']:,} {cur}",
        f"Runway: {d['runway_months'] if d['runway_months'] is not None else 'N/A'} months",
        f"AR outstanding: {d['ar_outstanding']:,} {cur} (overdue {d['ar_overdue']:,})",
        f"AP outstanding: {d['ap_outstanding']:,} {cur} (overdue {d['ap_overdue']:,})",
    ]
    return "\n".join(lines)
