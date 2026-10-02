"""The monthly close pack (roadmap §4.9, part 3).

One ZIP a month for the owner, the accountant or the auditor:

* ``close-pack-<month>.pdf`` — a cover with the close checklist, then the
  financial statements for the month, the trial balance, receivables and
  payables aging at month end, the bank reconciliation and budget vs actual;
* ``close-pack-<month>.xlsx`` — the checklist and the same tables, a sheet
  each, with real numbers;
* ``journal-<month>.csv`` — every journal line of the month.

The month is a key in the company's calendar: ``1405-07`` is Mehr 1405 for an
Iranian company, ``2026-09`` September for a UK one. Amounts are in the base
currency, every currency at the value it was posted at — as the statements
show them by default. ``checklist`` is also served on its own, so the page can
say what's still open before anyone downloads the pack.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.reporting.statement_export import (
    ExportError,
    Table,
    _fmt_date,
    _locale,
    month_range,
    render_pdf,
    render_xlsx,
    tables,
)

BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")
MAX_BANK_LINES = 200

T = {
    "en": {
        "title": "Monthly close pack",
        "summary_ok": "Everything on the checklist is done.",
        "summary_warn": "Checklist items that still need attention: {n}.",
        "contents": "In this pack: the checklist, the financial statements for the month, the trial balance, "
                    "receivables and payables aging, the bank reconciliation and budget vs actual.",
        "checklist": "Checklist",
        "col_state": "State", "col_item": "Item", "col_detail": "Detail",
        "state": {"ok": "Done", "warn": "Needs attention", "info": "For information"},
        # the checklist
        "balanced": "Debits equal credits",
        "balanced_ok": "{n} journal lines this month, {amount} each side.",
        "balanced_warn": "Debits and credits differ by {amount} this month.",
        "bank": "Bank statements reconciled",
        "bank_none": "No bank statement lines are dated in this month.",
        "bank_ok": "All {total} bank lines this month are matched to the books.",
        "bank_warn": "{open} of {total} bank lines this month aren't matched yet.",
        "drafts": "No draft invoices",
        "drafts_ok": "No invoice dated this month is still a draft.",
        "drafts_warn": "Invoices dated this month still in draft: {n} ({numbers}).",
        "payroll": "Payroll posted",
        "payroll_ok": "The pay run for this month is posted.",
        "payroll_draft": "A pay run ending this month is still a draft.",
        "payroll_missing": "No pay run ends this month; employees on payroll: {n}.",
        "depreciation": "Depreciation posted",
        "depreciation_ok": "Every fixed asset's charge up to this month is posted.",
        "depreciation_warn": "Monthly depreciation charges up to this month not posted yet: {n} — run depreciation.",
        "fx": "Exchange rates in place",
        "fx_ok": "Every entry this month has its base-currency value.",
        "fx_warn": "Entries this month waiting for an exchange rate: {n} ({currencies}); they're left out of these totals.",
        "lock": "Books locked",
        "lock_ok": "The books are locked through {date}.",
        "lock_info": "Lock the books through {date} once everything above is done (Settings → closing date).",
        # the tables
        "tb": "Trial balance", "tb_note": "Balances are debit positive, credit in brackets.",
        "tb_cols": ("Opening", "Debit", "Credit", "Closing"),
        "ar": "Receivables aging", "ap": "Payables aging",
        "aging_sub": "Open balances at {date}, by days past due", "aging_none": "Nothing open at {date}.",
        "aging_cols": ("Current", "1–30", "31–60", "61–90", "90+", "Total"),
        "bank_t": "Bank reconciliation", "bank_cols": ("Lines", "Matched", "Not matched", "Set aside", "Closing balance"),
        "bank_note": "Lines dated this month. Set aside: duplicates and lines skipped on review.",
        "bank_open": "Bank lines not matched", "bank_open_cols": ("Date", "Debit", "Credit"),
        "budget_t": "Budget vs actual", "budget_cols": ("Budget", "Actual", "Left", "Used"),
        "budget_none": "No budgets are set for this month.",
        "total": "Total", "unknown": "Unknown",
    },
    "fa": {
        "title": "بسته بستن ماه",
        "summary_ok": "همه موارد فهرست بررسی انجام شده است.",
        "summary_warn": "{n} مورد از فهرست بررسی هنوز نیاز به رسیدگی دارد.",
        "contents": "در این بسته: فهرست بررسی، صورت‌های مالی ماه، تراز آزمایشی، سنی مطالبات و بدهی‌ها، "
                    "صورت مغایرت بانکی و مقایسه بودجه با عملکرد.",
        "checklist": "فهرست بررسی",
        "col_state": "وضعیت", "col_item": "مورد", "col_detail": "توضیح",
        "state": {"ok": "انجام شده", "warn": "نیاز به رسیدگی", "info": "برای اطلاع"},
        "balanced": "برابری بدهکار و بستانکار",
        "balanced_ok": "{n} ردیف سند در این ماه، هر طرف {amount}.",
        "balanced_warn": "جمع بدهکار و بستانکار این ماه {amount} اختلاف دارد.",
        "bank": "تطبیق صورت‌حساب‌های بانکی",
        "bank_none": "هیچ ردیف صورت‌حساب بانکی در این ماه نیست.",
        "bank_ok": "همه {total} ردیف بانکی این ماه با دفاتر تطبیق داده شده است.",
        "bank_warn": "{open} ردیف از {total} ردیف بانکی این ماه هنوز تطبیق داده نشده است.",
        "drafts": "نبود فاکتور پیش‌نویس",
        "drafts_ok": "هیچ فاکتوری با تاریخ این ماه پیش‌نویس نمانده است.",
        "drafts_warn": "{n} فاکتور با تاریخ این ماه هنوز پیش‌نویس است: {numbers}.",
        "payroll": "ثبت حقوق",
        "payroll_ok": "لیست حقوق این ماه ثبت شده است.",
        "payroll_draft": "لیست حقوقی که در این ماه تمام می‌شود هنوز پیش‌نویس است.",
        "payroll_missing": "هیچ لیست حقوقی در این ماه تمام نمی‌شود و {n} کارمند در حقوق و دستمزد هستند.",
        "depreciation": "ثبت استهلاک",
        "depreciation_ok": "استهلاک همه دارایی‌های ثابت تا این ماه ثبت شده است.",
        "depreciation_warn": "{n} استهلاک ماهانه تا این ماه ثبت نشده است — استهلاک را اجرا کنید.",
        "fx": "نرخ ارز",
        "fx_ok": "همه اسناد این ماه ارزش به ارز پایه دارند.",
        "fx_warn": "{n} سند این ماه منتظر نرخ ارز است ({currencies}) و در این جمع‌ها نیامده است.",
        "lock": "قفل دفاتر",
        "lock_ok": "دفاتر تا {date} قفل است.",
        "lock_info": "پس از انجام موارد بالا، دفاتر را تا {date} قفل کنید (تنظیمات ← تاریخ بستن).",
        "tb": "تراز آزمایشی", "tb_note": "مانده بدهکار مثبت و مانده بستانکار داخل پرانتز است.",
        "tb_cols": ("ابتدای ماه", "بدهکار", "بستانکار", "پایان ماه"),
        "ar": "سنی مطالبات", "ap": "سنی بدهی‌ها",
        "aging_sub": "مانده‌های باز در {date}، بر اساس روزهای گذشته از سررسید", "aging_none": "در {date} مانده بازی نیست.",
        "aging_cols": ("جاری", "۱ تا ۳۰", "۳۱ تا ۶۰", "۶۱ تا ۹۰", "بیش از ۹۰", "جمع"),
        "bank_t": "صورت مغایرت بانکی", "bank_cols": ("ردیف‌ها", "تطبیق‌شده", "تطبیق‌نشده", "کنار گذاشته", "مانده پایانی"),
        "bank_note": "ردیف‌های با تاریخ این ماه. کنار گذاشته: تکراری‌ها و ردیف‌هایی که در بازبینی رد شدند.",
        "bank_open": "ردیف‌های بانکی تطبیق‌نشده", "bank_open_cols": ("تاریخ", "بدهکار", "بستانکار"),
        "budget_t": "بودجه و عملکرد", "budget_cols": ("بودجه", "عملکرد", "باقی‌مانده", "مصرف"),
        "budget_none": "برای این ماه بودجه‌ای تعیین نشده است.",
        "total": "جمع", "unknown": "نامشخص",
    },
    "es": {
        'title': 'Paquete de cierre mensual',
        'summary_ok': 'Todo lo de la lista está hecho.',
        'summary_warn': 'Puntos de la lista que aún requieren atención: {n}.',
        'contents': 'En este paquete: la lista, los estados financieros del mes, el balance de comprobación, la antigüedad de cobros y pagos, la conciliación bancaria y el presupuesto frente a lo real.',
        'checklist': 'Lista de comprobación',
        'col_state': 'Estado',
        'col_item': 'Punto',
        'col_detail': 'Detalle',
        'state': {'ok': 'Hecho', 'warn': 'Requiere atención', 'info': 'Para información'},
        'balanced': 'El debe es igual al haber',
        'balanced_ok': '{n} líneas de asiento este mes, {amount} en cada lado.',
        'balanced_warn': 'El debe y el haber difieren en {amount} este mes.',
        'bank': 'Extractos bancarios conciliados',
        'bank_none': 'No hay líneas de extracto con fecha de este mes.',
        'bank_ok': 'Las {total} líneas bancarias del mes están casadas con los libros.',
        'bank_warn': '{open} de {total} líneas bancarias del mes aún no están casadas.',
        'drafts': 'Sin facturas en borrador',
        'drafts_ok': 'Ninguna factura de este mes sigue en borrador.',
        'drafts_warn': 'Facturas de este mes aún en borrador: {n} ({numbers}).',
        'payroll': 'Nómina contabilizada',
        'payroll_ok': 'La nómina de este mes está contabilizada.',
        'payroll_draft': 'Una nómina que termina este mes sigue en borrador.',
        'payroll_missing': 'Ninguna nómina termina este mes; empleados en nómina: {n}.',
        'depreciation': 'Amortización contabilizada',
        'depreciation_ok': 'La amortización de cada activo fijo hasta este mes está contabilizada.',
        'depreciation_warn': 'Cargos mensuales de amortización hasta este mes sin contabilizar: {n}; ejecuta la amortización.',
        'fx': 'Tipos de cambio disponibles',
        'fx_ok': 'Cada asiento de este mes tiene su valor en la moneda base.',
        'fx_warn': 'Asientos de este mes a la espera de un tipo de cambio: {n} ({currencies}); quedan fuera de estos totales.',
        'lock': 'Libros bloqueados',
        'lock_ok': 'Los libros están bloqueados hasta el {date}.',
        'lock_info': 'Bloquea los libros hasta el {date} cuando todo lo anterior esté hecho (Ajustes → fecha de cierre).',
        'tb': 'Balance de comprobación',
        'tb_note': 'Los saldos deudores en positivo, los acreedores entre paréntesis.',
        'tb_cols': ('Inicial', 'Debe', 'Haber', 'Final'),
        'ar': 'Antigüedad de cobros',
        'ap': 'Antigüedad de pagos',
        'aging_sub': 'Saldos pendientes al {date}, por días de retraso',
        'aging_none': 'Nada pendiente al {date}.',
        'aging_cols': ('Corriente', '1–30', '31–60', '61–90', '90+', 'Total'),
        'bank_t': 'Conciliación bancaria',
        'bank_cols': ('Líneas', 'Casadas', 'Sin casar', 'Apartadas', 'Saldo final'),
        'bank_note': 'Líneas con fecha de este mes. Apartadas: duplicadas y descartadas en la revisión.',
        'bank_open': 'Líneas bancarias sin casar',
        'bank_open_cols': ('Fecha', 'Debe', 'Haber'),
        'budget_t': 'Presupuesto frente a real',
        'budget_cols': ('Presupuesto', 'Real', 'Restante', 'Usado'),
        'budget_none': 'No hay presupuestos para este mes.',
        'total': 'Total',
        'unknown': 'Desconocido',
    },
    "ar": {
        'title': 'حزمة إقفال الشهر',
        'summary_ok': 'تم إنجاز كل ما في قائمة المراجعة.',
        'summary_warn': 'بنود في القائمة لا تزال تحتاج إلى متابعة: {n}.',
        'contents': 'في هذه الحزمة: قائمة المراجعة، والقوائم المالية للشهر، وميزان المراجعة، وأعمار الذمم المدينة والدائنة، والتسوية البنكية، والميزانية مقابل الفعلي.',
        'checklist': 'قائمة المراجعة',
        'col_state': 'الحالة',
        'col_item': 'البند',
        'col_detail': 'التفاصيل',
        'state': {'ok': 'منجز', 'warn': 'يحتاج متابعة', 'info': 'للعلم'},
        'balanced': 'المدين يساوي الدائن',
        'balanced_ok': '{n} سطر قيد هذا الشهر، {amount} في كل جانب.',
        'balanced_warn': 'يختلف المدين عن الدائن هذا الشهر بمقدار {amount}.',
        'bank': 'تسوية كشوف الحسابات البنكية',
        'bank_none': 'لا توجد سطور كشف بنكي بتاريخ هذا الشهر.',
        'bank_ok': 'كل سطور البنك هذا الشهر ({total}) مطابقة للدفاتر.',
        'bank_warn': '{open} من {total} سطر بنكي هذا الشهر لم تُطابق بعد.',
        'drafts': 'لا فواتير مسودة',
        'drafts_ok': 'لا توجد فاتورة بتاريخ هذا الشهر ما زالت مسودة.',
        'drafts_warn': 'فواتير هذا الشهر ما زالت مسودة: {n} ({numbers}).',
        'payroll': 'ترحيل الرواتب',
        'payroll_ok': 'رُحّلت دورة رواتب هذا الشهر.',
        'payroll_draft': 'دورة رواتب تنتهي هذا الشهر ما زالت مسودة.',
        'payroll_missing': 'لا تنتهي أي دورة رواتب هذا الشهر؛ عدد الموظفين في الرواتب: {n}.',
        'depreciation': 'ترحيل الإهلاك',
        'depreciation_ok': 'رُحّل إهلاك كل الأصول الثابتة حتى هذا الشهر.',
        'depreciation_warn': 'أقساط إهلاك شهرية حتى هذا الشهر لم تُرحّل بعد: {n} — شغّل الإهلاك.',
        'fx': 'أسعار الصرف متوفرة',
        'fx_ok': 'لكل قيد هذا الشهر قيمته بالعملة الأساسية.',
        'fx_warn': 'قيود هذا الشهر بانتظار سعر صرف: {n} ({currencies})؛ وهي مستبعدة من هذه المجاميع.',
        'lock': 'قفل الدفاتر',
        'lock_ok': 'الدفاتر مقفلة حتى {date}.',
        'lock_info': 'اقفل الدفاتر حتى {date} بعد إنجاز ما سبق (الإعدادات ← تاريخ الإقفال).',
        'tb': 'ميزان المراجعة',
        'tb_note': 'الأرصدة المدينة موجبة، والدائنة بين قوسين.',
        'tb_cols': ('افتتاحي', 'مدين', 'دائن', 'ختامي'),
        'ar': 'أعمار الذمم المدينة',
        'ap': 'أعمار الذمم الدائنة',
        'aging_sub': 'الأرصدة المفتوحة في {date}، حسب أيام التأخر',
        'aging_none': 'لا شيء مفتوح في {date}.',
        'aging_cols': ('جاري', '1–30', '31–60', '61–90', '90+', 'الإجمالي'),
        'bank_t': 'التسوية البنكية',
        'bank_cols': ('السطور', 'مطابقة', 'غير مطابقة', 'مستبعدة', 'الرصيد الختامي'),
        'bank_note': 'سطور بتاريخ هذا الشهر. المستبعدة: المكررة وما تُرك في المراجعة.',
        'bank_open': 'سطور بنكية غير مطابقة',
        'bank_open_cols': ('التاريخ', 'مدين', 'دائن'),
        'budget_t': 'الميزانية مقابل الفعلي',
        'budget_cols': ('الميزانية', 'الفعلي', 'المتبقي', 'المستخدم'),
        'budget_none': 'لا ميزانيات لهذا الشهر.',
        'total': 'الإجمالي',
        'unknown': 'غير معروف',
    },
}


@dataclass
class Month:
    key: str
    start: date
    end: date
    label: str
    locale: str
    lang: str
    calendar: str = "gregorian"     # the company's display calendar (Settings)

    def d(self, x) -> str:
        if self.calendar == "jalali":
            # the calendar the company chose, in the reader's digits ("1405/06/31")
            import jdatetime
            from app.services.documents.formatting import to_persian_digits
            j = jdatetime.date.fromgregorian(date=x)
            said = f"{j.year:04d}/{j.month:02d}/{j.day:02d}"
            return to_persian_digits(said) if self.lang == "fa" else said
        if self.lang == "fa" and self.locale != "ir":
            # a Gregorian date in Persian text: slashes keep the digits together (a hyphen splits them)
            from app.services.documents.formatting import to_persian_digits
            return to_persian_digits(x.strftime("%Y/%m/%d"))
        return _fmt_date(x, self.locale, self.lang)


def resolve_month(db: Session, key: str | None, lang: str | None = None, *, documents: bool = True) -> Month:
    """The month the pack is for: the key given, or last month in the company's calendar.
    The documents of a UK company are English (its statements have no Persian
    template); the checklist on screen can be read in Persian by anyone."""
    from app.services.calendar_periods import company_calendar, month_label, previous_month_key
    locale = _locale(db)
    lang = lang if lang in T else ("fa" if locale == "ir" else "en")
    if documents and (lang in ("es", "ar") or (lang == "fa" and locale == "uk")):
        lang = "en"       # the statements are written in Persian (Iranian template) or English
    calendar = company_calendar(db)
    key = key or previous_month_key(date.today(), calendar)
    start, end = month_range(key)
    return Month(key, start, end, month_label(key, lang), locale, lang, calendar)


def recent_months(db: Session, lang: str, n: int = 12) -> list[dict]:
    """The last ``n`` months, newest first, for the page's picker (this month included)."""
    from app.services.calendar_periods import company_calendar, last_n_months
    return [{"key": p.key, "label": p.label} for p in reversed(last_n_months(date.today(), n, company_calendar(db), lang))]


def _money(v: int, m: Month) -> str:
    from app.services.documents.formatting import to_persian_digits
    text = f"{int(v):,}"
    return to_persian_digits(text) if m.lang == "fa" else text


def _n(v, m: Month) -> str:
    from app.services.documents.formatting import to_persian_digits
    return to_persian_digits(str(v)) if m.lang == "fa" else str(v)


# --- the checklist -------------------------------------------------------------------------------------------------

def checklist(db: Session, m: Month) -> list[dict]:
    """What a month-end close checks, each ``ok``, ``warn`` or ``info``."""
    from app.models.bank_statement import BankStatementRow
    from app.models.employee_pay import EmployeePayProfile
    from app.models.fixed_asset import FixedAsset
    from app.models.invoice import Invoice
    from app.models.pay_run import PayRun
    from app.models.transaction import Transaction, TransactionLine
    from app.services import fixed_assets
    from app.services.period_service import get_closed_period

    W = T[m.lang]
    out: list[dict] = []

    def add(key, state, detail):
        out.append({"key": key, "state": state, "item": W[key], "detail": detail})

    live = (Transaction.date >= m.start, Transaction.date <= m.end, Transaction.deleted_at.is_(None))
    n_lines, dr, cr = db.execute(
        select(func.count(TransactionLine.id), func.coalesce(func.sum(TransactionLine.base_debit), 0),
               func.coalesce(func.sum(TransactionLine.base_credit), 0))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id).where(*live)).one()
    if int(dr) == int(cr):
        add("balanced", "ok", W["balanced_ok"].format(n=_n(n_lines, m), amount=_money(dr, m)))
    else:
        add("balanced", "warn", W["balanced_warn"].format(amount=_money(abs(int(dr) - int(cr)), m)))

    statuses = dict(db.execute(select(BankStatementRow.recon_status, func.count(BankStatementRow.id))
                               .where(BankStatementRow.tx_date >= m.start, BankStatementRow.tx_date <= m.end)
                               .group_by(BankStatementRow.recon_status)).all())
    total = sum(statuses.values())
    open_ = statuses.get("unmatched", 0)
    if not total:
        add("bank", "info", W["bank_none"])
    elif open_:
        add("bank", "warn", W["bank_warn"].format(open=_n(open_, m), total=_n(total, m)))
    else:
        add("bank", "ok", W["bank_ok"].format(total=_n(total, m)))

    drafts = db.execute(select(Invoice.number).where(Invoice.status == "draft", Invoice.issue_date >= m.start,
                                                     Invoice.issue_date <= m.end).order_by(Invoice.number)).scalars().all()
    if drafts:
        shown = ", ".join(drafts[:8]) + ("…" if len(drafts) > 8 else "")
        add("drafts", "warn", W["drafts_warn"].format(n=_n(len(drafts), m), numbers=shown))
    else:
        add("drafts", "ok", W["drafts_ok"])

    runs = db.execute(select(PayRun.status).where(PayRun.period_end >= m.start, PayRun.period_end <= m.end)).scalars().all()
    on_payroll = db.execute(select(func.count(EmployeePayProfile.id)).where(EmployeePayProfile.active.is_(True))).scalar() or 0
    if runs:
        if any(s == "draft" for s in runs):
            add("payroll", "warn", W["payroll_draft"])
        else:
            add("payroll", "ok", W["payroll_ok"])
    elif on_payroll:
        add("payroll", "warn", W["payroll_missing"].format(n=_n(on_payroll, m)))

    if db.execute(select(func.count(FixedAsset.id))).scalar():
        pending = fixed_assets.due(db, m.end)
        if pending:
            add("depreciation", "warn", W["depreciation_warn"].format(n=_n(len(pending), m)))
        else:
            add("depreciation", "ok", W["depreciation_ok"])

    waiting = db.execute(select(Transaction.currency).where(*live, Transaction.fx_rate.is_(None),
                                                            Transaction.fx_role.is_(None))).scalars().all()
    if waiting:
        add("fx", "warn", W["fx_warn"].format(n=_n(len(waiting), m),
                                              currencies=", ".join(sorted({(c or "").upper() for c in waiting}))))
    else:
        add("fx", "ok", W["fx_ok"])

    locked = get_closed_period(db)
    if locked and locked >= m.end:
        add("lock", "ok", W["lock_ok"].format(date=m.d(locked)))
    else:
        add("lock", "info", W["lock_info"].format(date=m.d(m.end)))
    return out


# --- the tables ----------------------------------------------------------------------------------------------------

def _line(label, values, *, style="line", indent=0) -> dict:
    return {"label": label, "indent": indent, "style": style, "values": list(values)}


def trial_balance(db: Session, m: Month) -> Table:
    from app.services.reporting.repository import trial_balance_rows
    W = T[m.lang]
    opening = {code: (name, d - c) for code, name, d, c in trial_balance_rows(db, date(1900, 1, 1), m.start - timedelta(days=1))}
    month = {code: (name, d, c) for code, name, d, c in trial_balance_rows(db, m.start, m.end)}
    rows, tot = [], [0, 0, 0, 0]
    for code in sorted(set(opening) | set(month)):
        name = (month.get(code) or opening.get(code))[0]
        o = opening.get(code, (name, 0))[1]
        _, d, c = month.get(code, (name, 0, 0))
        close = o + d - c
        if not (o or d or c):
            continue
        rows.append(_line(f"{code}  {name}", [o, d, c, close]))
        tot = [tot[0] + o, tot[1] + d, tot[2] + c, tot[3] + close]
    rows.append(_line(W["total"], tot, style="total"))
    return Table("trial_balance", W["tb"], m.label, list(W["tb_cols"]), rows, note=W["tb_note"])


def aging(db: Session, m: Month, kind: str) -> Table:
    from app.api.manager_reports import _open_invoice_aging
    W = T[m.lang]
    rep = _open_invoice_aging(db, kind, None, m.end)
    party = "vendor" if kind == "purchase" else "customer"
    by: dict[str, list[int]] = {}
    for it in rep["items"]:
        row = by.setdefault(it[party] or W["unknown"], [0] * len(BUCKETS))
        row[BUCKETS.index(it["aging_bucket"])] += int(it["balance_due"])
    rows, tot = [], [0] * (len(BUCKETS) + 1)
    for name in sorted(by, key=lambda k: -sum(by[k])):
        vals = by[name] + [sum(by[name])]
        rows.append(_line(name, vals))
        tot = [a + b for a, b in zip(tot, vals)]
    if rows:
        rows.append(_line(W["total"], tot, style="total"))
    return Table("ar_aging" if kind == "sales" else "ap_aging", W["ar" if kind == "sales" else "ap"],
                 W["aging_sub"].format(date=m.d(m.end)), list(W["aging_cols"]), rows,
                 note="" if rows else W["aging_none"].format(date=m.d(m.end)), flow=kind == "purchase")


def bank(db: Session, m: Month) -> list[Table]:
    from app.models.bank_statement import BankStatement, BankStatementRow
    W = T[m.lang]
    lines = db.execute(select(BankStatementRow).where(BankStatementRow.tx_date >= m.start, BankStatementRow.tx_date <= m.end)
                       .order_by(BankStatementRow.tx_date, BankStatementRow.row_index)).scalars().all()
    if not lines:
        return []
    statements = {s.id: s for s in db.execute(select(BankStatement).where(
        BankStatement.id.in_({ln.statement_id for ln in lines}))).scalars()}
    summary: dict = {}
    for ln in lines:
        s = summary.setdefault(ln.statement_id, {"n": 0, "matched": 0, "open": 0, "aside": 0, "balance": None})
        s["n"] += 1
        if ln.recon_status in ("matched", "partial"):
            s["matched"] += 1
        elif ln.recon_status == "unmatched":
            s["open"] += 1
        else:
            s["aside"] += 1
        if ln.balance is not None:
            s["balance"] = int(ln.balance)                           # the last line's running balance
    rows = []
    for sid, s in summary.items():
        st = statements.get(sid)
        label = " · ".join(x for x in (st.bank_name if st else "", (st.account_number or "") if st else "",
                                       (st.currency or "") if st else "") if x)
        rows.append(_line(label or W["unknown"], [s["n"], s["matched"], s["open"], s["aside"], s["balance"]]))
    out = [Table("bank_reconciliation", W["bank_t"], m.label, list(W["bank_cols"]), rows, note=W["bank_note"])]
    open_lines = [ln for ln in lines if ln.recon_status == "unmatched"][:MAX_BANK_LINES]
    if open_lines:
        out.append(Table("bank_unmatched", W["bank_open"], m.label, list(W["bank_open_cols"]), [
            _line((ln.description or ln.counterparty or ln.reference or "—")[:90],
                  [m.d(ln.tx_date), int(ln.debit or 0) or None, int(ln.credit or 0) or None]) for ln in open_lines],
            flow=True))
    return out


def budgets(db: Session, m: Month) -> Table:
    from app.services.budget_service import budget_utilization
    W = T[m.lang]
    rows, tot = [], [0, 0, 0]
    for b in sorted(budget_utilization(db, m.key), key=lambda b: b["category"]):
        rows.append(_line(b["label"], [b["limit_amount"], b["actual_amount"], b["variance"],
                                          f"{round(b['utilization_pct'])}%"]))
        tot = [tot[0] + b["limit_amount"], tot[1] + b["actual_amount"], tot[2] + b["variance"]]
    if rows:
        used = f"{round(tot[1] / tot[0] * 100)}%" if tot[0] else ""
        rows.append(_line(W["total"], tot + [used], style="total"))
    return Table("budgets", W["budget_t"], m.label, list(W["budget_cols"]), rows,
                 note="" if rows else W["budget_none"], flow=True)


# --- the pack ------------------------------------------------------------------------------------------------------

def journal_csv(db: Session, m: Month) -> bytes:
    """Every line of every live journal in the month, in date order."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    buf = io.StringIO()
    from app.core.spreadsheet import csv_bytes, csv_writer
    w = csv_writer(buf)
    jalali = m.locale == "ir"
    w.writerow(["date"] + (["date_jalali"] if jalali else []) + [
        "reference", "description", "account_code", "account_name", "line_description",
        "debit", "credit", "currency", "base_debit", "base_credit"])
    q = (select(Transaction, TransactionLine, Account)
         .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
         .join(Account, Account.id == TransactionLine.account_id)
         .where(Transaction.date >= m.start, Transaction.date <= m.end, Transaction.deleted_at.is_(None))
         .order_by(Transaction.date, Transaction.created_at, Transaction.id, TransactionLine.debit.desc()))
    for t, ln, acc in db.execute(q):
        extra = []
        if jalali:
            import jdatetime
            extra = [jdatetime.date.fromgregorian(date=t.date).strftime("%Y/%m/%d")]
        w.writerow([t.date.isoformat()] + extra + [
            t.reference or "", t.description or "", acc.code, acc.name, ln.line_description or "",
            int(ln.debit or 0), int(ln.credit or 0), t.currency or "",
            "" if ln.base_debit is None else int(ln.base_debit), "" if ln.base_credit is None else int(ln.base_credit)])
    return csv_bytes(buf)


def summary(items: list[dict], lang: str) -> str:
    W = T[lang]
    n = sum(1 for i in items if i["state"] == "warn")
    if not n:
        return W["summary_ok"]
    text = W["summary_warn"].format(n=n)
    if lang == "fa":
        from app.services.documents.formatting import to_persian_digits
        text = to_persian_digits(text)
    return text


def build(db: Session, m: Month) -> dict:
    """Everything in the pack: the checklist, the statement tables, the extra tables, the metadata.
    The bank statement lines are bank:read data — a viewer's pack leaves them out."""
    from app.core.redaction import may_see_identity
    statements, meta = tables(db, from_date=m.start, to_date=m.end, lang=m.lang)
    extra = [trial_balance(db, m), aging(db, m, "sales"), aging(db, m, "purchase"),
             *(bank(db, m) if may_see_identity() else []), budgets(db, m)]
    return {"checklist": checklist(db, m), "statements": statements, "extra": extra, "meta": meta}


def pdf(db: Session, m: Month, pack: dict | None = None) -> bytes:
    pack = pack or build(db, m)
    W = T[m.lang]
    cover = {"title": W["title"], "period": f"{m.label} — {m.d(m.start)} – {m.d(m.end)}",
             "summary": summary(pack["checklist"], m.lang), "checklist": pack["checklist"], "contents": W["contents"]}
    return render_pdf(db, pack["statements"] + pack["extra"], pack["meta"], cover=cover)


def xlsx(db: Session, m: Month, pack: dict | None = None) -> bytes:
    from app.services.documents.branding import build_brand
    pack = pack or build(db, m)
    W = T[m.lang]
    lead = [(W["checklist"], [[W["col_state"], W["col_item"], W["col_detail"]]] + [
        [W["state"][i["state"]], i["item"], i["detail"]] for i in pack["checklist"]])]
    return render_xlsx(pack["statements"] + pack["extra"], pack["meta"], company=build_brand(db)["issuer"]["name"],
                       lead_sheets=lead)


def zip_pack(db: Session, m: Month) -> bytes:
    pack = build(db, m)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"close-pack-{m.key}.pdf", pdf(db, m, pack))
        z.writestr(f"close-pack-{m.key}.xlsx", xlsx(db, m, pack))
        z.writestr(f"journal-{m.key}.csv", journal_csv(db, m))
    return buf.getvalue()


__all__ = ["ExportError", "Month", "build", "checklist", "journal_csv", "pdf", "recent_months",
           "resolve_month", "xlsx", "zip_pack"]
