"""The accountant on migrated books (scenarios R, AI QA 2026-10-12).

A real model was asked about two migrated companies (a Sepidar company and two
schools' hand-kept books); each finding is pinned here without a model."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_statement_export import _company


@pytest.fixture()
def ir(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, dr, cr, amount, desc, day="2026-08-10"):
    r = api.post("/transactions", json={"date": day, "description": desc, "currency": "IRR", "lines": [
        {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text


def test_r2_the_chats_cash_is_the_dashboards(ir, db):
    """«کل موجودی نقد و بانک چقدره؟» gave the banks only (692,378,980) while the
    dashboard and the balance sheet said 860,274,030: a migrated chart's petty
    cash (111003, under 1110) was left out of the chat's figure."""
    api, cid = ir
    r = api.post("/accounts", json={"name": "تنخواه گردان", "parent_code": "1110", "code": "111003"})
    assert r.status_code == 201, r.text
    _post(api, "1110", "3110", 1_000_000, "capital")
    _post(api, "111003", "1110", 300_000, "petty cash top-up")
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
    from app.services.cash_service import cash_on_hand
    with use_company(cid):
        out = asyncio.run(GetCashPosition().run(ToolContext(db=db, user_id="u"), GetCashPositionInput()))
        dashboard = cash_on_hand(db, locale="ir")
    assert {a["account_code"] for a in out["accounts"] if a["balance"]} == {"1110", "111003"}
    assert out["total"] == dashboard == 1_000_000
    kpis = {k["key"]: k["value"] for k in api.get("/reports/owner-dashboard").json()["kpis"]}
    assert kpis["cash_on_hand"] == out["total"]


def _chat(api, message):
    r = api.post("/ai-accountant/chat", json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def test_r6_who_owes_us_reads_the_books_not_only_invoices(ir, db):
    """A migrated company's parties carry balances and it has no invoices:
    «کی به ما بدهکاره؟» answered «هیچ فاکتور فروشی منتظر پرداخت نیست» while its
    debtors report listed six (2,191,898,610 rials)."""
    api, _ = ir
    aria = api.post("/entities", json={"type": "client", "name": "آریا"}).json()
    r = api.post("/transactions", json={
        "date": "2026-08-10", "description": "مانده افتتاحیه آریا", "currency": "IRR",
        "lines": [{"account_code": "1112", "debit": 4_200_000, "credit": 0},
                  {"account_code": "3110", "debit": 0, "credit": 4_200_000}],
        "entity_links": [{"role": "client", "entity_id": aria["id"]}]})
    assert r.status_code == 201, r.text
    out = _chat(api, "کی به ما بدهکاره؟")
    # a Persian question is answered in Persian, whatever the reader's saved language
    assert out["text"] == "طبق دفاتر، ۱ طرف حساب روی هم ۴٬۲۰۰٬۰۰۰ ریال به ما بدهکارند؛ بیشترین: آریا (۴٬۲۰۰٬۰۰۰ ریال).", out["text"]
    assert {c["name"] for c in out["tool_calls"]} == {"list_invoices", "get_party_balances"}
    nobody = _chat(api, "به کی بدهکاریم؟")["text"]
    assert nobody == "به کسی بدهکار نیستیم: نه فاکتور خرید بازی هست و نه بستانکاری در دفاتر."
    assert _chat(api, "who do we owe?")["text"] == "We owe nobody anything: no open supplier bill and no creditor in the books."
    from app.db.tenant import use_company
    from app.services.ai_accountant.fast_paths import answer
    with use_company(_):
        fa = asyncio.run(answer(db, "کی به ما بدهکاره؟", user_id="u", username="t", lang="fa"))
        fa_none = asyncio.run(answer(db, "به کی بدهکاریم؟", user_id="u", username="t", lang="fa"))
    assert fa.text == "طبق دفاتر، ۱ طرف حساب روی هم ۴٬۲۰۰٬۰۰۰ ریال به ما بدهکارند؛ بیشترین: آریا (۴٬۲۰۰٬۰۰۰ ریال)."
    assert fa_none.text == "به کسی بدهکار نیستیم: نه فاکتور خرید بازی هست و نه بستانکاری در دفاتر."
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.invoice_tools import GetPartyBalances, GetPartyBalancesInput
    with use_company(_):
        one = asyncio.run(GetPartyBalances().run(ToolContext(db=db, user_id="u"), GetPartyBalancesInput(party="آريا")))
    assert one["debtors"] == [{"party": "آریا", "type": "client", "balance": 4_200_000, "over_90_days": 0}]


def test_r1_a_named_bank_is_that_banks_balance(ir):
    """«موجودی بانک ملت چقدره؟» was answered with all the cash together."""
    api, _ = ir
    mellat = api.post("/entities", json={"type": "bank", "name": "بانک ملت جاری 7748"}).json()
    other = api.post("/entities", json={"type": "bank", "name": "بانک دی"}).json()
    _post(api, mellat["code"], "3110", 3_000_000, "capital")
    _post(api, other["code"], "3110", 500_000, "capital")
    out = _chat(api, "موجودی بانک ملت چقدره؟")
    assert out["text"] == "موجودی حساب بانکی بانک ملت جاری 7748 امروز: ۳٬۰۰۰٬۰۰۰ ریال.", out["text"]
    assert [c["name"] for c in out["tool_calls"]] == ["get_account_balance"]
    total = _chat(api, "موجودی بانک چقدره؟")
    assert [c["name"] for c in total["tool_calls"]] == ["get_cash_position"]
    # a bank the books don't have, or a word naming several: the model answers
    from app.services.ai_accountant.fast_paths import _named_bank
    assert _named_bank("موجودی بانک ملی چقدره؟") == "ملی" and _named_bank("موجودی بانک‌ها چقدره؟") is None
    assert _named_bank("what's the Barclays bank balance?") == "barclays" and _named_bank("bank balance") is None


def test_r10_the_books_own_accounts_come_before_the_seeds(ir, db):
    """A migrated company keeps the seed chart beside its own: «لوازم التحریر»
    was posted to the seed's never-used 6112 «سایر هزینه‌های عملیاتی» over the
    company's 611213 «هزینه ملزومات و نوشت افزار», «اجاره» likewise over its rent
    account, «فروش» to the seed's 4110 over the 411003 it sells on."""
    api, cid = ir
    for code, name, parent in (("611213", "هزینه ملزومات و نوشت افزار", "6112"), ("611228", "هزینه اجاره", "6112"),
                               ("411003", "فروش خدمات", "4110")):
        r = api.post("/accounts", json={"name": name, "parent_code": parent, "code": code})
        assert r.status_code == 201, r.text
    for i in range(12):                                   # a company with history: 36 lines
        _post(api, "611213", "1110", 1_000_000, f"نوشت افزار {i}")
        _post(api, "611228", "1110", 5_000_000, f"اجاره {i}")
        _post(api, "1110", "411003", 9_000_000, f"فروش {i}")
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput

    def top(query):
        with use_company(cid):
            out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u"), SearchAccountsInput(query=query)))
        return out["matches"][0]["code"]
    assert top("لوازم التحریر") == "611213" and top("office supplies") == "611213"
    assert top("اجاره") == "611228" and top("فروش") == "411003"


def test_r3_jalali_dates_months_and_years_reach_the_tools_as_meant():
    """The model sent «امسال» as 1405-01-01..1405-07-20, which read as the year
    1405 AD (a zero income statement); it computed Tir as 06-22..07-21 (a day
    short, Tir's salaries in Mordad); it answered «۱۳۹۹» with 1404's figures."""
    from app.services.ai_accountant.orchestrator import _gregorian_dates, _message_language, _with_jalali_labels
    got = _gregorian_dates({"statement": "income_statement", "from_date": "1405-01-01", "to_date": "۱۴۰۵/۰۷/۲۰",
                            "reference": "1405-01-01", "lines": [{"date": "1404-12-29"}]})
    assert got == {"statement": "income_statement", "from_date": "2026-03-21", "to_date": "2026-10-12",
                   "reference": "1405-01-01", "lines": [{"date": "2026-03-20"}]}      # a reference stays as written
    assert _with_jalali_labels({"rows": [{"date": "2026-07-28", "amount": 5}]}) == \
        {"rows": [{"date": "2026-07-28", "date_jalali": "۶ مرداد ۱۴۰۵", "amount": 5}]}
    from app.services.ai_accountant.orchestrator import _for_the_model
    assert _for_the_model({"total": 21_721_200_283, "count": 45, "rows": [{"amount": 500}]}, persian=True) == \
        {"total": 21_721_200_283, "total_fa": "۲۱٬۷۲۱٬۲۰۰٬۲۸۳", "count": 45, "rows": [{"amount": 500}]}
    assert _message_language("What was our biggest expense last month?", "fa") == "en"
    assert _message_language("کی به ما بدهکاره؟", "en") == "fa" and _message_language("پول کافی داریم؟", "fa") == "fa"


def test_r3_r5_r16_whole_months_and_years(ir, db):
    api, cid = ir
    _post(api, "6112", "1110", 7_000_000, "rent Tir", day="2026-07-22")          # 31 Tir 1405
    _post(api, "6112", "1110", 2_000_000, "rent Mordad", day="2026-07-23")       # 1 Mordad 1405
    _post(api, "1110", "4110", 9_000_000, "sale", day="2026-04-01")              # Farvardin 1405
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import GetFinancialStatement, GetFinancialStatementInput
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    ctx = ToolContext(db=db, user_id="u")
    with use_company(cid):
        tir = asyncio.run(GetSpendingSummary().run(ctx, GetSpendingSummaryInput(month="1405-04")))
        mordad = asyncio.run(GetSpendingSummary().run(ctx, GetSpendingSummaryInput(month="1405-05")))
        y1399 = asyncio.run(GetSpendingSummary().run(ctx, GetSpendingSummaryInput(year="1399", kind="income")))
        y1405 = asyncio.run(GetFinancialStatement().run(ctx, GetFinancialStatementInput(statement="income_statement",
                                                                                          year="1405")))
    assert (tir["total"], mordad["total"], y1399["total"]) == (7_000_000, 2_000_000, 0)
    assert y1405["net_income"] == 9_000_000 - 9_000_000 and y1405["revenue"][0]["amount"] == 9_000_000, y1405


def test_r10_r12_a_party_in_another_order_or_without_its_kind():
    """«بانک آینده» ranked «بانک دی فرمانیه» first on the shared «بانک»;
    «علیرضا حیدری» matched «حیدری علیرضا» at 0.5."""
    from app.services.ai_accountant.read_tools import _party_words
    assert _party_words("بانک آینده") <= _party_words("آینده جردن")
    assert _party_words("علیرضا حیدری") == _party_words("حیدری علیرضا")
    assert not _party_words("بانک آینده") <= _party_words("بانک دی فرمانیه")


def test_r6_whom_we_owe_names_the_balance_sheets_liabilities(ir, db):
    """A school owes its bank loan and its sister school, with no party in the
    books: «به کی بدهکاریم؟» said «به کسی بدهکار نیستیم»."""
    api, cid = ir
    _post(api, "1110", "2110", 9_997_000_000, "تسهیلات بانک پارسیان")
    from app.db.tenant import use_company
    from app.services.ai_accountant.fast_paths import answer
    with use_company(cid):
        out = asyncio.run(answer(db, "به کی بدهکاریم؟", user_id="u", username="t", lang="fa"))
    assert out.text.startswith("به هیچ تأمین‌کننده یا طرف حسابی در دفاتر بدهکار نیستیم؛ بدهی‌های ترازنامه روی هم ۹٬۹۹۷٬۰۰۰٬۰۰۰ ریال است")


def test_r15_bank_ayandeh_is_not_a_schedule(ir, db):
    """«آینده» is Bank Ayandeh's name and the word for "future": «… از بانک
    آینده» let a rent dated next month through as a scheduled entry."""
    api, cid = ir
    bank = api.post("/entities", json={"type": "bank", "name": "آینده جردن"}).json()
    from datetime import date, timedelta
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput, _mentions_scheduled
    later = (date.today() + timedelta(days=25)).isoformat()
    args = ProposeCreateTransactionInput(date=later, description="اجاره آبان", currency="IRR", lines=[
        {"account_code": "6112", "debit": 100_000_000, "credit": 0},
        {"account_code": bank["code"], "debit": 0, "credit": 100_000_000}])
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u", user_message=f"اجاره آبان رو ثبت کن، تاریخ {later}، مبلغ ۱۰۰ میلیون ریال از بانک آینده")
        with pytest.raises(ToolError) as e:
            asyncio.run(ProposeCreateTransaction().run(ctx, args))
    assert e.value.code == "future_date"
    assert _mentions_scheduled("برای آینده زمان‌بندی کن") and not _mentions_scheduled("از آینده جردن", ("آینده جردن",))


def test_tomans_carded_as_rials_are_refused(ir, db):
    """«قسط وام بانک ملت ۲۰ میلیون تومان» was carded at 20,000,000 rials."""
    api, cid = ir
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateTransaction, ProposeCreateTransactionInput, _toman_amounts)
    assert _toman_amounts("قسط ۲۰ میلیون تومان و ۱۵۰ هزار تومن") == {20_000_000, 150_000}

    def card(amount):
        args = ProposeCreateTransactionInput(date="2026-08-10", description="قسط وام", currency="IRR", lines=[
            {"account_code": "2110", "debit": amount, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": amount}])
        with use_company(cid):
            ctx = ToolContext(db=db, user_id="u", user_message="قسط وام بانک ملت ۲۰ میلیون تومان از بانک پرداخت شد")
            return asyncio.run(ProposeCreateTransaction().run(ctx, args))
    with pytest.raises(ToolError) as e:
        card(20_000_000)
    assert e.value.code == "toman_not_converted" and "200,000,000" in e.value.message
    assert card(200_000_000)["confirmation_token"]


def test_a_model_without_adaptive_thinking_still_chats():
    """claude-haiku-4-5 refused every chat: "adaptive thinking is not supported
    on this model" (model benchmark, 2026-10-12)."""
    import anthropic
    import httpx
    from app.services.ai_accountant.anthropic_client import _create

    sent = []

    class Messages:
        async def create(self, **kw):
            sent.append(dict(kw))
            if "thinking" in kw:
                raise anthropic.BadRequestError(
                    "adaptive thinking is not supported on this model",
                    response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body=None)
            return "ok"

    class Client:
        messages = Messages()

    out = asyncio.run(_create(Client(), {"model": "claude-haiku-4-5", "thinking": {"type": "adaptive"},
                                         "output_config": {"effort": "low"}}))
    assert out == "ok" and "thinking" in sent[0] and "thinking" not in sent[1] and "output_config" in sent[1]
