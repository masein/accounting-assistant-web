"""
CFO Intelligence Layer: answers high-level financial questions with
KPI calculations, cross-report correlation, narrative generation,
and risk scoring. Pure computation — no LLM dependency.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.services.reporting.common import classify_account_code, ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE

logger = logging.getLogger(__name__)

# ─── Report language support ─────────────────────────────────────────
# The CFO/CEO builders produce human-readable prose (insight titles and
# bodies, the narrative paragraph, Q&A answers). These templates localize
# that prose to the user's preferred UI language; structured KPI labels
# stay English because the frontend already translates them by key.
SUPPORTED_REPORT_LANGUAGES = ("en", "fa", "es", "ar")

_CFO_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "ins_unprofitable_title": "Business is unprofitable",
        "ins_unprofitable_body": "Net loss of {loss:,} {money} over the past 12 months. Revenue: {revenue:,}, Expenses: {expenses:,}.",
        "ins_runway_critical_title": "Cash runway critical: {runway:.1f} months",
        "ins_runway_critical_body": "At current burn rate ({burn:,}/month), cash ({cash:,}) will run out in {runway:.1f} months.",
        "ins_runway_limited_title": "Cash runway is limited: {runway:.1f} months",
        "ins_runway_limited_body": "Consider reducing expenses or accelerating collections.",
        "ins_revenue_declined_title": "Revenue declined {pct:.0f}% month-over-month",
        "ins_revenue_declined_body": "This month: {current:,} vs last month: {previous:,}.",
        "ins_expense_spike_title": "Expenses spiked {pct:.0f}% this month",
        "ins_expense_spike_body": "This month: {current:,} vs last month: {previous:,}.",
        "ins_top_cost_title": "Top cost driver: {category}",
        "ins_top_cost_body": "{category} accounts for {pct:.0f}% of total expenses ({amount:,} {money}).",
        "ins_high_receivables_title": "High receivables",
        "ins_high_receivables_body": "Outstanding receivables ({ar:,}) exceed 2 months of revenue. Collection may be lagging.",
        "narr_overview": "Over the past 12 months, total revenue was {revenue:,} {money} with total expenses of {expenses:,} {money}.",
        "narr_profitable": "The business is profitable with a net margin of {margin:.1f}%.",
        "narr_loss": "The business is running at a loss of {loss:,} {money}.",
        "narr_cash": "Cash on hand is {cash:,} {money}, providing approximately {runway:.1f} months of runway at the current burn rate of {burn:,}/month.",
        "narr_key_concern": "Key concern: {title}.",
        "narr_grade": "Overall financial health grade: {grade}.",
        "qa_health": "Financial health grade: {grade} (risk score: {risk}/100). {narrative}",
        "qa_runway": "At the current burn rate of {burn:,} {money}/month, with {cash:,} {money} cash on hand, the business can sustain operations for approximately {runway:.1f} months.",
        "qa_margin": "Net margin is {margin}%.",
        "qa_expenses_up": "Expenses increased {pct}% month-over-month.",
        "qa_no_cost_concentration": "No significant cost concentration detected.",
        "qa_cash_position": "Cash on hand: {cash:,} {money}.",
        "qa_burn": "Burn rate: {burn:,}/month.",
        "qa_receivables": "Outstanding receivables: {ar:,} {money} — consider accelerating collections.",
        "qa_burn_rate": "Monthly burn rate: {burn:,} {money} (3-month average of expenses).",
    },
    "fa": {
        "ins_unprofitable_title": "کسب‌وکار زیان‌ده است",
        "ins_unprofitable_body": "زیان خالص {loss:,} {money} در ۱۲ ماه گذشته. درآمد: {revenue:,}، هزینه‌ها: {expenses:,}.",
        "ins_runway_critical_title": "دوام نقدینگی بحرانی است: {runway:.1f} ماه",
        "ins_runway_critical_body": "با نرخ سوخت فعلی ({burn:,} در ماه)، نقدینگی ({cash:,}) در {runway:.1f} ماه تمام می‌شود.",
        "ins_runway_limited_title": "دوام نقدینگی محدود است: {runway:.1f} ماه",
        "ins_runway_limited_body": "کاهش هزینه‌ها یا تسریع وصول مطالبات را در نظر بگیرید.",
        "ins_revenue_declined_title": "درآمد نسبت به ماه قبل {pct:.0f}٪ کاهش یافت",
        "ins_revenue_declined_body": "این ماه: {current:,} در مقابل ماه قبل: {previous:,}.",
        "ins_expense_spike_title": "هزینه‌ها این ماه {pct:.0f}٪ جهش داشت",
        "ins_expense_spike_body": "این ماه: {current:,} در مقابل ماه قبل: {previous:,}.",
        "ins_top_cost_title": "بزرگ‌ترین محرک هزینه: {category}",
        "ins_top_cost_body": "{category} معادل {pct:.0f}٪ از کل هزینه‌ها است ({amount:,} {money}).",
        "ins_high_receivables_title": "مطالبات بالا",
        "ins_high_receivables_body": "مطالبات وصول‌نشده ({ar:,}) از دو ماه درآمد بیشتر است. وصول مطالبات احتمالاً عقب افتاده است.",
        "narr_overview": "در ۱۲ ماه گذشته، درآمد کل {revenue:,} {money} و هزینه کل {expenses:,} {money} بود.",
        "narr_profitable": "کسب‌وکار سودآور است و حاشیه سود خالص {margin:.1f}٪ دارد.",
        "narr_loss": "کسب‌وکار با زیان {loss:,} {money} مواجه است.",
        "narr_cash": "موجودی نقد {cash:,} {money} است که با نرخ سوخت فعلی {burn:,} در ماه، حدود {runway:.1f} ماه دوام می‌آورد.",
        "narr_key_concern": "نگرانی اصلی: {title}.",
        "narr_grade": "نمره کلی سلامت مالی: {grade}.",
        "qa_health": "نمره سلامت مالی: {grade} (امتیاز ریسک: {risk}/100). {narrative}",
        "qa_runway": "با نرخ سوخت فعلی {burn:,} {money} در ماه و موجودی نقد {cash:,} {money}، کسب‌وکار می‌تواند حدود {runway:.1f} ماه به فعالیت ادامه دهد.",
        "qa_margin": "حاشیه سود خالص {margin}٪ است.",
        "qa_expenses_up": "هزینه‌ها نسبت به ماه قبل {pct}٪ افزایش یافت.",
        "qa_no_cost_concentration": "تمرکز هزینه قابل‌توجهی یافت نشد.",
        "qa_cash_position": "موجودی نقد: {cash:,} {money}.",
        "qa_burn": "نرخ سوخت: {burn:,} در ماه.",
        "qa_receivables": "مطالبات وصول‌نشده: {ar:,} {money} — تسریع وصول را در نظر بگیرید.",
        "qa_burn_rate": "نرخ سوخت ماهانه: {burn:,} {money} (میانگین هزینه‌های ۳ ماه اخیر).",
    },
    "es": {
        "ins_unprofitable_title": "El negocio no es rentable",
        "ins_unprofitable_body": "Pérdida neta de {loss:,} {money} en los últimos 12 meses. Ingresos: {revenue:,}, Gastos: {expenses:,}.",
        "ins_runway_critical_title": "Liquidez crítica: {runway:.1f} meses de margen",
        "ins_runway_critical_body": "Al ritmo de gasto actual ({burn:,}/mes), el efectivo ({cash:,}) se agotará en {runway:.1f} meses.",
        "ins_runway_limited_title": "Margen de liquidez limitado: {runway:.1f} meses",
        "ins_runway_limited_body": "Considera reducir gastos o acelerar los cobros.",
        "ins_revenue_declined_title": "Los ingresos cayeron {pct:.0f}% intermensual",
        "ins_revenue_declined_body": "Este mes: {current:,} frente al mes pasado: {previous:,}.",
        "ins_expense_spike_title": "Los gastos subieron {pct:.0f}% este mes",
        "ins_expense_spike_body": "Este mes: {current:,} frente al mes pasado: {previous:,}.",
        "ins_top_cost_title": "Mayor generador de costes: {category}",
        "ins_top_cost_body": "{category} representa el {pct:.0f}% de los gastos totales ({amount:,} {money}).",
        "ins_high_receivables_title": "Cuentas por cobrar elevadas",
        "ins_high_receivables_body": "Las cuentas por cobrar pendientes ({ar:,}) superan 2 meses de ingresos. El cobro puede estar retrasado.",
        "narr_overview": "En los últimos 12 meses, los ingresos totales fueron {revenue:,} {money} con gastos totales de {expenses:,} {money}.",
        "narr_profitable": "El negocio es rentable con un margen neto del {margin:.1f}%.",
        "narr_loss": "El negocio opera con una pérdida de {loss:,} {money}.",
        "narr_cash": "El efectivo disponible es {cash:,} {money}, lo que da aproximadamente {runway:.1f} meses de margen al ritmo de gasto actual de {burn:,}/mes.",
        "narr_key_concern": "Principal preocupación: {title}.",
        "narr_grade": "Calificación general de salud financiera: {grade}.",
        "qa_health": "Calificación de salud financiera: {grade} (puntuación de riesgo: {risk}/100). {narrative}",
        "qa_runway": "Al ritmo de gasto actual de {burn:,} {money}/mes, con {cash:,} {money} de efectivo disponible, el negocio puede operar aproximadamente {runway:.1f} meses.",
        "qa_margin": "El margen neto es {margin}%.",
        "qa_expenses_up": "Los gastos aumentaron {pct}% intermensual.",
        "qa_no_cost_concentration": "No se detectó una concentración de costes significativa.",
        "qa_cash_position": "Efectivo disponible: {cash:,} {money}.",
        "qa_burn": "Ritmo de gasto: {burn:,}/mes.",
        "qa_receivables": "Cuentas por cobrar pendientes: {ar:,} {money} — considera acelerar los cobros.",
        "qa_burn_rate": "Ritmo de gasto mensual: {burn:,} {money} (promedio de gastos de 3 meses).",
    },
    "ar": {
        "ins_unprofitable_title": "النشاط التجاري غير مربح",
        "ins_unprofitable_body": "صافي خسارة {loss:,} {money} خلال الـ 12 شهراً الماضية. الإيرادات: {revenue:,}، المصروفات: {expenses:,}.",
        "ins_runway_critical_title": "مدة كفاية النقد حرجة: {runway:.1f} شهراً",
        "ins_runway_critical_body": "بمعدل الإنفاق الحالي ({burn:,} شهرياً)، سينفد النقد ({cash:,}) خلال {runway:.1f} شهراً.",
        "ins_runway_limited_title": "مدة كفاية النقد محدودة: {runway:.1f} شهراً",
        "ins_runway_limited_body": "فكّر في خفض المصروفات أو تسريع التحصيل.",
        "ins_revenue_declined_title": "انخفضت الإيرادات {pct:.0f}٪ مقارنة بالشهر السابق",
        "ins_revenue_declined_body": "هذا الشهر: {current:,} مقابل الشهر الماضي: {previous:,}.",
        "ins_expense_spike_title": "قفزت المصروفات {pct:.0f}٪ هذا الشهر",
        "ins_expense_spike_body": "هذا الشهر: {current:,} مقابل الشهر الماضي: {previous:,}.",
        "ins_top_cost_title": "أكبر بند تكلفة: {category}",
        "ins_top_cost_body": "{category} يمثل {pct:.0f}٪ من إجمالي المصروفات ({amount:,} {money}).",
        "ins_high_receivables_title": "ذمم مدينة مرتفعة",
        "ins_high_receivables_body": "الذمم المدينة المستحقة ({ar:,}) تتجاوز شهرين من الإيرادات. قد يكون التحصيل متأخراً.",
        "narr_overview": "خلال الـ 12 شهراً الماضية، بلغ إجمالي الإيرادات {revenue:,} {money} وإجمالي المصروفات {expenses:,} {money}.",
        "narr_profitable": "النشاط التجاري مربح بهامش صافٍ قدره {margin:.1f}٪.",
        "narr_loss": "النشاط التجاري يعمل بخسارة قدرها {loss:,} {money}.",
        "narr_cash": "النقد المتاح هو {cash:,} {money}، أي ما يعادل {runway:.1f} شهراً تقريباً بمعدل الإنفاق الحالي {burn:,} شهرياً.",
        "narr_key_concern": "أهم مصدر قلق: {title}.",
        "narr_grade": "التقييم العام للصحة المالية: {grade}.",
        "qa_health": "تقييم الصحة المالية: {grade} (درجة المخاطر: {risk}/100). {narrative}",
        "qa_runway": "بمعدل الإنفاق الحالي {burn:,} {money} شهرياً، ومع نقد متاح قدره {cash:,} {money}، يمكن للنشاط الاستمرار نحو {runway:.1f} شهراً.",
        "qa_margin": "هامش الربح الصافي {margin}٪.",
        "qa_expenses_up": "ارتفعت المصروفات {pct}٪ مقارنة بالشهر السابق.",
        "qa_no_cost_concentration": "لم يُرصد تركّز كبير في التكاليف.",
        "qa_cash_position": "النقد المتاح: {cash:,} {money}.",
        "qa_burn": "معدل الإنفاق: {burn:,} شهرياً.",
        "qa_receivables": "الذمم المدينة المستحقة: {ar:,} {money} — فكّر في تسريع التحصيل.",
        "qa_burn_rate": "معدل الإنفاق الشهري: {burn:,} {money} (متوسط مصروفات 3 أشهر).",
    },
}


def _normalize_report_language(lang: str | None) -> str:
    lang = (lang or "en").strip().lower()
    return lang if lang in SUPPORTED_REPORT_LANGUAGES else "en"


def _s(lang: str, key: str) -> str:
    """Localized template lookup with English fallback."""
    pack = _CFO_STRINGS.get(lang) or _CFO_STRINGS["en"]
    return pack.get(key) or _CFO_STRINGS["en"][key]


@dataclass
class KPI:
    key: str
    label: str
    value: float | int
    unit: str = ""  # IRR, %, months, ratio
    trend: str = ""  # up, down, flat
    trend_pct: float = 0.0
    risk_level: str = "normal"  # normal, caution, danger


@dataclass
class Insight:
    priority: int  # 1=highest
    category: str  # revenue, expense, cash, risk, growth
    title: str
    body: str
    severity: str = "info"  # info, warning, critical


@dataclass
class CFOReport:
    kpis: list[KPI] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    narrative: str = ""
    risk_score: int = 0  # 0-100 (0=no risk, 100=extreme risk)
    runway_months: float = 0.0
    burn_rate: int = 0
    health_grade: str = "A"  # A, B, C, D, F


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


# Locale-specific account-code prefixes for the three "live" buckets the
# CFO intelligence engine measures (cash on hand, AR for receivables risk,
# AP for payables risk). Without this map every UK-locale install would
# read £0 across the board because the Iranian default codes (1110,
# 1112, 21xx) don't exist in the UK chart.
_CODE_MAP_BY_LOCALE: dict[str, dict[str, tuple[str, ...]]] = {
    "ir": {
        "cash": ("1110",),                  # موجودی نقد و بانک
        "ar":   ("1112",),                  # دریافتنی‌های تجاری
        "ap":   ("21",),                    # all 21xx current liabilities
    },
    "uk": {
        "cash": ("1200", "1210", "1220"),   # bank current / deposit / petty cash
        "ar":   ("1100", "1300", "1400"),   # trade debtors + prepayments + VAT receivable
        "ap":   ("21",),                    # all 21xx creditors
    },
    "default": {
        "cash": ("1110", "1200"),           # accept both common cash codes
        "ar":   ("1100", "1112"),
        "ap":   ("21",),
    },
}


def _resolve_code_map(db: Session) -> dict[str, tuple[str, ...]]:
    """Pick the cash/AR/AP code-prefix tuples that match the active
    reporting locale. Defaults to the broadest mapping if the locale is
    unset or unrecognised."""
    from app.services.locale_service import get_reporting_locale

    locale = (get_reporting_locale(db) or "default").lower()
    return _CODE_MAP_BY_LOCALE.get(locale, _CODE_MAP_BY_LOCALE["default"])


def _resolve_currency_unit(db: Session, requested: str | None) -> str:
    """The label shown on monetary KPIs. Prefer the explicitly-requested
    currency (e.g. for cross-currency comparison reports), otherwise the
    active reporting_currency AppSetting."""
    if requested:
        return requested.upper()
    try:
        from app.services.fx_service import get_reporting_currency
        return (get_reporting_currency(db) or "IRR").upper()
    except Exception:
        return "IRR"


def _load_monthly_data(db: Session, months_back: int = 12, currency: str | None = None) -> dict:
    cutoff = date.today() - timedelta(days=months_back * 31)
    q = (
        select(Transaction)
        .where(Transaction.date >= cutoff)
        .where(Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    )
    if currency:
        q = q.where(Transaction.currency == currency)
    txns = db.execute(q).scalars().unique().all()

    code_map = _resolve_code_map(db)
    cash_prefixes = code_map["cash"]
    ar_prefixes = code_map["ar"]
    ap_prefixes = code_map["ap"]

    monthly_revenue: dict[str, int] = defaultdict(int)
    monthly_expense: dict[str, int] = defaultdict(int)
    monthly_cash_in: dict[str, int] = defaultdict(int)
    monthly_cash_out: dict[str, int] = defaultdict(int)
    expense_by_cat: dict[str, int] = defaultdict(int)
    expense_code_by_cat: dict[str, str] = {}
    revenue_by_client: dict[str, int] = defaultdict(int)
    total_cash = 0
    total_receivable_ledger = 0
    total_payable_ledger = 0

    for txn in txns:
        month = _month_key(txn.date)
        for ln in txn.lines:
            code = ln.account.code or ""
            acc_type = classify_account_code(code)
            if acc_type == REVENUE:
                monthly_revenue[month] += ln.credit - ln.debit
            elif acc_type == EXPENSE:
                monthly_expense[month] += ln.debit - ln.credit
                expense_by_cat[ln.account.name] += ln.debit - ln.credit
                expense_code_by_cat[ln.account.name] = code

            if any(code.startswith(p) for p in cash_prefixes):
                delta = ln.debit - ln.credit
                total_cash += delta
                if delta > 0:
                    monthly_cash_in[month] += delta
                else:
                    monthly_cash_out[month] += abs(delta)

            if any(code.startswith(p) for p in ar_prefixes):
                total_receivable_ledger += ln.debit - ln.credit
            if any(code.startswith(p) for p in ap_prefixes):
                total_payable_ledger += ln.credit - ln.debit

    # Fold outstanding invoices into AR/AP. Without this an SME running
    # cash-basis bookkeeping (sales recorded as cash receipts, not via
    # AR) shows zero receivables even when they have a stack of unpaid
    # invoices sitting in the invoices table. Cancelled and paid
    # invoices are excluded.
    outstanding_ar = db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0))
        .where(Invoice.kind == "sales", Invoice.status.in_(["issued", "draft"]))
    ).scalar() or 0
    outstanding_ap = db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0))
        .where(Invoice.kind == "purchase", Invoice.status.in_(["issued", "draft"]))
    ).scalar() or 0

    total_receivable = total_receivable_ledger + int(outstanding_ar)
    total_payable = total_payable_ledger + int(outstanding_ap)

    return {
        "monthly_revenue": monthly_revenue,
        "monthly_expense": monthly_expense,
        "monthly_cash_in": monthly_cash_in,
        "monthly_cash_out": monthly_cash_out,
        "expense_by_cat": expense_by_cat,
        "expense_code_by_cat": expense_code_by_cat,
        "total_cash": total_cash,
        "total_receivable": total_receivable,
        "total_payable": total_payable,
        "transaction_count": len(txns),
    }


def build_cfo_report(db: Session, currency: str | None = None, lang: str = "en") -> CFOReport:
    lang = _normalize_report_language(lang)
    report = CFOReport()
    data = _load_monthly_data(db, months_back=12, currency=currency)
    # Cash on hand must match the owner dashboard exactly: the true all-time
    # net balance of cash/bank accounts as of today, not the 12-month
    # windowed sum _load_monthly_data accumulates for burn/trend (AI-6).
    from app.services.cash_service import cash_on_hand as _cash_on_hand
    from app.services.locale_service import get_reporting_locale

    data["total_cash"] = _cash_on_hand(
        db, locale=get_reporting_locale(db), currency=currency, as_of=date.today()
    )
    # Currency-unit label that lands on every monetary KPI. Resolved
    # once so the entire report is internally consistent.
    money = _resolve_currency_unit(db, currency)

    rev_vals = list(data["monthly_revenue"].values())
    exp_vals = list(data["monthly_expense"].values())
    cash_in_vals = list(data["monthly_cash_in"].values())
    cash_out_vals = list(data["monthly_cash_out"].values())

    current_month = _month_key(date.today())
    prev_month = _month_key(date.today().replace(day=1) - timedelta(days=1))

    cur_rev = data["monthly_revenue"].get(current_month, 0)
    prev_rev = data["monthly_revenue"].get(prev_month, 0)
    cur_exp = data["monthly_expense"].get(current_month, 0)
    prev_exp = data["monthly_expense"].get(prev_month, 0)

    # KPI: Total Revenue
    total_rev = sum(rev_vals) if rev_vals else 0
    rev_trend = ((cur_rev - prev_rev) / prev_rev * 100) if prev_rev else 0
    report.kpis.append(KPI(
        key="total_revenue", label="Total Revenue (12m)", value=total_rev,
        unit=money, trend="up" if rev_trend > 0 else "down" if rev_trend < 0 else "flat",
        trend_pct=round(rev_trend, 1),
    ))

    # KPI: Monthly Avg Revenue
    avg_rev = int(mean(rev_vals)) if rev_vals else 0
    report.kpis.append(KPI(key="avg_monthly_revenue", label="Avg Monthly Revenue", value=avg_rev, unit=money))

    # KPI: Net Profit
    total_exp = sum(exp_vals) if exp_vals else 0
    net_profit = total_rev - total_exp
    margin = (net_profit / total_rev * 100) if total_rev > 0 else 0
    report.kpis.append(KPI(
        key="net_profit", label="Net Profit (12m)", value=net_profit,
        unit=money, risk_level="danger" if net_profit < 0 else "normal",
    ))
    report.kpis.append(KPI(key="net_margin", label="Net Margin", value=round(margin, 1), unit="%"))

    # KPI: Cash on Hand
    report.kpis.append(KPI(
        key="cash_on_hand", label="Cash on Hand", value=data["total_cash"],
        unit=money, risk_level="danger" if data["total_cash"] < 0 else "caution" if data["total_cash"] < avg_rev else "normal",
    ))

    # KPI: Burn Rate
    recent_exp = exp_vals[-3:] if len(exp_vals) >= 3 else exp_vals
    burn_rate = int(mean(recent_exp)) if recent_exp else 0
    report.burn_rate = burn_rate
    report.kpis.append(KPI(key="burn_rate", label="Monthly Burn Rate", value=burn_rate, unit=money))

    # KPI: Runway
    runway = data["total_cash"] / burn_rate if burn_rate > 0 else 999
    report.runway_months = round(runway, 1)
    report.kpis.append(KPI(
        key="runway_months", label="Cash Runway", value=round(runway, 1),
        unit="months", risk_level="danger" if runway < 3 else "caution" if runway < 6 else "normal",
    ))

    # KPI: Receivables & Payables
    report.kpis.append(KPI(key="accounts_receivable", label="Accounts Receivable", value=data["total_receivable"], unit=money))
    report.kpis.append(KPI(key="accounts_payable", label="Accounts Payable", value=data["total_payable"], unit=money))

    # Expense trend
    exp_trend = ((cur_exp - prev_exp) / prev_exp * 100) if prev_exp else 0
    report.kpis.append(KPI(
        key="expense_trend", label="Expense MoM Change", value=round(exp_trend, 1),
        unit="%", trend="up" if exp_trend > 0 else "down",
        risk_level="caution" if exp_trend > 20 else "normal",
    ))

    # --- INSIGHTS ---
    priority = 1

    if net_profit < 0:
        report.insights.append(Insight(
            priority=priority, category="revenue", severity="critical",
            title=_s(lang, "ins_unprofitable_title"),
            body=_s(lang, "ins_unprofitable_body").format(
                loss=abs(net_profit), money=money, revenue=total_rev, expenses=total_exp),
        ))
        priority += 1

    if runway < 3:
        report.insights.append(Insight(
            priority=priority, category="cash", severity="critical",
            title=_s(lang, "ins_runway_critical_title").format(runway=runway),
            body=_s(lang, "ins_runway_critical_body").format(
                burn=burn_rate, cash=data["total_cash"], runway=runway),
        ))
        priority += 1
    elif runway < 6:
        report.insights.append(Insight(
            priority=priority, category="cash", severity="warning",
            title=_s(lang, "ins_runway_limited_title").format(runway=runway),
            body=_s(lang, "ins_runway_limited_body"),
        ))
        priority += 1

    if rev_trend < -10 and prev_rev > 0:
        report.insights.append(Insight(
            priority=priority, category="revenue", severity="warning",
            title=_s(lang, "ins_revenue_declined_title").format(pct=abs(rev_trend)),
            body=_s(lang, "ins_revenue_declined_body").format(current=cur_rev, previous=prev_rev),
        ))
        priority += 1

    if exp_trend > 30 and prev_exp > 0:
        report.insights.append(Insight(
            priority=priority, category="expense", severity="warning",
            title=_s(lang, "ins_expense_spike_title").format(pct=exp_trend),
            body=_s(lang, "ins_expense_spike_body").format(current=cur_exp, previous=prev_exp),
        ))
        priority += 1

    # Top cost driver
    if data["expense_by_cat"]:
        top_cat = max(data["expense_by_cat"].items(), key=lambda x: x[1])
        top_pct = top_cat[1] / total_exp * 100 if total_exp > 0 else 0
        report.insights.append(Insight(
            priority=priority, category="expense", severity="info",
            title=_s(lang, "ins_top_cost_title").format(category=top_cat[0]),
            body=_s(lang, "ins_top_cost_body").format(
                category=top_cat[0], pct=top_pct, amount=top_cat[1], money=money),
        ))
        priority += 1

    # Receivable risk
    if data["total_receivable"] > avg_rev * 2 and avg_rev > 0:
        report.insights.append(Insight(
            priority=priority, category="risk", severity="warning",
            title=_s(lang, "ins_high_receivables_title"),
            body=_s(lang, "ins_high_receivables_body").format(ar=data["total_receivable"]),
        ))
        priority += 1

    # --- RISK SCORE ---
    risk = 0
    if net_profit < 0:
        risk += 30
    if runway < 3:
        risk += 35
    elif runway < 6:
        risk += 15
    if rev_trend < -20:
        risk += 15
    if exp_trend > 30:
        risk += 10
    if data["total_receivable"] > avg_rev * 3:
        risk += 10
    report.risk_score = min(100, risk)

    # Health Grade
    if report.risk_score <= 15:
        report.health_grade = "A"
    elif report.risk_score <= 30:
        report.health_grade = "B"
    elif report.risk_score <= 50:
        report.health_grade = "C"
    elif report.risk_score <= 70:
        report.health_grade = "D"
    else:
        report.health_grade = "F"

    # --- NARRATIVE ---
    parts = []
    parts.append(_s(lang, "narr_overview").format(revenue=total_rev, expenses=total_exp, money=money))
    if net_profit >= 0:
        parts.append(_s(lang, "narr_profitable").format(margin=margin))
    else:
        parts.append(_s(lang, "narr_loss").format(loss=abs(net_profit), money=money))
    parts.append(_s(lang, "narr_cash").format(
        cash=data["total_cash"], money=money, runway=runway, burn=burn_rate))
    if report.insights:
        top = report.insights[0]
        parts.append(_s(lang, "narr_key_concern").format(title=top.title))
    parts.append(_s(lang, "narr_grade").format(grade=report.health_grade))
    report.narrative = " ".join(parts)

    return report


def answer_cfo_question(db: Session, question: str, currency: str | None = None, lang: str = "en") -> str:
    """
    Answer a natural language CFO question using computed KPIs.
    Returns a plain-text narrative answer in the requested language.
    """
    lang = _normalize_report_language(lang)
    report = build_cfo_report(db, currency=currency, lang=lang)
    low = question.lower()
    money = _resolve_currency_unit(db, currency)

    kpi_map = {k.key: k for k in report.kpis}

    if any(w in low for w in ("healthy", "health", "سلامت", "وضعیت", "salud", "saludable", "صحة", "سليم")):
        return _s(lang, "qa_health").format(
            grade=report.health_grade, risk=report.risk_score, narrative=report.narrative)

    if any(w in low for w in ("survive", "runway", "last", "بقا", "دوام", "sobrevivir", "aguantar", "البقاء", "الاستمرار")):
        return _s(lang, "qa_runway").format(
            burn=report.burn_rate, money=money,
            cash=kpi_map.get("cash_on_hand", KPI(key="", label="", value=0)).value,
            runway=report.runway_months)

    if any(w in low for w in ("profit drop", "profit fell", "profit declin", "سود کاهش", "چرا سود", "bajó la ganancia", "cayó el beneficio", "انخفاض الربح")):
        exp_trend = kpi_map.get("expense_trend")
        top_costs = sorted(report.insights, key=lambda i: i.priority)
        expense_insights = [i for i in top_costs if i.category == "expense"]
        parts = [_s(lang, "qa_margin").format(
            margin=kpi_map.get("net_margin", KPI(key="", label="", value=0)).value)]
        if expense_insights:
            parts.append(expense_insights[0].body)
        if exp_trend and exp_trend.value > 0:
            parts.append(_s(lang, "qa_expenses_up").format(pct=exp_trend.value))
        return " ".join(parts) if parts else report.narrative

    if any(w in low for w in ("cost driver", "main cost", "biggest expense", "هزینه اصلی", "بیشترین هزینه", "mayor gasto", "mayor coste", "أكبر مصروف", "أكبر تكلفة")):
        cost_insights = [i for i in report.insights if i.category == "expense"]
        if cost_insights:
            return cost_insights[0].body
        return _s(lang, "qa_no_cost_concentration")

    if any(w in low for w in ("cash leak", "where.*cash", "نشت نقدینگی", "کجا.*پول", "fuga de efectivo", "تسرب النقد")):
        parts = [_s(lang, "qa_cash_position").format(
            cash=kpi_map.get("cash_on_hand", KPI(key="", label="", value=0)).value, money=money)]
        parts.append(_s(lang, "qa_burn").format(burn=report.burn_rate))
        ar = kpi_map.get("accounts_receivable")
        if ar and ar.value > 0:
            parts.append(_s(lang, "qa_receivables").format(ar=ar.value, money=money))
        return " ".join(parts)

    if any(w in low for w in ("burn rate", "نرخ سوخت", "هزینه ماهانه", "ritmo de gasto", "معدل الإنفاق")):
        return _s(lang, "qa_burn_rate").format(burn=report.burn_rate, money=money)

    # Default: return full narrative
    return report.narrative


@dataclass
class CEOReport:
    revenue_total: int = 0
    revenue_trend: float = 0.0
    profit_total: int = 0
    profit_margin: float = 0.0
    cash_position: int = 0
    cash_runway_months: float = 0.0
    burn_rate: int = 0
    health_grade: str = "A"
    risk_score: int = 0
    total_assets: int = 0
    total_liabilities: int = 0
    total_equity: int = 0
    assets_breakdown: list = field(default_factory=list)
    liabilities_breakdown: list = field(default_factory=list)
    equity_breakdown: list = field(default_factory=list)
    monthly_revenue: list = field(default_factory=list)
    monthly_expenses: list = field(default_factory=list)
    monthly_profit: list = field(default_factory=list)
    top_expenses: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    accounts_receivable: int = 0
    accounts_payable: int = 0
    liability_ratio: float = 0.0


def build_ceo_report(db: Session, currency: str | None = None, lang: str = "en") -> CEOReport:
    """Build a high-level CEO executive summary report. Alerts inherit the
    localized insight text from the CFO report."""
    cfo = build_cfo_report(db, currency=currency, lang=lang)
    data = _load_monthly_data(db, months_back=12, currency=currency)

    report = CEOReport()

    kpi_map = {k.key: k for k in cfo.kpis}

    # Revenue & Profit
    report.revenue_total = int(kpi_map.get("total_revenue", KPI(key="", label="", value=0)).value)
    report.profit_total = int(kpi_map.get("net_profit", KPI(key="", label="", value=0)).value)
    report.profit_margin = float(kpi_map.get("net_margin", KPI(key="", label="", value=0)).value)
    report.cash_position = int(kpi_map.get("cash_on_hand", KPI(key="", label="", value=0)).value)
    report.cash_runway_months = cfo.runway_months
    report.burn_rate = cfo.burn_rate
    report.health_grade = cfo.health_grade
    report.risk_score = cfo.risk_score
    report.accounts_receivable = int(kpi_map.get("accounts_receivable", KPI(key="", label="", value=0)).value)
    report.accounts_payable = int(kpi_map.get("accounts_payable", KPI(key="", label="", value=0)).value)

    # Revenue trend
    rev_trend_kpi = kpi_map.get("total_revenue")
    report.revenue_trend = float(rev_trend_kpi.trend_pct) if rev_trend_kpi else 0.0

    # Monthly series
    months_sorted = sorted(data["monthly_revenue"].keys())
    for m in months_sorted:
        rev = data["monthly_revenue"].get(m, 0)
        exp = data["monthly_expense"].get(m, 0)
        report.monthly_revenue.append({"month": m, "amount": rev})
        report.monthly_expenses.append({"month": m, "amount": exp})
        report.monthly_profit.append({"month": m, "amount": rev - exp})

    # Top expenses
    sorted_expenses = sorted(data["expense_by_cat"].items(), key=lambda x: x[1], reverse=True)[:8]
    total_exp = sum(data["expense_by_cat"].values()) or 1
    for cat, amt in sorted_expenses:
        report.top_expenses.append({"category": cat, "amount": amt, "pct": round(amt / total_exp * 100, 1), "account_code": data["expense_code_by_cat"].get(cat, "")})

    # Balance sheet totals (from accounts)
    from app.models.account import Account as AccountModel
    from app.services.reporting.common import classify_account_code, ASSET, LIABILITY, EQUITY
    accounts = db.execute(select(AccountModel)).scalars().all()
    lines_q = select(
        TransactionLine.account_id,
        func.coalesce(func.sum(TransactionLine.debit), 0),
        func.coalesce(func.sum(TransactionLine.credit), 0),
    ).join(Transaction, TransactionLine.transaction_id == Transaction.id)
    if currency:
        lines_q = lines_q.where(Transaction.currency == currency)
    lines_q = lines_q.group_by(TransactionLine.account_id)
    acc_by_id = {a.id: a for a in accounts}
    assets_map: dict[str, dict] = {}
    liabilities_map: dict[str, dict] = {}
    equity_map: dict[str, dict] = {}
    for account_id, td, tc in db.execute(lines_q).all():
        acc = acc_by_id.get(account_id)
        if not acc:
            continue
        acc_type = classify_account_code(acc.code)
        if acc_type == ASSET:
            bal = (td or 0) - (tc or 0)
            report.total_assets += bal
            if bal != 0:
                assets_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}
        elif acc_type == LIABILITY:
            bal = (tc or 0) - (td or 0)
            report.total_liabilities += bal
            if bal != 0:
                liabilities_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}
        elif acc_type == EQUITY:
            bal = (tc or 0) - (td or 0)
            report.total_equity += bal
            if bal != 0:
                equity_map[acc.code] = {"code": acc.code, "name": acc.name, "balance": bal}

    report.assets_breakdown = sorted(assets_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.liabilities_breakdown = sorted(liabilities_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.equity_breakdown = sorted(equity_map.values(), key=lambda x: abs(x["balance"]), reverse=True)
    report.liability_ratio = round(report.total_liabilities / report.total_assets, 4) if report.total_assets > 0 else 0.0

    # Alerts
    for insight in cfo.insights:
        if insight.severity in ("critical", "warning"):
            report.alerts.append({"severity": insight.severity, "title": insight.title, "body": insight.body})

    return report
