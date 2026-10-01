"""The monthly report card for personal books (roadmap §4.12) — کارنامه ماهانه.

For one month of the company's calendar (last month by default): income,
spending, what was saved and the savings rate, against the month before and
the three months before it; the biggest spending categories and what moved;
budgets kept; how net worth changed; the savings goals; and three plain
checks — saved at least a tenth of income, spent no more than the recent
average, stayed inside the budgets. Amounts are base-currency values.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code

SAVE_TARGET = 10          # % of income
TOP = 6

T = {
    "en": {
        "saved": "Saved at least {pct}% of income",
        "saved_detail": "You kept {rate}% of what came in.",
        "saved_none": "No income was recorded this month.",
        "spending": "Spent no more than usual",
        "spending_detail": "{spent} against a three-month average of {avg}.",
        "spending_none": "Not enough history yet to compare.",
        "budgets": "Stayed inside the budgets",
        "budgets_detail": "{kept} of {set} budgets kept.",
        "budgets_over": "Over: {names}.",
        "budgets_none": "No budgets were set for this month.",
        "in_progress": "This month isn't over yet — the figures are so far.",
    },
    "fa": {
        "saved": "پس‌انداز دست‌کم {pct}٪ درآمد",
        "saved_detail": "{rate}٪ از درآمد ماه را نگه داشتید.",
        "saved_none": "در این ماه درآمدی ثبت نشده است.",
        "spending": "خرج بیشتر از معمول نبود",
        "spending_detail": "{spent} در برابر میانگین سه‌ماهه {avg}.",
        "spending_none": "هنوز سابقه کافی برای مقایسه نیست.",
        "budgets": "ماندن در بودجه",
        "budgets_detail": "{kept} از {set} بودجه رعایت شد.",
        "budgets_over": "بیش از بودجه: {names}.",
        "budgets_none": "برای این ماه بودجه‌ای تعیین نشده است.",
        "in_progress": "این ماه هنوز تمام نشده است — ارقام تا امروز است.",
    },
    "es": {
        "saved": "Ahorraste al menos el {pct} % de los ingresos",
        "saved_detail": "Guardaste el {rate} % de lo que entró.",
        "saved_none": "No se registraron ingresos este mes.",
        "spending": "No gastaste más de lo habitual",
        "spending_detail": "{spent} frente a una media de tres meses de {avg}.",
        "spending_none": "Aún no hay historial suficiente para comparar.",
        "budgets": "Te mantuviste dentro de los presupuestos",
        "budgets_detail": "{kept} de {set} presupuestos cumplidos.",
        "budgets_over": "Superados: {names}.",
        "budgets_none": "No hay presupuestos para este mes.",
        "in_progress": "Este mes aún no ha terminado: las cifras son hasta hoy.",
    },
    "ar": {
        "saved": "ادخرت {pct}٪ على الأقل من الدخل",
        "saved_detail": "احتفظت بـ {rate}٪ مما دخل.",
        "saved_none": "لم يُسجّل دخل هذا الشهر.",
        "spending": "لم تنفق أكثر من المعتاد",
        "spending_detail": "{spent} مقابل متوسط ثلاثة أشهر قدره {avg}.",
        "spending_none": "لا يوجد سجل كافٍ للمقارنة بعد.",
        "budgets": "بقيت ضمن الميزانيات",
        "budgets_detail": "التُزم بـ {kept} من {set} ميزانية.",
        "budgets_over": "تجاوزت: {names}.",
        "budgets_none": "لا ميزانيات لهذا الشهر.",
        "in_progress": "لم ينتهِ هذا الشهر بعد — الأرقام حتى اليوم.",
    },
}


def _flows(db: Session, start: date, end: date) -> tuple[int, int, dict[str, int]]:
    """(income, spending, spending by category) between two dates, base values."""
    rows = db.execute(
        select(Account.code, Account.name, func.coalesce(func.sum(TransactionLine.base_debit), 0),
               func.coalesce(func.sum(TransactionLine.base_credit), 0))
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Transaction.date >= start, Transaction.date <= end, Transaction.deleted_at.is_(None))
        .group_by(Account.code, Account.name)).all()
    income = spending = 0
    cats: dict[str, int] = {}
    for code, name, dr, cr in rows:
        kind = classify_account_code(code)
        if kind == REVENUE:
            income += int(cr) - int(dr)
        elif kind == EXPENSE:
            amount = int(dr) - int(cr)
            spending += amount
            if amount:
                cats[name] = cats.get(name, 0) + amount
    return income, spending, cats


def _rate(income: int, spending: int) -> float | None:
    return round((income - spending) / income * 100, 1) if income > 0 else None


def _fmt(n: int, lang: str) -> str:
    from app.services.documents.formatting import to_persian_digits
    text = f"{int(n):,}"
    return to_persian_digits(text) if lang == "fa" else text


def report_card(db: Session, month: str | None = None, *, lang: str | None = None, today: date | None = None) -> dict:
    from app.services.budget_service import budget_utilization
    from app.services.calendar_periods import company_calendar, key_bounds, month_label, previous_month_key, shift
    from app.services.net_worth_service import compute_net_worth
    from app.services.personal_goals import list_goals

    today = today or date.today()
    cal = company_calendar(db)
    lang = lang if lang in T else ("fa" if cal == "jalali" else "en")
    W = T[lang]
    key = month or previous_month_key(today, cal)
    start, end = key_bounds(key)                       # raises ValueError on a bad key
    y, m = int(key[:4]), int(key[5:7])
    keys = [f"{py:04d}-{pm:02d}" for py, pm in (shift(y, m, -i) for i in range(1, 4))]
    upto = min(end, today)

    income, spending, cats = _flows(db, start, upto)
    history = [_flows(db, *key_bounds(k)) for k in keys]
    p_income, p_spending, p_cats = history[0]
    past = [h[1] for h in history if h[0] or h[1]]
    avg3 = round(sum(past) / len(past)) if past else None

    top = sorted(cats.items(), key=lambda kv: -kv[1])[:TOP]
    categories = [{"category": c, "amount": a, "previous": p_cats.get(c, 0),
                   "change_pct": round((a - p_cats[c]) / p_cats[c] * 100, 1) if p_cats.get(c) else None}
                  for c, a in top]
    rises = sorted(((c, a - p_cats.get(c, 0)) for c, a in cats.items()), key=lambda kv: -kv[1])
    biggest_rise = {"category": rises[0][0], "increase": rises[0][1]} if rises and rises[0][1] > 0 else None

    budgets = budget_utilization(db, key)
    over = [b["category"] for b in budgets if b["actual_amount"] > b["limit_amount"]]

    nw_end = compute_net_worth(db, as_of=upto, with_trend=False)
    nw_start = compute_net_worth(db, as_of=start - timedelta(days=1), with_trend=False)

    rate = _rate(income, spending)
    checks = [
        {"key": "saved", "ok": None if rate is None else rate >= SAVE_TARGET,
         "item": W["saved"].format(pct=_fmt(SAVE_TARGET, lang)),
         "detail": W["saved_none"] if rate is None else W["saved_detail"].format(rate=_fmt_rate(rate, lang))},
        {"key": "spending", "ok": None if avg3 is None else spending <= avg3, "item": W["spending"],
         "detail": W["spending_none"] if avg3 is None else W["spending_detail"].format(spent=_fmt(spending, lang),
                                                                                         avg=_fmt(avg3, lang))},
        {"key": "budgets", "ok": None if not budgets else not over, "item": W["budgets"],
         "detail": W["budgets_none"] if not budgets else (
             W["budgets_detail"].format(kept=_fmt(len(budgets) - len(over), lang), set=_fmt(len(budgets), lang))
             + (" " + W["budgets_over"].format(names="، ".join(over) if lang in ("fa", "ar") else ", ".join(over)) if over else ""))},
    ]
    return {
        "month": key, "label": month_label(key, lang), "from_date": start.isoformat(), "to_date": end.isoformat(),
        "in_progress": end >= today, "note": W["in_progress"] if end >= today else None, "lang": lang,
        "currency": nw_end.currency,
        "income": income, "spending": spending, "saved": income - spending, "savings_rate": rate,
        "previous": {"month": keys[0], "income": p_income, "spending": p_spending, "saved": p_income - p_spending,
                     "savings_rate": _rate(p_income, p_spending)},
        "average_spending_3m": avg3,
        "categories": categories, "biggest_rise": biggest_rise,
        "budgets": {"set": len(budgets), "kept": len(budgets) - len(over), "over": over},
        "net_worth": {"start": int(nw_start.net_worth), "end": int(nw_end.net_worth),
                      "change": int(nw_end.net_worth) - int(nw_start.net_worth)},
        "goals": [{"name": g["name"], "percent": g["percent"], "on_track": g["on_track"], "reached": g["reached"]}
                  for g in list_goals(db, today=upto)],
        "checks": checks, "passed": sum(1 for c in checks if c["ok"]),
        "scored": sum(1 for c in checks if c["ok"] is not None),
    }


def _fmt_rate(rate: float, lang: str) -> str:
    from app.services.documents.formatting import to_persian_digits
    text = f"{rate:g}"
    return to_persian_digits(text) if lang == "fa" else text
