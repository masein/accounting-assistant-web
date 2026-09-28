"""The manager report's supplementary charts speak the UI's language: every
title, legend, axis and drill-down label drawn by ``renderManagerReportChart``
(app/static/js/05-reports-manager.js) comes from ``t()``, every key it uses is
in the four packs, and a language change redraws the report on screen."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
MANAGER = (JS / "05-reports-manager.js").read_text(encoding="utf-8")
I18N = (JS / "02-i18n.js").read_text(encoding="utf-8")

NEW_KEYS = (
    "chartBalanceSheetTrend", "chartNetWorthOverTime", "legendNetWorth", "chartCostProfitBreakdown",
    "legendNetProfit", "legendCogs", "legendOperatingExpenses", "legendOtherExpenses", "legendBreakdown",
    "chartCashInOutOverTime", "chartNetCashFlowTrend", "legendNetCashFlow", "chartTopAccountsDrCr",
    "chartNetBalanceByAccount", "legendNetBalance", "chartSalesTrendOverTime", "chartPurchaseTrendOverTime",
    "legendSalesAmount", "legendPurchaseAmount", "legendQuantity", "axisQty", "chartReceivablesVsPayables",
    "legendReceivablesDebtors", "legendPayablesCreditors", "legendArVsAp", "chartTopEntities", "legendNetAmount",
    "labelUnknownEntity",
)


def _function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    while True:
        ch = src[i]
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            return src[start:i + 1]
        i += 1


CHARTS = _function(MANAGER, "renderManagerReportChart")


def _values(key: str) -> list[str]:
    return re.findall(r"^        " + re.escape(key) + r": '((?:[^'\\]|\\.)*)',$", I18N, re.M)


def test_no_english_literal_is_drawn():
    assert not re.search(r"_addExtraChart\(\s*['\"`]", CHARTS), "a chart title is a literal"
    assert not re.search(r"\blabel: ['\"`]", CHARTS), "a dataset label is a literal"
    assert not re.search(r"\blabels: \[\s*['\"`]", CHARTS), "category labels are literals"
    assert not re.search(r"\btext: ['\"`]", CHARTS), "an axis title is a literal"
    assert not re.search(r"t\('[A-Za-z]+'\) \|\| ['\"`]", CHARTS), "an English fallback after t()"
    for title in ("Balance Sheet Trend", "Net Worth Over Time", "Assets / Liabilities / Equity over time",
                  "Net Worth (Assets", "'Assets'", "'Liabilities'", "'Equity'", "Cost & Profit", "Trend Over Time",
                  "Top Accounts", "Net Balance", "Receivables vs Payables", "Top Entities", "'Unknown'", "'Qty'"):
        assert title not in CHARTS, title


def test_the_chart_helper_has_no_english_either():
    helper = _function(MANAGER, "_addExtraChart")
    assert not re.search(r"t\('[A-Za-z]+'\) \|\| ['\"`]", helper)


def _keys_used() -> set[str]:
    keys: set[str] = set()
    for call in re.findall(r"\bt\(([^()]*)\)", CHARTS):
        keys |= set(re.findall(r"'([A-Za-z_]+)'", call))
    return keys


def test_every_key_the_charts_use_is_in_all_four_packs():
    used = _keys_used()
    assert set(NEW_KEYS) <= used
    for key in sorted(used):
        values = _values(key)
        assert len(values) == 4 and all(values), f"{key}: {len(values)} packs"


@pytest.mark.parametrize("key", NEW_KEYS)
def test_each_new_key_is_translated(key):
    en, fa, es, ar = _values(key)
    assert fa != en and ar != en and es, key
    assert re.search(r"[؀-ۿ]", fa) and re.search(r"[؀-ۿ]", ar), key


def test_statement_rows_are_labelled_in_the_ui_language():
    assert "(_fa ? r.label_fa : r.label_en)" in CHARTS
    assert "(_fa ? c.label_fa : c.label_en)" in CHARTS


def test_a_language_change_redraws_the_report_on_screen():
    apply = _function(I18N, "applyLanguage")
    assert "mgrRelocalize()" in apply
    relocalize = _function(MANAGER, "mgrRelocalize")
    assert "renderManagerReport(_mgrShown.data, _mgrShown.currency, _mgrShown.chartInputs)" in relocalize
    assert "lastManagerReport !== _mgrShown.data" in relocalize          # not a report another panel drew
    render = _function(MANAGER, "renderManagerReport")
    assert "renderManagerReportChart(data, chartInputs)" in render
    # the redraw fetches the periods the report was run for, not the form's current values
    assert "inputs || managerChartInputs()" in CHARTS
    run = _function(MANAGER, "runManagerReport")
    assert run.index("managerChartInputs()") < run.index("await fetch(url)")


def test_period_names_follow_every_ui_language():
    core = (JS / "01-core.js").read_text(encoding="utf-8")
    fmt = _function(core, "formatPeriodKey")
    assert "{ fa: 'fa-IR-u-ca-gregory', es: 'es-ES', ar: 'ar-u-nu-latn' }[ui]" in fmt       # Gregorian months
    assert "_J_MONTHS[ui] || _J_MONTHS.en" in fmt and "_SEASONS[ui] || _SEASONS.en" in fmt
    assert "(Spring|Summer|Autumn|Winter)" in fmt                                           # "2026-Summer" keys
    seasons = core[core.index("const _SEASONS = {"):core.index("function formatPeriodKey")]
    for lang, word in (("fa", "پاییز"), ("ar", "الخريف"), ("es", "Otoño"), ("en", "Autumn")):
        assert f"{lang}: [" in seasons and word in seasons, lang
    assert "ar: ['فروردين'" in core
