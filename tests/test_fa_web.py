"""The web app's Persian, as a Persian reader meets it (Persian copy review, 2026-10-31).

- Pieces side by side join with «، » in Persian and Arabic (joinParts / partSep, js/01-core.js):
  the middle dot is the size and height of «۰», so «امروز · ۱۸ مهر» read «۱۸۰ مهر». Lists join
  with «، » too (joinList): «علی رضایی, سارا محمدی» had a Latin comma between Persian names.
- A template is filled with tf() (Persian digits), never String.replace («مهلت 3 صورتحساب …»).
- The ledger's group rows say the parent's own tier on the chart, not always «دفتر کل».
- Figures the page writes itself are in the page's digits: the audit trail's time, the fixed-asset
  KPIs, Chart.js's axes, the UK VAT boxes.

tests_e2e/test_fa_web_joins.py runs the helpers and the ledger in a browser."""
from __future__ import annotations

import re

from tests.i18n_source import JS, pack_values

INDEX = JS.parent / "index.html"


def _js(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _code_files():
    return [p for p in sorted(JS.glob("*.js")) if p.name != "02-i18n.js"]


def test_one_helper_joins_pieces_and_lists_in_the_pages_language():
    core = _js("01-core.js")
    assert "function partSep() { return _uiIsRtl() ? '، ' : ' · '; }" in core
    assert "function listSep() { return _uiIsRtl() ? '، ' : ', '; }" in core
    assert "return lang === 'fa' || lang === 'ar';" in core
    # the report card's own separator is the shared one now
    assert "rcSep" not in _js("05-reports-manager.js")


def test_no_middle_dot_is_written_between_pieces_by_hand():
    # left: partSep itself; a zero shown as «·» in the Iranian statements; the chat card's date, which
    # picks its separator from the date's own script
    allowed = {"if (amount === 0) return '·';", "if (v === 0) return '·';", "if (n === 0) return '·';"}
    found = []
    for path in _code_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.strip()
            if "·" not in code or code.startswith("//") or code in allowed:
                continue
            if "function partSep()" in code or "_RTL_WORDS.test(date) ? '، ' : ' · '" in code:
                continue
            found.append(f"{path.name}:{n}")
    assert found == [], found


def test_no_persian_or_arabic_string_puts_a_middle_dot_between_its_pieces():
    values = pack_values()
    dotted = {lang: sorted(k for k, v in values[lang].items() if " · " in v) for lang in ("fa", "ar")}
    assert dotted == {"fa": [], "ar": []}
    # read in context: a word where a comma would clash
    assert values["fa"]["fxSharedFrom"] == "مشترک از {source}"
    assert values["fa"]["jiStrayItem"].endswith("بدهکار {dr}؛ بستانکار {cr}")
    assert values["fa"]["forecastWeekLine"] == "ورودی {inflow}، خروجی {outflow}، مانده {closing}"


def test_lists_of_names_and_codes_join_in_the_pages_language():
    assert "names: joinList(d.skipped.map(x => x.name))" in _js("11-time-expenses-payroll.js")
    assert "joinList(r.entity_names || [])" in _js("13-companies-products.js")
    ops = _js("12-ops.js")
    assert ".join('، ')" not in ops and "['fa', 'ar'].includes(currentLanguage) ? '، '" not in ops
    # the alert channels by name, not the server's ids («slack, telegram, email»)
    inv = _js("08-entities-invoices.js")
    assert "slack: 'digestChannelSlack', telegram: 'digestChannelTelegram', email: 'digestChannelEmail'" in inv
    assert "joinList((data.delivered || []).map(" in inv


def test_templates_are_filled_with_tf_not_string_replace():
    found = [f"{p.name}:{n}" for p in _code_files()
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
             if re.search(r"\bt\('[A-Za-z_]+'\)\.replace(All)?\('\{", line)]
    assert found == [], found


def test_sentences_built_from_server_values_say_them_in_the_pages_words():
    inv = _js("08-entities-invoices.js")
    assert "tf('riCreatedLater', { d: formatDisplayDate(data.next_run_date) })" in inv
    assert "tf('moOverdueCount', { n: Number(data.overdue) })" in inv
    reports = _js("05-reports-manager.js")
    assert "tf('goalNeeded', { v: amt(g.needed_per_month), date: formatDisplayDate(g.target_date) })" in reports
    assert "localDigits(formatNum(g.percent) + '%')" in reports
    ops = _js("12-ops.js")
    # the party's kind by name in the queue and in the message sent to the chat («client «امیر» …»)
    assert "enumLabel('entType_', r.entity_type)" in ops
    assert "tf('migrationAiPrompt', { type: enumLabel('entType_', rec.entity_type)" in ops
    time = _js("11-time-expenses-payroll.js")
    assert "tf('timeRateSaved', { scope: timeRateScopeLabel(data.scope) })" in time
    assert "'employee-default (pay profile)': 'profile'" in time
    assert "localDigits(p.children) + ' ' + t('payrollChildrenShort')" in time
    chat = _js("15-ai-chat.js")
    assert "tf('chatIntakeAppliedSummary', {" in chat
    assert "localDigits(mins) + ' ' + t('chatTimeMin')" in chat
    # the id and its «…» isolated together, the dots no longer in the templates
    assert "id: '\\u2066' + (data.reversal_transaction_id || '').slice(0, 8) + '…\\u2069'" in chat
    values = pack_values()
    assert [lang for lang in values if "…" in values[lang]["aiUndoReversalNote"]] == []
    assert values["fa"]["chatIntakeAppliedSummary"] == "اعمال شد — {accounts} حساب و {entities} طرف حساب ساخته شد. {opening}: {date}"
    assert values["fa"]["timeRateScope_profile"] == "پیش‌فرض کارمند در مشخصات حقوقی"


def test_the_rate_feeds_line_is_in_the_companys_calendar():
    fx = _js("10-forms-fx-bank.js")
    assert "when.toLocaleString()" not in fx
    assert "tfBdi('fxFeedsRatesOf', { date: formatDisplayDate(r.date) })" in fx
    assert "joinList((headers || []).map((h, i) => `${localDigits(i)}=${h}`))" in fx
    assert "account: localCode(f.suggested_account_code || '')" in fx


def test_the_ledger_names_each_parent_by_its_tier_on_the_chart():
    ledger = _js("09-ledger.js")
    assert "const tier = accountLevelLabel(ledgerLevels, g.code);" in ledger
    assert "t('ledgerKol')" not in ledger
    core = _js("01-core.js")
    assert "return level ? enumLabel('coaLevel_', level) : '';" in core
    assert "levels[String(a.code)] = a.level;" in core
    drawer = _js("08-entities-invoices.js")
    assert "accountLevelLabel(levels, data.account_code) || t('ledgerMoin')" in drawer
    assert "<strong>${escapeHtml(localCode(data.account_code))}</strong>" in drawer
    values = pack_values()
    assert (values["fa"]["ledgerKol"], values["fa"]["ledgerMoin"]) == ("سرفصل", "حساب")   # when the chart didn't say


def test_figures_the_page_writes_are_in_its_digits():
    core = _js("01-core.js")
    assert "+ ' ' + localDigits(p(d.getHours()) + ':' + p(d.getMinutes()));" in core      # formatDisplayDateTime
    assert "Chart.defaults.scales.linear.ticks.callback = chartTick;" in core            # Chart.js wrote «0»
    assert "function auditWhen(ts) {\n      return formatDisplayDateTime(ts);" in _js("11-time-expenses-payroll.js")
    ops = _js("12-ops.js")
    assert "|| formatAmount(0) + ' ' + currencyUnit();" in ops                            # fixed-asset KPIs said «0»
    inv = _js("08-entities-invoices.js")
    assert "white-space:nowrap;\">${escapeHtml(t('mtdBox'))} ${escapeHtml(localDigits(n))}</td>" in inv
    assert "labels: trend.map(r => formatPeriodKey(r.period))" in _js("05-reports-manager.js")
    assert "tf('optionDefaultBank', { code: localCode(defaultCash) })" in _js("06-vouchers.js")


def test_the_small_layout_and_wording_fixes():
    page = INDEX.read_text(encoding="utf-8")
    # the contacts list's column is «کد», not the form's «کد (اختیاری)»
    assert '<th data-i18n="tableCode">Code</th><th data-i18n="usersActions">Actions</th>' in page
    assert "setTextSel('#entities-table thead th:nth-child(3)', 'tableCode');" in _js("02-i18n.js")
    # the SMS example reads left to right, its placeholders in the page's language
    assert '<pre id="sms-auto-example" dir="ltr" style="text-align:left;' in page
    assert "<${t('smsExampleKey')}>" in _js("10-forms-fx-bank.js")
    assert 'data-i18n-placeholder="mailSendersPlaceholder"' in page
    # the empty invoice list spans all eight columns
    assert "'<tr><td colspan=\"8\" class=\"empty-state\">' + escapeHtml(t('noInvoicesYet'))" in _js("06-vouchers.js")
    # «whole amounts» only for a currency without places
    assert "tf(moneyPlaces(ccy) ? 'equityAmountHintPlaces' : 'equityAmountHint'," in _js("12-ops.js")
    # each tax bracket says «up to»
    assert "tf('payrollRulesUpto', { amount: formatAmount(b.upto), rate: Math.round(b.rate * 100) + '%' })" \
        in _js("11-time-expenses-payroll.js")
    values = pack_values()
    assert values["fa"]["payrollRulesUpto"] == "تا {amount}: {rate}"
    assert values["fa"]["equityAmountHintPlaces"] == "مبالغ به {currency} است، مانند سایر بخش‌های دفاتر."
