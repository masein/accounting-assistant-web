"""Can I afford it? (scenarios AM1–AM10).

From the owner (2026-10-20): «I want to buy something, and the system checks my
finances and tells me whether I can or can't buy it, or by installments, or
reminds me to save if I set a goal». Worked out from the books, never guessed:

* the money in the cash and bank places today;
* the last three months' average income and spending (the company's calendar);
* the payments due in the next 30 days (installments, cheques to pay);
* what the savings goals already need each month.

A month's spending plus those payments is the cushion, kept untouched. What
is above it is free to spend; what each month leaves over, less the goals, is
spare. The answer, in this order:

* ``now``          — the free money covers it;
* ``wait``         — saving the shortfall from what is spare takes three months or less;
* ``installments`` — the shortest of 3, 6, 12 or 24 equal monthly payments that takes
  at most half of what is spare;
* ``save``         — no plan fits: what to put aside each month, and for how long;
* ``not_now``      — nothing is spare (spending at or above income);
* ``too_new``      — less than a month of history: only the cash part.

Investment advice is not given (the owner paused it).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import average, plain, to_places

HISTORY_MONTHS = 3
DUE_DAYS = 30
WAIT_MONTHS = 3
PLANS = (3, 6, 12, 24)
SHARE_OF_SPARE = 0.5


class AffordError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _cash_now(db: Session, today: date) -> Any:
    """The money in the cash and bank places today, at its base value."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.ai_accountant.cash_tools import cash_account_codes
    from app.services.locale_service import get_reporting_locale
    from app.services.proactive_reviews import _personal
    codes = cash_account_codes(db, locale=get_reporting_locale(db), mode="personal" if _personal(db) else "default")
    if not codes:
        return 0
    d, c = db.execute(
        select(func.coalesce(func.sum(TransactionLine.base_debit), 0), func.coalesce(func.sum(TransactionLine.base_credit), 0))
        .join(Account, Account.id == TransactionLine.account_id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Account.code.in_(codes), Transaction.deleted_at.is_(None), Transaction.date <= today)).one()
    return plain(d or 0) - plain(c or 0)


def _due_soon(db: Session, today: date, base: str) -> Any:
    from app.services.commitment_service import outstanding
    return plain(sum((r.amount for r in outstanding(db, direction="pay")
                      if r.due_date <= today + timedelta(days=DUE_DAYS)), 0))


def _goals_need(db: Session, today: date) -> Any:
    try:
        from app.services.personal_goals import list_goals
        return plain(sum((g["needed_per_month"] or 0 for g in list_goals(db, today=today)
                          if not g["reached"] and g.get("needed_per_month")), 0))
    except Exception:  # noqa: BLE001 — a company has no goals
        return 0


def check(db: Session, *, price, currency: str | None = None, item: str | None = None,
          today: date | None = None) -> dict:
    """The figures and the answer (module docstring). ``price`` in ``currency`` (the books' by default)."""
    from app.services.calendar_periods import company_calendar, key_bounds, previous_month_key, shift
    from app.services.fx_base import base_currency, rate_to_base
    from app.services.report_card import _flows
    today = today or date.today()
    base = base_currency(db)
    ccy = (currency or base).strip().upper()
    price = plain(price)
    if price <= 0:
        raise AffordError("invalid_amount", "The price must be more than zero.")
    rate = None
    price_base = price
    if ccy != base:
        rate = rate_to_base(db, ccy, today)
        if not rate:
            raise AffordError("no_rate", f"No {ccy}→{base} rate on file: ask for the price in {base}.")
        price_base = plain(to_places(price * rate, base))

    cal = company_calendar(db)
    last = previous_month_key(today, cal)
    y, m = int(last[:4]), int(last[5:7])
    keys = ["%04d-%02d" % shift(y, m, -i) for i in range(HISTORY_MONTHS)]
    flows = [_flows(db, *key_bounds(k)) for k in keys]
    active = [f for f in flows if f[0] or f[1]]
    income = plain(average([f[0] for f in active], base)) if active else None
    spending = plain(average([f[1] for f in active], base)) if active else None

    cash = _cash_now(db, today)
    due = _due_soon(db, today, base)
    goals = _goals_need(db, today)
    cushion = (spending or 0) + due
    free = cash - cushion
    spare = (income - spending) if active else None
    spare_free = (spare - goals) if spare is not None else None

    facts: dict[str, Any] = {
        "item": (item or "").strip()[:80] or None, "price": price, "currency": ccy, "price_base": price_base,
        "base": base, "rate": rate, "calendar": cal, "today": today.isoformat(),
        "cash": cash, "after": cash - price_base, "cushion": cushion, "due_30": due, "free": free,
        "income": income, "spending": spending, "months": len(active), "goals_need": goals,
        "spare": spare, "spare_free": spare_free,
        "wait_months": None, "plan": None, "plan_each": None,
    }
    if free >= price_base:
        facts["verdict"] = "now"
        return facts
    if not active:
        facts["verdict"] = "too_new"
        return facts
    short = price_base - max(free, 0)
    facts["short"] = short
    if spare_free is not None and spare_free > 0:
        facts["wait_months"] = math.ceil(short / spare_free)
        for n in PLANS:
            each = price_base / n
            if each <= spare_free * SHARE_OF_SPARE:
                facts["plan"], facts["plan_each"] = n, plain(to_places(each, base))
                break
    if facts["wait_months"] is not None and facts["wait_months"] <= WAIT_MONTHS:
        facts["verdict"] = "wait"
    elif facts["plan"]:
        facts["verdict"] = "installments"
    elif facts["wait_months"]:
        facts["verdict"] = "save"
    else:
        facts["verdict"] = "not_now"
    return facts


# --- the card ---------------------------------------------------------------------------------------------------

A: dict[str, dict[str, str]] = {
    "title": {"en": "{item} for {price}?", "fa": "{item} به قیمت {price}؟", "es": "¿{item} por {price}?",
              "ar": "{item} بسعر {price}؟"},
    "title_plain": {"en": "Something for {price}?", "fa": "خریدی به قیمت {price}؟", "es": "¿Una compra de {price}?",
                    "ar": "شراء بسعر {price}؟"},
    "now": {"en": "Yes — you can buy it now", "fa": "بله، همین حالا می‌توانید", "es": "Sí, puedes comprarlo ya",
            "ar": "نعم، يمكنك شراؤه الآن"},
    "now_detail": {"en": "{after} stays after it, above your {cushion} cushion.",
                   "fa": "بعد از خرید {after} می‌ماند، بالاتر از پشتوانهٔ {cushion}.",
                   "es": "Te quedan {after} después, por encima de tu colchón de {cushion}.",
                   "ar": "يبقى {after} بعد الشراء، فوق احتياطك البالغ {cushion}."},
    "wait": {"en": "Wait about {months} month(s) and pay cash", "fa": "حدود {months} ماه صبر کنید و نقد بخرید",
             "es": "Espera unos {months} mes(es) y paga al contado", "ar": "انتظر نحو {months} شهر وادفع نقدًا"},
    "wait_detail": {"en": "{short} short today, and {spare} a month to spare.",
                    "fa": "امروز {short} کم دارید و ماهی {spare} اضافه می‌ماند.",
                    "es": "Hoy te faltan {short} y te sobran {spare} al mes.",
                    "ar": "ينقصك اليوم {short}، ويفيض لديك {spare} شهريًا."},
    "installments": {"en": "In {n} installments of {each}", "fa": "در {n} قسط ماهی {each}",
                     "es": "En {n} cuotas de {each}", "ar": "على {n} أقساط بقيمة {each}"},
    "installments_detail": {"en": "That is within half of the {spare} you have spare each month.",
                            "fa": "این کمتر از نصف {spare} است که هر ماه اضافه می‌ماند.",
                            "es": "Es menos de la mitad de los {spare} que te sobran al mes.",
                            "ar": "وهذا ضمن نصف {spare} الفائض لديك كل شهر."},
    "or_save": {"en": "Saving for it instead takes about {months} months.", "fa": "پس‌انداز برایش حدود {months} ماه طول می‌کشد.",
                "es": "Ahorrar para ello llevaría unos {months} meses.", "ar": "الادخار له يستغرق نحو {months} أشهر."},
    "save": {"en": "Save {each} a month, about {months} months", "fa": "ماهی {each} کنار بگذارید، حدود {months} ماه",
             "es": "Ahorra {each} al mes, unos {months} meses", "ar": "ادّخر {each} شهريًا، نحو {months} أشهر"},
    "save_detail": {"en": "No plan of up to 24 monthly payments fits what you have spare.",
                    "fa": "هیچ اقساطی تا ۲۴ ماه با آنچه هر ماه اضافه می‌ماند جور نمی‌شود.",
                    "es": "Ningún plan de hasta 24 cuotas cabe en lo que te sobra al mes.",
                    "ar": "لا تناسب أي خطة حتى 24 قسطًا ما يفيض لديك شهريًا."},
    "not_now": {"en": "Not now", "fa": "فعلاً نه", "es": "Ahora no", "ar": "ليس الآن"},
    "not_now_detail": {"en": "Over the last three months you spent {spent} a month against {income} of income: nothing is left over yet.",
                       "fa": "در سه ماه گذشته ماهی {spent} خرج کردید در برابر {income} درآمد: هنوز چیزی اضافه نمی‌ماند.",
                       "es": "En los últimos tres meses gastaste {spent} al mes frente a {income} de ingresos: aún no sobra nada.",
                       "ar": "في الأشهر الثلاثة الأخيرة أنفقت {spent} شهريًا مقابل دخل {income}: لا يفيض شيء بعد."},
    "too_new": {"en": "Only the cash part, for now", "fa": "فعلاً فقط بخش نقدی", "es": "Por ahora, solo el efectivo",
                "ar": "الجزء النقدي فقط حاليًا"},
    "too_new_detail": {"en": "{free} is free to spend today; the monthly picture needs a month of entries.",
                       "fa": "امروز {free} آزاد برای خرج دارید؛ تصویر ماهانه یک ماه ثبت لازم دارد.",
                       "es": "Hoy tienes {free} libres; la foto mensual necesita un mes de registros.",
                       "ar": "لديك اليوم {free} متاحة للإنفاق؛ الصورة الشهرية تحتاج شهرًا من القيود."},
    "now_bar": {"en": "Now", "fa": "الان", "es": "Ahora", "ar": "الآن"},
    "after_bar": {"en": "After", "fa": "بعد از خرید", "es": "Después", "ar": "بعد الشراء"},
    "cushion": {"en": "cushion", "fa": "پشتوانه", "es": "colchón", "ar": "الاحتياطي"},
    "chart": {"en": "Your money, before and after", "fa": "پولتان، قبل و بعد", "es": "Tu dinero, antes y después",
              "ar": "مالك، قبل وبعد"},
    "ring_label": {"en": "Free to spend", "fa": "آزاد برای خرج", "es": "Libre para gastar", "ar": "متاح للإنفاق"},
    "ring_text": {"en": "{pct} of the price", "fa": "{pct} از قیمت", "es": "{pct} del precio", "ar": "{pct} من السعر"},
    "price": {"en": "Price", "fa": "قیمت", "es": "Precio", "ar": "السعر"},
    "f_income": {"en": "Income about {x} a month", "fa": "درآمد حدود ماهی {x}", "es": "Ingresos de unos {x} al mes",
                 "ar": "دخل نحو {x} شهريًا"},
    "f_spending": {"en": "Spending about {x} a month", "fa": "خرج حدود ماهی {x}", "es": "Gastos de unos {x} al mes",
                   "ar": "إنفاق نحو {x} شهريًا"},
    "f_due": {"en": "{x} due in the next 30 days", "fa": "{x} سررسید تا ۳۰ روز آینده", "es": "{x} vencen en 30 días",
              "ar": "{x} مستحقة خلال 30 يومًا"},
    "f_goals": {"en": "{x} a month already set aside for goals", "fa": "ماهی {x} برای هدف‌ها کنار گذاشته شده",
                "es": "{x} al mes ya reservados para metas", "ar": "{x} شهريًا مخصصة للأهداف"},
    "f_rate": {"en": "1 {ccy} = {rate} {base}, the latest rate on file", "fa": "هر {ccy} = {rate} {base}، آخرین نرخ ثبت‌شده",
               "es": "1 {ccy} = {rate} {base}, la última tasa registrada", "ar": "1 {ccy} = {rate} {base}، آخر سعر مسجل"},
    "interest": {"en": "Plus any interest or fee the seller charges.", "fa": "به‌علاوهٔ هر سود یا کارمزدی که فروشنده بگیرد.",
                 "es": "Más los intereses o comisiones que cobre el vendedor.", "ar": "يضاف إليه أي فائدة أو رسوم يفرضها البائع."},
    "ideas": {"en": "Ideas", "fa": "پیشنهادها", "es": "Ideas", "ar": "اقتراحات"},
    "goal_say": {"en": "Make it a savings goal: {price} by {date}; I'll remind you each week.",
                 "fa": "یک هدف پس‌انداز بسازید: {price} تا {date}؛ هر هفته یادآوری می‌کنم.",
                 "es": "Hazlo una meta de ahorro: {price} para {date}; te lo recordaré cada semana.",
                 "ar": "اجعله هدف ادخار: {price} بحلول {date}؛ سأذكّرك كل أسبوع."},
    "goal_ask": {"en": "Make a savings goal of {price} for {item} by {date}", "fa": "یک هدف پس‌انداز {price} برای {item} تا {date} بساز",
                 "es": "Crea una meta de ahorro de {price} para {item} para {date}",
                 "ar": "أنشئ هدف ادخار بقيمة {price} لـ{item} بحلول {date}"},
    "thing": {"en": "it", "fa": "این خرید", "es": "esto", "ar": "هذه الشراء"},
    "less_say": {"en": "See where the money goes each month, and what could give.",
                 "fa": "ببینید پول هر ماه کجا می‌رود و کجا می‌شود کم کرد.",
                 "es": "Mira adónde va el dinero cada mes y qué podría ceder.",
                 "ar": "اعرف أين يذهب المال كل شهر وما يمكن تقليله."},
    "less_ask": {"en": "How can I spend less?", "fa": "چطور خرجم را کم کنم؟", "es": "¿Cómo puedo gastar menos?",
                 "ar": "كيف أنفق أقل؟"},
}
_EMOJI = {"now": "✅", "wait": "🧘", "installments": "🗓️", "save": "🐷", "not_now": "✋", "too_new": "🌱"}
_MOOD = {"now": "good", "wait": "calm", "installments": "calm", "save": "watch", "not_now": "watch", "too_new": "calm"}


def card(f: dict, lang: str) -> dict:
    """The answer as the review card (scenario AM9)."""
    from app.services.proactive_reviews import _day, _digits, _money, _pct, _say
    lang = lang if lang in ("en", "fa", "es", "ar") else "en"
    base, cal = f["base"], f.get("calendar", "gregorian")
    m = lambda v: _money(v, base, lang)                 # noqa: E731
    verdict = f["verdict"]
    price_said = _money(f["price"], f["currency"], lang)
    title = (_say(A, "title", lang, item=f["item"], price=price_said) if f.get("item")
             else _say(A, "title_plain", lang, price=price_said))
    months = _digits(str(f["wait_months"]), lang) if f.get("wait_months") is not None else None
    spare = m(f["spare_free"]) if f.get("spare_free") is not None else None
    if verdict == "now":
        headline = _say(A, "now", lang)
        detail = _say(A, "now_detail", lang, after=m(f["after"]), cushion=m(f["cushion"]))
    elif verdict == "wait":
        headline = _say(A, "wait", lang, months=months)
        detail = _say(A, "wait_detail", lang, short=m(f["short"]), spare=spare)
    elif verdict == "installments":
        headline = _say(A, "installments", lang, n=_digits(str(f["plan"]), lang), each=m(f["plan_each"]))
        detail = _say(A, "installments_detail", lang, spare=spare) + (
            " " + _say(A, "or_save", lang, months=months) if months else "")
    elif verdict == "save":
        from app.services.proactive_reviews import _nice
        # up to a round figure: «ماهی ۱۵٬۰۰۰٬۰۰۰», not 14,642,858 — still enough over those months
        each = _nice(f["short"] / f["wait_months"]) if f.get("wait_months") else f["spare_free"]
        headline = _say(A, "save", lang, each=m(each), months=months)
        detail = _say(A, "save_detail", lang)
    elif verdict == "not_now":
        headline = _say(A, "not_now", lang)
        detail = _say(A, "not_now_detail", lang, spent=m(f["spending"] or 0), income=m(f["income"] or 0))
    else:
        headline = _say(A, "too_new", lang)
        detail = _say(A, "too_new_detail", lang, free=m(max(f["free"], 0)))

    facts = [detail]
    if f.get("income") is not None:
        facts += [_say(A, "f_income", lang, x=m(f["income"])), _say(A, "f_spending", lang, x=m(f["spending"]))]
    if f.get("due_30"):
        facts.append(_say(A, "f_due", lang, x=m(f["due_30"])))
    if f.get("goals_need"):
        facts.append(_say(A, "f_goals", lang, x=m(f["goals_need"])))
    if f.get("rate"):
        facts.append(_say(A, "f_rate", lang, ccy=f["currency"], base=base, rate=_digits(f"{f['rate']:,.2f}", lang)))

    ideas = []
    if verdict in ("wait", "installments", "save") and f.get("wait_months"):
        on = (date.fromisoformat(f["today"]) + timedelta(days=31 * f["wait_months"])).isoformat()
        when = _day(on, cal, lang)
        goal_price = m(f["price_base"])
        ideas.append({"key": "goal", "icon": "🎯", "text": _say(A, "goal_say", lang, price=goal_price, date=when),
                      "ask": _say(A, "goal_ask", lang, price=goal_price, date=when, item=f.get("item") or A["thing"][lang])})
    if verdict in ("not_now", "save"):
        ideas.append({"key": "spend_less", "icon": "💡", "text": _say(A, "less_say", lang), "ask": _say(A, "less_ask", lang)})

    pct = 0.0 if not f["price_base"] else max(0.0, min(100.0, float(max(f["free"], 0)) / float(f["price_base"]) * 100))
    return {
        "kind": "afford", "mood": _MOOD[verdict], "emoji": _EMOJI[verdict], "title": title, "currency": base,
        "headline": {"label": headline, "value": f["price_base"], "display": m(f["price_base"]), "delta": None},
        "chart": {"title": A["chart"][lang], "usual": f["cushion"] or None, "usual_label": A["cushion"][lang], "legend": [],
                  "bars": [{"label": A["now_bar"][lang], "value": max(f["cash"], 0), "display": m(f["cash"]), "current": False},
                           {"label": A["after_bar"][lang], "value": max(f["after"], 0), "display": m(f["after"]),
                            "current": True}]},
        "ring": {"pct": round(pct, 1), "label": A["ring_label"][lang], "display": m(max(f["free"], 0)),
                 "text": _say(A, "ring_text", lang, pct=_pct(pct, lang))},
        "categories_title": None, "categories": [], "facts": facts[:6],
        "ideas_title": A["ideas"][lang], "ideas": ideas[:2],
        "note": A["interest"][lang] if verdict == "installments" else None,
    }
