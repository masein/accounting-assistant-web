"""Typed reply blocks for chat clients (roadmap ROADMAP_ANDROID_CHAT P0.3).

A reply is a list of blocks the phone draws natively: the data the tools
read (a figure, a table), the vouchers to confirm, an import's review card,
then the accountant's short sentence. The numbers in a block come from the
tool results and the ledger, never from the model's prose; the model only
decides which tools to call.

Every block carries ``type``, a stable ``id`` and a ``fallback_text``: an older
app, a bot or a screen reader still gets a sentence for a block it can't draw.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.money import json_amount, grouped, persian_figures

# Data blocks drawn per reply at most, newest tool calls first kept.
MAX_DATA_BLOCKS = 3


def _get(obj: Any, key: str, default=None):
    """Proposals arrive as dicts (orchestrator) or models (the API's ChatProposal)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def display_date(iso: str | None, calendar: str, lang: str) -> str | None:
    """The date as this company writes it: «۱۸ مهر ۱۴۰۵» or "10 Oct 2026"."""
    if not iso:
        return None
    d = date.fromisoformat(iso)
    if calendar == "jalali":
        from app.utils.jalali import JALALI_MONTH_NAMES_EN, format_jalali_long, gregorian_to_jalali
        if lang == "fa":
            return format_jalali_long(d)
        y, m, day = gregorian_to_jalali(d)
        return f"{day} {JALALI_MONTH_NAMES_EN[m - 1]} {y}"
    # a Gregorian company, said in the reader's language: «۵ اکتبر ۲۰۲۶», not "5 Oct 2026" inside Persian words
    if lang == "fa":
        from app.services.calendar_periods import GREGORIAN_MONTHS_FA
        from app.utils.jalali import to_persian_digits
        return to_persian_digits(f"{d.day} {GREGORIAN_MONTHS_FA[d.month - 1]} {d.year}")
    if lang in _GREGORIAN_MONTHS:
        return f"{d.day} {_GREGORIAN_MONTHS[lang][d.month - 1]} {d.year}"
    return f"{d.day} {d.strftime('%b %Y')}"


_GREGORIAN_MONTHS = {
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
}


def _account_names(db: Session, codes: Iterable[str]) -> dict[str, str]:
    from app.models.account import Account
    codes = sorted({str(c) for c in codes if c})
    if not codes:
        return {}
    rows = db.execute(select(Account.code, Account.name).where(Account.code.in_(codes))).all()
    return {str(code): name for code, name in rows}


def _old_codes(db: Session, codes: Iterable[str]) -> dict[str, str]:
    """An account's first code in the user's own books, by its code here: a
    card says «۶۱۳۱۰۲ ملزومات اداری (۱۳۰۲)», both (scenario AH7)."""
    from app.models.account import Account
    codes = sorted({str(c) for c in codes if c})
    if not codes:
        return {}
    rows = db.execute(select(Account.code, Account.source_codes)
                      .where(Account.code.in_(codes), Account.source_codes.isnot(None))).all()
    return {str(code): old.split(",")[0] for code, old in rows if old}


# --- proposals ------------------------------------------------------------------------

def card_names(db: Session, proposals: list | None) -> dict[str, str]:
    """The names of the accounts the cards' lines post to, by code; a place
    the card makes on confirm («کیف پول سبز», 112001) by its own name."""
    all_lines = [ln for p in (proposals or []) for ln in ((_get(p, "preview") or {}).get("lines") or [])
                 if isinstance(ln, dict)]
    names = _account_names(db, (ln.get("account_code") for ln in all_lines))
    for p in proposals or []:
        preview = _get(p, "preview") or {}
        if isinstance(preview, dict) and preview.get("new_account") and preview.get("account_code"):
            names.setdefault(preview["account_code"], preview.get("place") or preview["account_code"])   # made on confirm
    return names


_EDITABLE = {"propose_create_transaction"}           # kept in step with proposal_edit.EDITABLE (a test checks)


def card_people(db: Session, p: Any, preview: Any) -> list[dict]:
    """The people a card names, as the phone and the web say them under its
    description: those already in the books (its ``entity_links``) and those
    confirming makes (``new``). Banks are its lines, not people (scenario AK4)."""
    import uuid as _uuid

    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import normalize_match_name
    if not isinstance(preview, dict):
        return []
    out: list[dict] = []
    ids = []
    for link in preview.get("entity_links") or []:
        try:
            ids.append(_uuid.UUID(str((link or {}).get("entity_id"))))
        except (ValueError, TypeError, AttributeError):
            continue
    if ids:
        for name, etype in db.execute(select(Entity.name, Entity.type).where(Entity.id.in_(ids))).all():
            if etype != "bank":
                out.append({"name": name, "new": False})
    for ne in preview.get("new_entities") or _get(p, "new_entities") or []:
        if isinstance(ne, dict) and ne.get("name") and ne.get("type") != "bank":
            out.append({"name": ne["name"], "new": True})
    seen: set[str] = set()
    return [x for x in out if not (normalize_match_name(x["name"]) in seen or seen.add(normalize_match_name(x["name"])))]


def proposal_block(db: Session, p: Any, *, calendar: str, lang: str, names: dict[str, str] | None = None) -> dict:
    """A voucher to confirm: what, how much, which accounts, when."""
    preview = _get(p, "preview") or {}
    summary = (_get(p, "summary") or "").strip()
    lines_in = preview.get("lines") if isinstance(preview, dict) else None
    lines = []
    amount = None
    if isinstance(lines_in, list) and lines_in:
        names = names if names is not None else _account_names(db, (ln.get("account_code") for ln in lines_in))
        olds = _old_codes(db, (ln.get("account_code") for ln in lines_in))
        for ln in lines_in:
            code = str(ln.get("account_code") or "")
            name = names.get(code, code)
            if olds.get(code):
                old = olds[code]
                if lang == "fa":
                    from app.utils.jalali import to_persian_digits
                    old = to_persian_digits(old)
                name = f"{name} ({old})"
            lines.append({"account": code, "name": name,
                          "debit": json_amount(ln.get("debit") or 0), "credit": json_amount(ln.get("credit") or 0)})
        amount = sum(x["debit"] for x in lines)
    elif isinstance(preview, dict):
        for key in ("amount", "total", "gross", "net"):
            if isinstance(preview.get(key), (int, float)):
                amount = json_amount(preview[key])
                break
    currency = (preview.get("currency") if isinstance(preview, dict) else None) or None
    if currency is None and amount is not None:
        from app.services.fx_base import base_currency
        currency = base_currency(db)
    iso = None
    if isinstance(preview, dict):
        for key in ("date", "issue_date", "pay_date", "due_date", "on"):
            iso = _iso(preview.get(key))
            if iso:
                break
    title = (preview.get("description") if isinstance(preview, dict) else None) or (summary.splitlines() or [""])[0]
    token = str(_get(p, "confirmation_token"))
    needs_approval = bool(_get(p, "needs_approval"))
    return {
        "type": "proposal",
        "id": f"proposal:{token}",
        "token": token,
        "tool": _get(p, "tool_name") or "",
        "title": title,
        "summary": summary,
        "amount": {"value": amount, "currency": currency} if amount is not None else None,
        "date": {"iso": iso, "display": display_date(iso, calendar, lang)} if iso else None,
        "lines": lines,
        "people": card_people(db, p, preview),
        "new_entities": list(_get(p, "new_entities") or []),
        "needs_approval": needs_approval,
        "approval_threshold": _get(p, "approval_threshold"),
        "expires_at": _get(p, "expires_at"),
        # Edit only where the phone can change it (proposal_edit.EDITABLE)
        "actions": ["confirm", "edit", "cancel"] if _get(p, "tool_name") in _EDITABLE else ["confirm", "cancel"],
        # a card corrected in the chat names the one it withdrew, so the thread redraws that as changed
        **({"replaces": str(_get(p, "replaces"))} if _get(p, "replaces") else {}),
        "fallback_text": summary or title,
    }


# --- data the tools read ------------------------------------------------------------------

def _balance_figure(result: dict, **_) -> dict | None:
    if "balance" not in result:
        return None
    return {"type": "figure", "label": f"{result.get('account_code', '')} {result.get('account_name', '')}".strip(),
            "value": json_amount(result["balance"]), "currency": result.get("currency"),
            "as_of": result.get("as_of"),
            "fallback_text": f"{result.get('account_name', '')}: {grouped(json_amount(result['balance']))} {result.get('currency') or ''}".strip()}


def _cash_figure(result: dict, lang: str = "en", **_) -> dict | None:
    """get_cash_position: every cash and bank account together, with each one.
    Money in more than one currency is a list of places, each in its own
    currency (scenario AB5): one figure would add Omani rials to Iranian ones."""
    if "total" not in result:
        return None
    held = result.get("by_currency") or []
    if len(held) > 1 or (len(held) == 1 and held[0].get("currency") != result.get("currency")):
        rows = [{"label": h.get("account_name") or h.get("account_code"), "sub": h.get("account_code"),
                 "value": json_amount(h.get("balance") or 0), "currency": h.get("currency")}
                for h in result.get("holdings") or []]
        return {"type": "table", "kind": "holdings", "rows": rows,
                "fallback_text": " · ".join(f"{grouped(x['total'])} {x['currency']}" for x in held)}
    value = json_amount(result["total"])
    label = {"fa": "موجودی نقد و بانک", "es": "Efectivo y bancos", "ar": "النقد والبنوك"}.get(lang, "Cash and bank")
    return {"type": "figure", "label": label, "value": value, "currency": result.get("currency"),
            "as_of": result.get("as_of"),
            "breakdown": [{"label": a.get("account_name") or a.get("account_code"), "value": json_amount(a.get("balance") or 0)}
                          for a in result.get("accounts") or []],
            "fallback_text": f"{label}: {grouped(value)} {result.get('currency') or ''}".strip()}


def _spending_table(result: dict, **_) -> dict | None:
    """get_spending_summary: the period's total and its biggest categories.
    Spending in several currencies lists each currency's categories in its own
    amounts (scenario AO5): one total would add Omani rials to Iranian ones."""
    held = result.get("by_currency") or []
    if held:
        out = [{"label": r.get("category_name") or r.get("category_code"), "sub": r.get("category_code"),
                "value": json_amount(r.get("amount") or 0), "currency": h.get("currency")}
               for h in held for r in (h.get("by_category") or [])[:5]]
        if not out:
            return None
        return {"type": "table", "kind": "spending", "rows": out,
                "fallback_text": " · ".join(f"{grouped(json_amount(h.get('total') or 0))} {h.get('currency')}" for h in held)}
    rows = result.get("by_category")
    if not isinstance(rows, list) or not rows:
        return None
    out = [{"label": r.get("category_name") or r.get("category_code"), "sub": r.get("category_code"),
            "value": json_amount(r.get("amount") or 0), "currency": result.get("currency")} for r in rows[:8]]
    return {"type": "table", "kind": "spending", "rows": out,
            "totals": {"total": result.get("total"), "currency": result.get("currency")},
            "fallback_text": "; ".join(f"{r['label']} {grouped(r['value'])}" for r in out)}


def _invoice_table(result: dict, **_) -> dict | None:
    rows = result.get("invoices")
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for inv in rows[:8]:
        due = inv.get("balance_due", inv.get("amount_due", inv.get("amount")))
        out.append({"label": inv.get("party") or inv.get("entity_name") or inv.get("number") or "",
                    "sub": inv.get("number"), "value": json_amount(due or 0), "currency": inv.get("currency"),
                    "due_date": inv.get("due_date"), "state": inv.get("status")})
    return {"type": "table", "kind": "invoices", "rows": out, "count": int(result.get("count") or len(rows)),
            "totals": result.get("totals_by_currency") or {},
            "fallback_text": "; ".join(f"{r['label']} {grouped(r['value'])}" for r in out)}


def _budget_table(result: dict, **_) -> dict | None:
    rows = result.get("budgets")
    if not isinstance(rows, list) or not rows:
        return None
    out = [{"label": b.get("category"), "value": json_amount(b.get("actual") or 0), "limit": json_amount(b.get("budget") or 0),
            "used_pct": b.get("used_pct"), "state": b.get("state")} for b in rows[:8]]
    return {"type": "table", "kind": "budgets", "rows": out,
            "totals": {"budget": result.get("total_budget"), "actual": result.get("total_actual")},
            "fallback_text": "; ".join(f"{r['label']} {r['used_pct']}%" for r in out)}


_PAY_STATUS = {
    "fa": {"draft": "پیش‌نویس", "posted": "ثبت‌شده", "paid": "پرداخت‌شده"},
    "es": {"draft": "borrador", "posted": "registrada", "paid": "pagada"},
    "ar": {"draft": "مسودة", "posted": "مسجّلة", "paid": "مدفوعة"},
}


def _pay_month(iso: str, calendar: str, lang: str) -> str:
    from app.services.calendar_periods import month_key, month_label
    return month_label(month_key(date.fromisoformat(iso), calendar), lang)


def payslip_file(run_id, entity_id, employee: str | None, month: str | None) -> dict:
    """An employee's payslip for a pay run, as a file the phone can open or share."""
    name = f"payslip-{(employee or 'employee').replace(' ', '_')}.pdf"
    return {"type": "file", "id": f"file:payslip:{run_id}:{entity_id}", "name": name, "mime": "application/pdf",
            "path": f"/api/mobile/v1/documents/payslips/{run_id}/{entity_id}",
            "fallback_text": f"Payslip {employee or ''} {month or ''} (PDF)".replace("  ", " ")}


def _payroll(result: dict, lang: str = "en", calendar: str = "gregorian", **_) -> list[dict] | None:
    """get_payroll: the recent pay runs, net by month; or an employee's pay,
    month by month, with their latest payslip as a file."""
    words = _PAY_STATUS.get(lang, {})
    if isinstance(result.get("runs"), list) and result["runs"]:
        rows = [{"label": _pay_month(r["period_end"], calendar, lang), "sub": words.get(r["status"], r["status"]),
                 "value": json_amount(r.get("total_net") or 0), "currency": r.get("currency"), "state": r["status"]}
                for r in result["runs"][:8]]
        return [{"type": "table", "kind": "payroll_runs", "rows": rows,
                 "fallback_text": "; ".join(f"{r['label']} {grouped(r['value'])}" for r in rows)}]
    history = result.get("history")
    if not isinstance(history, list) or not history:
        return None
    rows = [{"label": _pay_month(h["period_end"], calendar, lang), "sub": words.get(h["status"], h["status"]),
             "value": json_amount(h.get("net_pay") or 0), "currency": h.get("currency"), "state": h["status"]}
            for h in history[:8]]
    out = [{"type": "table", "kind": "pay_history", "title": result.get("employee"), "rows": rows,
            "fallback_text": f"{result.get('employee')}: " + "; ".join(f"{r['label']} {grouped(r['value'])}" for r in rows)}]
    latest = history[0]
    if latest.get("run_id") and result.get("entity_id"):
        out.append(payslip_file(latest["run_id"], result["entity_id"], result.get("employee"), rows[0]["label"]))
    return out


def invoice_file(invoice_id, number) -> dict:
    """An invoice as a file the phone can open or share."""
    name = f"invoice-{number or str(invoice_id)[:8]}.pdf"
    return {"type": "file", "id": f"file:invoice:{invoice_id}", "name": name, "mime": "application/pdf",
            "path": f"/api/mobile/v1/documents/invoices/{invoice_id}",
            "fallback_text": f"Invoice {number} (PDF)" if number else "Invoice (PDF)"}


def _invoice_file(result: dict, **_) -> dict | None:
    """get_invoice: the invoice itself, as its PDF."""
    if not result.get("id"):
        return None
    return invoice_file(result["id"], result.get("number"))


def _forecast_chart(result: dict, lang: str = "en", **_) -> dict | None:
    """get_cash_forecast (no what-if): cash week by week for the next weeks,
    from today's balance, the lowest week and the first below zero marked."""
    weeks = result.get("weeks")
    if not isinstance(weeks, list) or not weeks or "opening_cash" not in result:
        return None
    title = {"fa": "نقدینگی هفته‌های آینده", "es": "Caja en las próximas semanas",
             "ar": "النقد في الأسابيع القادمة"}.get(lang, "Cash, the coming weeks")
    lowest = result.get("lowest") or {}
    cur = result.get("currency")
    return {"type": "chart", "kind": "cash_forecast", "style": "line", "title": title, "currency": cur,
            "start": {"x": result.get("as_of"), "value": json_amount(result["opening_cash"])},
            "points": [{"x": w["week_start"], "value": json_amount(w["closing"])} for w in weeks],
            "lowest": {"x": lowest.get("week_start"), "value": json_amount(lowest.get("closing") or 0)} if lowest else None,
            "first_negative": result.get("first_negative_week"),
            "fallback_text": f"{title}: {grouped(result['opening_cash'])} → {grouped(result.get('closing_cash') or 0)} {cur or ''}".strip()}


_UNITS = {
    "fa": {"GOLD_GRAM": "گرم طلا", "GOLD_COIN": "سکه", "USD": "دلار", "EUR": "یورو", "GBP": "پوند", "AED": "درهم"},
    "es": {"GOLD_GRAM": "g de oro", "GOLD_COIN": "monedas de oro"},
    "ar": {"GOLD_GRAM": "غرام ذهب", "GOLD_COIN": "عملة ذهبية", "USD": "دولار", "EUR": "يورو"},
    "en": {"GOLD_GRAM": "g of gold", "GOLD_COIN": "gold coins"},
}


def _quantity(q: float | None, unit: str | None, lang: str) -> str | None:
    if q is None or not unit:
        return None
    n = f"{q:,.3f}".rstrip("0").rstrip(".")
    words = _UNITS.get(lang, {}).get(unit) or _UNITS["en"].get(unit) or unit
    if lang == "fa":
        n = persian_figures(n)
    return f"{n} {words}"


def _net_worth(result: dict, lang: str = "en", **_) -> list[dict] | None:
    """get_net_worth: the figure, its twelve months as a line, and what is
    held in gold and currency at today's rates."""
    if "net_worth" not in result:
        return None
    cur = result.get("currency")
    label = {"fa": "دارایی خالص", "es": "Patrimonio neto", "ar": "صافي الثروة"}.get(lang, "Net worth")
    out = [{"type": "figure", "label": label, "value": json_amount(result["net_worth"]), "currency": cur,
            "as_of": result.get("as_of"),
            "breakdown": [{"label": {"fa": "دارایی‌ها", "es": "Activos", "ar": "الأصول"}.get(lang, "Assets"),
                           "value": json_amount(result.get("total_assets") or 0)},
                          {"label": {"fa": "بدهی‌ها", "es": "Deudas", "ar": "الديون"}.get(lang, "Liabilities"),
                           "value": json_amount(result.get("total_liabilities") or 0)}],
            "fallback_text": f"{label}: {grouped(json_amount(result['net_worth']))} {cur or ''}".strip()}]
    trend = [t for t in result.get("trend") or [] if t.get("date")]
    if len(trend) >= 2:
        title = {"fa": "دارایی خالص، دوازده ماه گذشته", "es": "Patrimonio neto, los últimos doce meses",
                 "ar": "صافي الثروة، آخر اثني عشر شهرًا"}.get(lang, "Net worth, the last twelve months")
        out.append({"type": "chart", "kind": "net_worth", "style": "line", "title": title, "currency": cur,
                    "points": [{"x": t["date"], "value": json_amount(t["value"])} for t in trend],
                    "fallback_text": f"{title}: {grouped(json_amount(trend[0]['value']))} → {grouped(json_amount(trend[-1]['value']))} {cur or ''}".strip()})
    held = [a for a in result.get("assets") or [] if a.get("revalued")]
    if held:
        rows = [{"label": a.get("account_name") or a.get("account_code"), "sub": _quantity(a.get("quantity"), a.get("unit"), lang),
                 "value": json_amount(a.get("market_value") or 0), "currency": cur, "gain": json_amount(a.get("unrealized_gain") or 0)}
                for a in held[:8]]
        out.append({"type": "table", "kind": "holdings", "rows": rows,
                    "fallback_text": "; ".join(f"{r['label']} {grouped(r['value'])}" for r in rows)})
    return out


def _affordability(result: dict, lang: str = "en", **_) -> dict | None:
    """Can I afford it? as the review's card (scenario AM9)."""
    if not result.get("verdict"):
        return None
    from app.services.affordability import card
    c = card(result, lang)
    return {"type": "review", **c,
            "fallback_text": "\n".join([c["title"], c["headline"]["label"], *c["facts"], *(i["text"] for i in c["ideas"])])}


RENDERERS = {
    "check_affordability": _affordability,
    "get_net_worth": _net_worth,
    "get_cash_forecast": _forecast_chart,
    "get_payroll": _payroll,
    "get_account_balance": _balance_figure,
    "get_cash_position": _cash_figure,
    "list_invoices": _invoice_table,
    "get_budget_status": _budget_table,
    "get_spending_summary": _spending_table,
    "get_invoice": _invoice_file,
}


def data_blocks(tool_calls: list[dict] | None, lang: str = "en", calendar: str = "gregorian") -> list[dict]:
    """The last successful call of each drawable read tool, in call order."""
    latest: dict[str, tuple[int, dict]] = {}
    for i, call in enumerate(tool_calls or []):
        name = call.get("name")
        result = call.get("result")
        if name in RENDERERS and isinstance(result, dict):
            latest[name] = (i, call)
    picked = sorted(latest.values(), key=lambda t: t[0])[-MAX_DATA_BLOCKS:]
    out = []
    for _i, call in picked:
        made = RENDERERS[call["name"]](call["result"], lang=lang, calendar=calendar)
        # a renderer gives a block, or several (a table and its document)
        for n, block in enumerate(made if isinstance(made, list) else [made] if made else []):
            block.setdefault("id", f"{call['name']}:{call.get('tool_use_id') or uuid.uuid4().hex[:8]}" + (f":{n}" if n else ""))
            block["tool"] = call["name"]
            out.append(block)
    return out


# --- the whole reply ----------------------------------------------------------------------

def build_blocks(db: Session, *, text: str | None, proposals: list | None, tool_calls: list[dict] | None,
                 intake: dict | None, lang: str = "en", calendar: str | None = None) -> list[dict]:
    """Cards first, then the words (the block comes before the sentence)."""
    if calendar is None:
        from app.services.locale_service import get_display_calendar
        calendar = get_display_calendar(db)
    blocks = data_blocks(tool_calls, lang=lang, calendar=calendar)
    if intake:
        # the words follow in their own block: a client that can't draw the card mustn't say them twice
        # a statement sent again shows its card again: its own id, or the list keeps only the first
        again = f":{uuid.uuid4().hex[:6]}" if intake.get("status") == "duplicate" else ""
        blocks.append({"type": "intake",
                       "id": f"intake:{intake.get('kind')}:{intake.get('statement_id') or intake.get('batch_id') or ''}{again}",
                       **intake, "fallback_text": "" if (text and text.strip()) else (text or "")})
    names = card_names(db, proposals)
    for p in proposals or []:
        blocks.append(proposal_block(db, p, calendar=calendar, lang=lang, names=names))
    if text and text.strip():
        said = {"type": "text", "id": f"text:{uuid.uuid4().hex[:8]}", "text": text.strip(), "fallback_text": text.strip()}
        # a short question's likely answers, to tap (ask_with_choices: «change the books' currency, or keep it?»)
        options = next((((c.get("result") or {}).get("options") or []) for c in reversed(tool_calls or [])
                        if c.get("name") == "ask_with_choices" and isinstance(c.get("result"), dict)), [])
        if options:
            said["choices"] = [str(o) for o in options][:4]
        blocks.append(said)
    return blocks


def posted_block(*, token: str, transaction_id: str | None, audit_log_id: str, voucher: str | None,
                 date_iso: str | None, calendar: str, lang: str, undo_seconds: int, file: dict | None = None) -> dict:
    """What a confirmed voucher becomes: stamped, with the undo window, and
    the document it made (an invoice's PDF) when there is one."""
    return {
        "file": file,
        "type": "posted", "id": f"posted:{token}", "token": token,
        "transaction_id": transaction_id, "audit_log_id": audit_log_id,
        "voucher": voucher, "date": {"iso": date_iso, "display": display_date(date_iso, calendar, lang)} if date_iso else None,
        "undo_seconds": undo_seconds,
        "actions": ["undo"] if undo_seconds > 0 else ["reverse"],
        "fallback_text": "Posted" + (f" · {voucher}" if voucher else ""),
    }
