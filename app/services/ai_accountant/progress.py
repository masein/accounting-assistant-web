"""What the accountant is doing right now, for a client that streams the turn
(roadmap ROADMAP_ANDROID_CHAT P0.5).

A turn runs the model and several tools for about nine seconds. A streaming
client listens through a context variable, so nothing in the chat path needs
a new parameter: ``listen(sink)`` before the turn, and the orchestrator calls
``emit`` as it goes — the model thinking, each tool, files being read. With
nobody listening ``emit`` does nothing.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable

_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("ai_progress_sink", default=None)


def listen(sink: Callable[[dict], None]) -> Token:
    return _sink.set(sink)


def stop(token: Token) -> None:
    _sink.reset(token)


# What each kind of step is called, in the user's language.
_PHRASES = {
    "thinking": {"en": "Thinking…", "fa": "در حال فکر کردن…", "es": "Pensando…", "ar": "يفكّر…"},
    "reading_files": {"en": "Reading the attachment…", "fa": "در حال خواندن پیوست…",
                      "es": "Leyendo el adjunto…", "ar": "يقرأ المرفق…"},
    "books": {"en": "Checking the books…", "fa": "در حال بررسی دفاتر…", "es": "Revisando los libros…",
              "ar": "يراجع الدفاتر…"},
    "parties": {"en": "Looking up the party…", "fa": "در حال پیدا کردن طرف حساب…", "es": "Buscando el tercero…",
                "ar": "يبحث عن الطرف…"},
    "invoices": {"en": "Reading the invoices…", "fa": "در حال خواندن فاکتورها…", "es": "Leyendo las facturas…",
                 "ar": "يقرأ الفواتير…"},
    "report": {"en": "Preparing the report…", "fa": "در حال آماده کردن گزارش…", "es": "Preparando el informe…",
               "ar": "يُعدّ التقرير…"},
    "payroll": {"en": "Working out the payroll…", "fa": "در حال محاسبهٔ حقوق…", "es": "Calculando la nómina…",
                "ar": "يحسب الرواتب…"},
    "drafting": {"en": "Drafting the voucher…", "fa": "در حال نوشتن پیش‌نویس سند…", "es": "Redactando el asiento…",
                 "ar": "يكتب مسودة القيد…"},
    "working": {"en": "Working on it…", "fa": "در حال کار…", "es": "Trabajando…", "ar": "يعمل…"},
}


def _kind(tool: str | None) -> str:
    t = tool or ""
    if t.startswith("propose_"):
        return "drafting"
    if t in ("find_entity", "list_entities"):
        return "parties"
    if "invoice" in t:
        return "invoices"
    if "payroll" in t or "pay_run" in t:
        return "payroll"
    if t in ("get_financial_statement", "get_tax_summary", "get_cash_forecast", "get_insights", "get_report_card",
             "get_spending_summary", "get_budget_status", "get_close_checklist"):
        return "report"
    if t in ("get_account_balance", "get_cash_position", "query_ledger", "search_accounts", "get_company_defaults",
             "review_bank_statement"):
        return "books"
    return "working"


def say(stage: str, tool: str | None, lang: str) -> str:
    key = _kind(tool) if stage == "tool" else stage
    table = _PHRASES.get(key) or _PHRASES["working"]
    return table.get(lang) or table["en"]


def emit(stage: str, *, tool: str | None = None, lang: str = "en") -> None:
    """Tell whoever listens what the turn is doing; never fails the turn."""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink({"stage": stage, "tool": tool, "text": say(stage, tool, lang)})
    except Exception:  # noqa: BLE001 — a dropped listener must not stop the books
        pass
