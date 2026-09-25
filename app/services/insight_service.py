"""Proactive insights — the system noticing things before the user asks.

"Hey, it looks like you added a new employee and your monthly cost went
up." Every detector here is deterministic arithmetic over the books (no
LLM), guarded so a failure in one never hides the others, and phrased in
the four UI languages so the bell, the dashboards and the AI briefing all
say the same thing.

Detectors (each returns 0..n ``Insight`` rows, keyed so the notification
feed can dedupe and re-emit them while the condition holds):

* payroll        — latest pay month vs the one before (≥10% move), with the
                   employees who joined / left; new pay profiles this month.
* expense_spike  — an expense account this month > 1.5× its 3-month average.
* revenue_drop   — last complete month's revenue < 75% of the prior 3.
* runway         — cash ÷ average monthly net burn under 6 / 3 months.
* vendor_outlier — a payment ≥ 3× that supplier's usual amount.
* statement_due  — no bank statement in 40+ days (or none ever).
* receivables    — trade debtors up ≥ 25% in 30 days.
* recurring_missed — a detected recurring payment is a week overdue.

The philosophy is the recurring detector's: not crying wolf matters more
than catching everything. Thresholds are deliberately conservative and the
feed shows at most a handful.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.bank_statement import BankStatement
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity, TransactionEntity
from app.models.pay_run import PayRun, PayRunLine
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "fa", "es", "ar")
CACHE_TTL_SECONDS = 600
MAX_INSIGHTS = 8


@dataclass
class Insight:
    key: str                 # stable id incl. period, e.g. "payroll-change-2026-09"
    kind: str
    severity: str            # info | warning | high
    page: str                # SPA page to open
    params: dict[str, Any] = field(default_factory=dict)   # template values
    amount: int | None = None
    data: dict[str, Any] = field(default_factory=dict)     # structured detail for the AI

    def localize(self, lang: str) -> dict[str, str]:
        lang = lang if lang in SUPPORTED_LANGUAGES else "en"
        tpl = _TEMPLATES[self.kind]
        out = {}
        for part in ("title", "message"):
            text = tpl[part].get(lang) or tpl[part]["en"]
            try:
                out[part] = text.format(**self.params)
            except (KeyError, IndexError, ValueError):
                out[part] = tpl[part]["en"].format(**self.params)
        return out

    def as_dict(self, lang: str) -> dict[str, Any]:
        loc = self.localize(lang)
        return {
            "key": self.key, "kind": self.kind, "severity": self.severity, "page": self.page,
            "title": loc["title"], "message": loc["message"], "amount": self.amount,
            "data": self.data,
        }


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "payroll_change": {
        "title": {
            "en": "Payroll {direction_en} {pct}% in {month}",
            "fa": "حقوق و دستمزد در {month} {pct}٪ {direction_fa}",
            "es": "La nómina {direction_es} un {pct}% en {month}",
            "ar": "الرواتب {direction_ar} بنسبة {pct}% في {month}",
        },
        "message": {
            "en": "{current} vs {previous} the month before.{joiners_en}{leavers_en}",
            "fa": "{current} در برابر {previous} ماه قبل.{joiners_fa}{leavers_fa}",
            "es": "{current} frente a {previous} el mes anterior.{joiners_es}{leavers_es}",
            "ar": "{current} مقابل {previous} في الشهر السابق.{joiners_ar}{leavers_ar}",
        },
    },
    "new_employee": {
        "title": {
            "en": "New employee on payroll: {names}",
            "fa": "کارمند جدید در حقوق و دستمزد: {names}",
            "es": "Nuevo empleado en nómina: {names}",
            "ar": "موظف جديد على الرواتب: {names}",
        },
        "message": {
            "en": "Monthly payroll will rise by about {delta} from the next pay run.",
            "fa": "هزینهٔ ماهانهٔ حقوق از دورهٔ بعد حدود {delta} بیشتر می‌شود.",
            "es": "La nómina mensual subirá unos {delta} a partir de la próxima liquidación.",
            "ar": "سترتفع الرواتب الشهرية بنحو {delta} من دورة الدفع القادمة.",
        },
    },
    "expense_spike": {
        "title": {
            "en": "{account} is running {ratio}× its usual level",
            "fa": "{account} حدود {ratio} برابر معمول است",
            "es": "{account} va {ratio}× por encima de lo habitual",
            "ar": "{account} أعلى بـ{ratio}× من المعتاد",
        },
        "message": {
            "en": "{current} so far in {month} against an average of {average} a month.",
            "fa": "تا اینجای {month} {current}، در برابر میانگین ماهانهٔ {average}.",
            "es": "{current} hasta ahora en {month} frente a una media mensual de {average}.",
            "ar": "{current} حتى الآن في {month} مقابل متوسط شهري {average}.",
        },
    },
    "revenue_drop": {
        "title": {
            "en": "Revenue fell {pct}% in {month}",
            "fa": "درآمد در {month} {pct}٪ کم شد",
            "es": "Los ingresos cayeron un {pct}% en {month}",
            "ar": "انخفضت الإيرادات {pct}% في {month}",
        },
        "message": {
            "en": "{current} against an average of {average} over the previous three months.",
            "fa": "{current} در برابر میانگین {average} در سه ماه قبل.",
            "es": "{current} frente a una media de {average} en los tres meses anteriores.",
            "ar": "{current} مقابل متوسط {average} في الأشهر الثلاثة السابقة.",
        },
    },
    "runway": {
        "title": {
            "en": "Cash covers about {months} months at the current pace",
            "fa": "نقدینگی با روند فعلی حدود {months} ماه کفایت می‌کند",
            "es": "El efectivo cubre unos {months} meses al ritmo actual",
            "ar": "النقد يكفي نحو {months} أشهر بالوتيرة الحالية",
        },
        "message": {
            "en": "{cash} on hand, spending about {burn} more than you earn each month.",
            "fa": "{cash} نقد موجود است و ماهانه حدود {burn} بیشتر از درآمد خرج می‌شود.",
            "es": "{cash} disponible; gastas unos {burn} más de lo que ingresas cada mes.",
            "ar": "{cash} متاح، وتنفق نحو {burn} أكثر مما تكسب كل شهر.",
        },
    },
    "vendor_outlier": {
        "title": {
            "en": "Unusually large payment to {name}",
            "fa": "پرداخت غیرمعمول به {name}",
            "es": "Pago inusualmente alto a {name}",
            "ar": "دفعة كبيرة غير معتادة إلى {name}",
        },
        "message": {
            "en": "{amount} on {date} — payments to {name} are usually about {typical}.",
            "fa": "{amount} در {date} — پرداخت‌ها به {name} معمولاً حدود {typical} است.",
            "es": "{amount} el {date}; los pagos a {name} suelen ser de unos {typical}.",
            "ar": "{amount} بتاريخ {date} — عادةً تكون الدفعات إلى {name} نحو {typical}.",
        },
    },
    "statement_due": {
        "title": {
            "en": "Time to upload a bank statement",
            "fa": "وقت بارگذاری صورتحساب بانکی است",
            "es": "Es hora de subir un extracto bancario",
            "ar": "حان وقت رفع كشف حساب بنكي",
        },
        "message": {
            "en": "The last statement covered up to {last}. Upload the new one and I'll check it against the books.",
            "fa": "آخرین صورتحساب تا {last} را پوشش می‌داد. صورتحساب جدید را بارگذاری کنید تا با دفاتر مقایسه کنم.",
            "es": "El último extracto llegaba hasta {last}. Sube el nuevo y lo comparo con los libros.",
            "ar": "غطّى آخر كشف حتى {last}. ارفع الجديد وسأقارنه بالدفاتر.",
        },
    },
    "statement_first": {
        "title": {
            "en": "Upload a bank statement to check your books",
            "fa": "برای بررسی دفاتر یک صورتحساب بانکی بارگذاری کنید",
            "es": "Sube un extracto bancario para comprobar tus libros",
            "ar": "ارفع كشف حساب بنكي للتحقق من دفاترك",
        },
        "message": {
            "en": "{count} bank movements were recorded in the last 90 days but no statement has been imported yet.",
            "fa": "{count} گردش بانکی در ۹۰ روز گذشته ثبت شده اما هنوز صورتحسابی وارد نشده است.",
            "es": "Se registraron {count} movimientos bancarios en 90 días pero aún no se importó ningún extracto.",
            "ar": "سُجّلت {count} حركة بنكية خلال 90 يومًا لكن لم يُستورد أي كشف بعد.",
        },
    },
    "receivables": {
        "title": {
            "en": "Customers owe you {pct}% more than a month ago",
            "fa": "طلب از مشتریان نسبت به یک ماه قبل {pct}٪ بیشتر شده",
            "es": "Los clientes te deben un {pct}% más que hace un mes",
            "ar": "يدين لك العملاء بنسبة {pct}% أكثر من الشهر الماضي",
        },
        "message": {
            "en": "Trade debtors: {current} now vs {previous} thirty days ago. Worth chasing the oldest invoices.",
            "fa": "حساب‌های دریافتنی: {current} اکنون در برابر {previous} سی روز پیش. پیگیری فاکتورهای قدیمی‌تر لازم است.",
            "es": "Deudores: {current} ahora frente a {previous} hace treinta días. Conviene reclamar las facturas más antiguas.",
            "ar": "المدينون: {current} الآن مقابل {previous} قبل ثلاثين يومًا. يجدر متابعة أقدم الفواتير.",
        },
    },
    "recurring_missed": {
        "title": {
            "en": "Usual payment not seen: {description}",
            "fa": "پرداخت معمول انجام نشده: {description}",
            "es": "Pago habitual no registrado: {description}",
            "ar": "لم تُسجَّل الدفعة المعتادة: {description}",
        },
        "message": {
            "en": "About {amount} was expected around {expected}; nothing recorded since {last}.",
            "fa": "حدود {amount} حوالی {expected} انتظار می‌رفت؛ از {last} چیزی ثبت نشده است.",
            "es": "Se esperaban unos {amount} hacia el {expected}; nada registrado desde {last}.",
            "ar": "كان يُتوقع نحو {amount} حوالي {expected}؛ لم يُسجَّل شيء منذ {last}.",
        },
    },
}

_DIRECTION = {
    "up": {"en": "rose", "fa": "بیشتر شد", "es": "subió", "ar": "ارتفعت"},
    "down": {"en": "fell", "fa": "کمتر شد", "es": "bajó", "ar": "انخفضت"},
}
_JOINERS = {"en": " New on payroll: {}.", "fa": " کارمند جدید: {}.", "es": " Nuevos en nómina: {}.", "ar": " جديد على الرواتب: {}."}
_LEAVERS = {"en": " No longer paid: {}.", "fa": " دیگر پرداخت نمی‌شود: {}.", "es": " Ya no cobran: {}.", "ar": " لم يعودوا يتقاضون: {}."}


def _fmt(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(round(n)):,}"


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _prev_month_first(d: date) -> date:
    return (_first_of_month(d) - timedelta(days=1)).replace(day=1)


def insight_language(db: Session) -> str:
    """Language for company-wide rows (notifications): Iranian charts speak
    Persian, everything else English. Per-user surfaces pass the user's own."""
    from app.services.locale_service import get_reporting_locale

    try:
        return "fa" if (get_reporting_locale(db) or "").lower() == "ir" else "en"
    except Exception:
        return "en"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _entity_names(db: Session, ids: set) -> list[str]:
    if not ids:
        return []
    rows = db.execute(select(Entity).where(Entity.id.in_(list(ids)))).scalars().all()
    return sorted(e.name for e in rows)


def detect_payroll(db: Session, today: date) -> list[Insight]:
    out: list[Insight] = []
    since = today - timedelta(days=400)
    runs = db.execute(
        select(PayRun).where(PayRun.pay_date >= since, PayRun.pay_date <= today,
                             PayRun.status.in_(["posted", "paid"]))
        .options(selectinload(PayRun.lines))
    ).scalars().unique().all()
    by_month: dict[str, list[PayRun]] = defaultdict(list)
    for r in runs:
        by_month[_month_key(r.pay_date)].append(r)
    months = sorted(by_month)
    mentioned: set = set()
    if len(months) >= 2:
        cur_m, prev_m = months[-1], months[-2]
        # Stale payroll (last run months ago) is not news.
        latest_pay = max(r.pay_date for r in by_month[cur_m])
        if (today - latest_pay).days <= 45:
            cur = sum(int(r.total_gross or 0) for r in by_month[cur_m])
            prev = sum(int(r.total_gross or 0) for r in by_month[prev_m])
            cur_ids = {l.entity_id for r in by_month[cur_m] for l in r.lines}
            prev_ids = {l.entity_id for r in by_month[prev_m] for l in r.lines}
            joiners = _entity_names(db, cur_ids - prev_ids)
            leavers = _entity_names(db, prev_ids - cur_ids)
            if prev > 0:
                pct = (cur - prev) / prev * 100
                if abs(pct) >= 10 or joiners or leavers:
                    direction = "up" if pct >= 0 else "down"
                    params = {
                        "pct": f"{abs(pct):.0f}", "month": cur_m, "current": _fmt(cur), "previous": _fmt(prev),
                    }
                    for lang in SUPPORTED_LANGUAGES:
                        params[f"direction_{lang}"] = _DIRECTION[direction][lang]
                        params[f"joiners_{lang}"] = _JOINERS[lang].format("، ".join(joiners) if lang in ("fa", "ar") else ", ".join(joiners)) if joiners else ""
                        params[f"leavers_{lang}"] = _LEAVERS[lang].format("، ".join(leavers) if lang in ("fa", "ar") else ", ".join(leavers)) if leavers else ""
                    out.append(Insight(
                        key=f"payroll-change-{cur_m}", kind="payroll_change",
                        severity="warning" if pct >= 10 else "info", page="payroll",
                        params=params, amount=cur - prev,
                        data={"month": cur_m, "current_gross": cur, "previous_gross": prev,
                              "pct": round(pct, 1), "joiners": joiners, "leavers": leavers},
                    ))
                    mentioned |= (cur_ids - prev_ids)

    # Pay profiles created in the last 31 days = someone was just put on payroll.
    since_dt = datetime.combine(today - timedelta(days=31), datetime.min.time(), tzinfo=timezone.utc)
    profiles = db.execute(
        select(EmployeePayProfile).where(EmployeePayProfile.active.is_(True))
    ).scalars().all()
    fresh = []
    for p in profiles:
        created = p.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= since_dt and created.date() <= today and p.entity_id not in mentioned:
            fresh.append(p)
    if fresh:
        names = _entity_names(db, {p.entity_id for p in fresh})
        delta = sum(int(p.base_salary or 0) for p in fresh)
        out.append(Insight(
            key=f"new-employee-{_month_key(today)}-{len(fresh)}", kind="new_employee",
            severity="info", page="payroll",
            params={"names": ", ".join(names), "delta": _fmt(delta)}, amount=delta,
            data={"names": names, "monthly_delta": delta},
        ))
    return out


def _expense_rows(db: Session, since: date, until: date) -> list[Transaction]:
    return db.execute(
        select(Transaction)
        .where(Transaction.date >= since, Transaction.date <= until, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().unique().all()


def detect_expense_spikes(db: Session, today: date, *, txns: list[Transaction] | None = None) -> list[Insight]:
    cur_m = _month_key(today)
    start = _first_of_month(today)
    for _ in range(3):
        start = _prev_month_first(start)
    txns = txns if txns is not None else _expense_rows(db, start, today)
    per_acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    names: dict[str, str] = {}
    for t in txns:
        m = _month_key(t.date)
        for ln in t.lines:
            code = ln.account.code or ""
            if classify_account_code(code) == EXPENSE:
                per_acc[code][m] += int(ln.debit or 0) - int(ln.credit or 0)
                names[code] = ln.account.name
    out: list[Insight] = []
    for code, months in per_acc.items():
        cur = months.get(cur_m, 0)
        base = [v for m, v in months.items() if m != cur_m and v > 0]
        if cur <= 0 or len(base) < 2:
            continue
        avg = sum(base) / 3  # average over the three-month window, absent months count as zero
        if avg <= 0 or cur < 1.5 * avg or (cur - avg) < avg * 0.5:
            continue
        out.append(Insight(
            key=f"expense-spike-{code}-{cur_m}", kind="expense_spike", severity="warning",
            page="dashboard",
            params={"account": names.get(code, code), "ratio": f"{cur / avg:.1f}", "current": _fmt(cur),
                    "average": _fmt(avg), "month": cur_m},
            amount=int(cur - avg),
            data={"account_code": code, "account_name": names.get(code, code), "current": cur,
                  "average": int(avg), "month": cur_m},
        ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


def detect_revenue_drop(db: Session, today: date, *, txns: list[Transaction] | None = None) -> list[Insight]:
    last_first = _prev_month_first(today)
    start = last_first
    for _ in range(3):
        start = _prev_month_first(start)
    last_end = _first_of_month(today) - timedelta(days=1)
    txns = txns if txns is not None else _expense_rows(db, start, last_end)
    rev: dict[str, int] = defaultdict(int)
    for t in txns:
        if t.date > last_end:
            continue
        m = _month_key(t.date)
        for ln in t.lines:
            if classify_account_code(ln.account.code or "") == REVENUE:
                rev[m] += int(ln.credit or 0) - int(ln.debit or 0)
    last_m = _month_key(last_first)
    prior = [rev.get(_month_key(m), 0) for m in _iter_months(start, last_first) if _month_key(m) != last_m]
    prior = [v for v in prior if v > 0]
    if len(prior) < 2:
        return []
    avg = sum(prior) / len(prior)
    cur = rev.get(last_m, 0)
    if avg <= 0 or cur >= 0.75 * avg:
        return []
    pct = (avg - cur) / avg * 100
    return [Insight(
        key=f"revenue-drop-{last_m}", kind="revenue_drop", severity="warning", page="dashboard",
        params={"pct": f"{pct:.0f}", "month": last_m, "current": _fmt(cur), "average": _fmt(avg)},
        amount=int(avg - cur),
        data={"month": last_m, "revenue": cur, "prior_average": int(avg), "pct": round(pct, 1)},
    )]


def _iter_months(start: date, end: date):
    m = _first_of_month(start)
    while m <= end:
        yield m
        m = (m + timedelta(days=32)).replace(day=1)


def runway_insight(cash: int, burn: int, today: date) -> Insight | None:
    """Pure helper: months of runway from cash and average monthly net burn."""
    if burn <= 0 or cash <= 0:
        return None
    months = cash / burn
    if months >= 6:
        return None
    return Insight(
        key=f"runway-{_month_key(today)}", kind="runway",
        severity="high" if months < 3 else "warning", page="dashboard",
        params={"months": f"{months:.1f}", "cash": _fmt(cash), "burn": _fmt(burn)},
        amount=cash, data={"cash": cash, "monthly_net_burn": burn, "runway_months": round(months, 1)},
    )


def detect_runway(db: Session, today: date) -> list[Insight]:
    from app.services.cash_service import cash_on_hand
    from app.services.locale_service import get_reporting_locale

    start = _first_of_month(today)
    for _ in range(3):
        start = _prev_month_first(start)
    last_end = _first_of_month(today) - timedelta(days=1)
    txns = _expense_rows(db, start, last_end)
    net: dict[str, int] = defaultdict(int)
    for t in txns:
        m = _month_key(t.date)
        for ln in t.lines:
            kind = classify_account_code(ln.account.code or "")
            if kind == EXPENSE:
                net[m] += int(ln.debit or 0) - int(ln.credit or 0)
            elif kind == REVENUE:
                net[m] -= int(ln.credit or 0) - int(ln.debit or 0)
    if len(net) < 2:
        return []
    burn = int(sum(net.values()) / 3)
    cash = cash_on_hand(db, locale=get_reporting_locale(db), as_of=today)
    ins = runway_insight(cash, burn, today)
    return [ins] if ins else []


def detect_vendor_outliers(db: Session, today: date) -> list[Insight]:
    from app.services.cash_service import cash_account_predicate
    from app.services.locale_service import get_reporting_locale

    is_cash = cash_account_predicate(get_reporting_locale(db))
    since = today - timedelta(days=395)
    txns = db.execute(
        select(Transaction)
        .join(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
        .where(Transaction.date >= since, Transaction.date <= today, Transaction.deleted_at.is_(None),
               TransactionEntity.role.in_(["supplier", "payee"]))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account),
                 selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity))
    ).scalars().unique().all()
    # (entity_id) -> [(date, amount, txn)]
    payments: dict = defaultdict(list)
    for t in txns:
        paid = sum(int(ln.credit or 0) for ln in t.lines if is_cash(ln.account.code or ""))
        if paid <= 0:
            continue
        for link in t.entity_links:
            if link.role in ("supplier", "payee"):
                payments[link.entity_id].append((t.date, paid, t, link.entity.name if link.entity else ""))
    out: list[Insight] = []
    recent_cut = today - timedelta(days=30)
    for eid, items in payments.items():
        items.sort(key=lambda x: x[0])
        for d, amount, t, name in items:
            if d < recent_cut:
                continue
            history = [a for (dd, a, _, _) in items if dd < d]
            if len(history) < 3:
                continue
            typical = median(history)
            if typical <= 0 or amount < 3 * typical:
                continue
            out.append(Insight(
                key=f"vendor-outlier-{t.id}", kind="vendor_outlier", severity="warning", page="entities",
                params={"name": name, "amount": _fmt(amount), "date": d.isoformat(), "typical": _fmt(typical)},
                amount=amount,
                data={"entity_id": str(eid), "entity_name": name, "transaction_id": str(t.id),
                      "amount": amount, "typical": int(typical), "date": d.isoformat(),
                      "description": t.description},
            ))
    out.sort(key=lambda i: -(i.amount or 0))
    return out[:3]


def detect_statement_due(db: Session, today: date) -> list[Insight]:
    from app.services.cash_service import cash_account_predicate
    from app.services.locale_service import get_reporting_locale

    latest = db.execute(
        select(BankStatement).order_by(BankStatement.created_at.desc())
    ).scalars().first()
    m = _month_key(today)
    if latest is None:
        is_cash = cash_account_predicate(get_reporting_locale(db))
        since = today - timedelta(days=90)
        txns = _expense_rows(db, since, today)
        moves = sum(1 for t in txns if any(is_cash(ln.account.code or "") for ln in t.lines))
        if moves < 15:
            return []
        return [Insight(
            key=f"statement-first-{m}", kind="statement_first", severity="info", page="bank-statements",
            params={"count": moves}, data={"bank_movements_90d": moves},
        )]
    last = latest.to_date or (latest.created_at.date() if latest.created_at else None)
    if last is None or (today - last).days < 40:
        return []
    return [Insight(
        key=f"statement-due-{m}", kind="statement_due", severity="info", page="bank-statements",
        params={"last": last.isoformat()}, data={"last_statement_to": last.isoformat(), "days": (today - last).days},
    )]


def detect_receivables_growth(db: Session, today: date) -> list[Insight]:
    from app.services.cfo_intelligence import _resolve_code_map

    ar_prefixes = _resolve_code_map(db)["ar"]

    def balance(as_of: date) -> int:
        total = 0
        rows = db.execute(
            select(TransactionLine.debit, TransactionLine.credit, TransactionLine.account_id)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(Transaction.date <= as_of, Transaction.deleted_at.is_(None))
        ).all()
        # Resolve account codes once.
        from app.models.account import Account
        codes = {a.id: (a.code or "") for a in db.execute(select(Account)).scalars().all()}
        for debit, credit, acc_id in rows:
            code = codes.get(acc_id, "")
            if any(code.startswith(p) for p in ar_prefixes):
                total += int(debit or 0) - int(credit or 0)
        return total

    now = balance(today)
    then = balance(today - timedelta(days=30))
    if then <= 0 or now < 1.25 * then:
        return []
    pct = (now - then) / then * 100
    return [Insight(
        key=f"receivables-{_month_key(today)}", kind="receivables", severity="warning", page="invoices",
        params={"pct": f"{pct:.0f}", "current": _fmt(now), "previous": _fmt(then)}, amount=now - then,
        data={"current": now, "previous": then, "pct": round(pct, 1)},
    )]


def detect_recurring_missed(db: Session, today: date) -> list[Insight]:
    from app.services.recurring_detection import detect_recurring

    out: list[Insight] = []
    for c in detect_recurring(db, today=today):
        if c.direction != "payment":
            continue
        if (today - c.next_expected).days < 7:
            continue
        out.append(Insight(
            key=f"recurring-missed-{c.key}-{_month_key(today)}", kind="recurring_missed", severity="info",
            page="recurring",
            params={"description": c.description, "amount": _fmt(c.typical_amount),
                    "expected": c.next_expected.isoformat(), "last": c.last_date.isoformat()},
            amount=c.typical_amount,
            data={"key": c.key, "typical_amount": c.typical_amount, "next_expected": c.next_expected.isoformat(),
                  "last_date": c.last_date.isoformat(), "counter_account_code": c.counter_account_code},
        ))
    return out[:3]


DETECTORS: tuple[tuple[str, Callable[[Session, date], list[Insight]]], ...] = (
    ("payroll", detect_payroll),
    ("expense_spike", detect_expense_spikes),
    ("revenue_drop", detect_revenue_drop),
    ("runway", detect_runway),
    ("vendor_outlier", detect_vendor_outliers),
    ("statement_due", detect_statement_due),
    ("receivables", detect_receivables_growth),
    ("recurring_missed", detect_recurring_missed),
)

_SEVERITY_ORDER = {"high": 0, "warning": 1, "info": 2}
_cache: dict[str, tuple[float, date, list[Insight]]] = {}


def invalidate_insights_cache() -> None:
    _cache.clear()


def compute_insights(db: Session, *, today: date | None = None, use_cache: bool = True) -> list[Insight]:
    """Run every detector, tolerate individual failures, rank and cap."""
    from app.db.tenant import get_current_company

    today = today or date.today()
    from app.core.shared_state import books_version, current_scope
    ckey = f"{get_current_company() or 'global'}:{books_version(db, current_scope())}"
    if use_cache:
        hit = _cache.get(ckey)
        if hit and hit[1] == today and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
            return list(hit[2])
    out: list[Insight] = []
    for name, fn in DETECTORS:
        try:
            out.extend(fn(db, today))
        except Exception:  # noqa: BLE001 — one broken detector must not hide the rest
            logger.warning("insight detector %s failed", name, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
    out.sort(key=lambda i: (_SEVERITY_ORDER.get(i.severity, 9), -(i.amount or 0)))
    out = out[:MAX_INSIGHTS]
    if use_cache:
        _cache[ckey] = (time.monotonic(), today, list(out))
    return out


def insights_payload(db: Session, lang: str, *, today: date | None = None) -> dict[str, Any]:
    items = compute_insights(db, today=today)
    return {
        "language": lang if lang in SUPPORTED_LANGUAGES else "en",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "insights": [i.as_dict(lang) for i in items],
    }


def briefing_text(insights: list[Insight], lang: str, *, limit: int = 4) -> str | None:
    """A short assistant-voice briefing for the chat — deterministic."""
    if not insights:
        return None
    lang = lang if lang in SUPPORTED_LANGUAGES else "en"
    head = {
        "en": "Before we start, a few things I noticed in the books:",
        "fa": "قبل از شروع، چند نکته که در دفاتر به چشمم آمد:",
        "es": "Antes de empezar, algunas cosas que noté en los libros:",
        "ar": "قبل أن نبدأ، بعض ما لاحظته في الدفاتر:",
    }[lang]
    tail = {
        "en": "Want me to look into any of these?",
        "fa": "می‌خواهید یکی از این‌ها را بررسی کنم؟",
        "es": "¿Quieres que revise alguno de estos puntos?",
        "ar": "هل تريد أن أتحقق من أي منها؟",
    }[lang]
    lines = []
    for ins in insights[:limit]:
        loc = ins.localize(lang)
        lines.append(f"• {loc['title']} — {loc['message']}")
    return head + "\n" + "\n".join(lines) + "\n" + tail
