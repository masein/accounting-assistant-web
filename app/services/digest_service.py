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


# the digest in the language its readers use (en, fa, es, ar)
_TEXT = {
    "head": {"en": "Daily digest — {company}", "fa": "خلاصه روزانه — {company}", "es": "Resumen diario — {company}",
             "ar": "الملخص اليومي — {company}"},
    "low": {"en": "⚠️ LOW CASH ({why})", "fa": "⚠️ نقدینگی کم ({why})", "es": "⚠️ POCA CAJA ({why})", "ar": "⚠️ نقد منخفض ({why})"},
    "cash_below_threshold": {"en": "cash below the threshold", "fa": "نقد کمتر از آستانه", "es": "caja por debajo del umbral",
                             "ar": "النقد أقل من الحد"},
    "runway_short": {"en": "short runway", "fa": "دوام نقدینگی کوتاه", "es": "autonomía corta", "ar": "مدة تغطية قصيرة"},
    "cash": {"en": "Cash on hand: {amount} {cur}", "fa": "موجودی نقد: {amount} {cur}", "es": "Caja disponible: {amount} {cur}",
             "ar": "النقد المتاح: {amount} {cur}"},
    "runway": {"en": "Runway: {months} months", "fa": "دوام نقدینگی: {months} ماه", "es": "Autonomía: {months} meses",
               "ar": "مدة التغطية: {months} شهر"},
    "na": {"en": "N/A", "fa": "نامشخص", "es": "N/D", "ar": "غير متاح"},
    "ar": {"en": "AR outstanding: {amount} {cur} (overdue {overdue})", "fa": "مطالبات باز: {amount} {cur} (سررسیدگذشته {overdue})",
           "es": "Cuentas por cobrar: {amount} {cur} (vencido {overdue})", "ar": "الذمم المدينة: {amount} {cur} (متأخر {overdue})"},
    "ap": {"en": "AP outstanding: {amount} {cur} (overdue {overdue})", "fa": "بدهی‌های باز: {amount} {cur} (سررسیدگذشته {overdue})",
           "es": "Cuentas por pagar: {amount} {cur} (vencido {overdue})", "ar": "الذمم الدائنة: {amount} {cur} (متأخر {overdue})"},
}
LANGS = ("en", "fa", "es", "ar")


def digest_language(db: Session) -> str:
    """The digest goes to the company's channels, so it is written once: in its
    owner's language, else the company's (Persian for an Iranian chart)."""
    from sqlalchemy import select

    from app.models.user import User
    cid = _company_id(db)
    if cid is not None:
        pref = db.execute(select(User.preferred_language).where(
            User.company_id == cid, User.role == "owner", User.is_active.is_(True)).order_by(User.created_at)).scalars().first()
        if pref and pref.strip().lower() in LANGS:
            return pref.strip().lower()
    from app.services.insight_service import insight_language
    return insight_language(db)


def format_digest(company_name: str, d: dict, lang: str = "en") -> str:
    """Plain-text digest body. Cash/AR/AP only — no salary or bank detail."""
    lang = lang if lang in LANGS else "en"
    say = lambda key, **kw: _TEXT[key][lang].format(**kw)  # noqa: E731
    cur = d["currency"]
    lines = [say("head", company=company_name)]
    if d["low_cash"]:
        why = "، ".join if lang in ("fa", "ar") else ", ".join
        lines.append(say("low", why=why(_TEXT[r][lang] if r in _TEXT else r for r in d["low_cash_reasons"])))
    months = d["runway_months"] if d["runway_months"] is not None else _TEXT["na"][lang]
    lines += [
        say("cash", amount=f"{d['cash_on_hand']:,}", cur=cur),
        say("runway", months=months),
        say("ar", amount=f"{d['ar_outstanding']:,}", cur=cur, overdue=f"{d['ar_overdue']:,}"),
        say("ap", amount=f"{d['ap_outstanding']:,}", cur=cur, overdue=f"{d['ap_overdue']:,}"),
    ]
    return "\n".join(lines)
