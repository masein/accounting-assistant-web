"""A date field is in the company's calendar (dressDateInputs, js/03-ui.js):
with the Jalali calendar chosen, dates were typed and picked in the browser's
Gregorian field. tests_e2e/test_jalali_date_field.py drives it; these pin the
places it must run from, so a refactor can't leave a field undressed."""
from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
UI = (JS / "03-ui.js").read_text(encoding="utf-8")
CORE = (JS / "01-core.js").read_text(encoding="utf-8")
SETTINGS = (JS / "10-forms-fx-bank.js").read_text(encoding="utf-8")
CSS = (JS.parent / "css" / "app.css").read_text(encoding="utf-8")


def test_the_observer_dresses_date_fields():
    run = re.search(r"const run = \(\) => \{(.*?)\};", UI)
    assert run and "dressDateInputs()" in run.group(1)


def test_the_calendar_loading_or_changing_redresses_them():
    load = re.search(r"async function loadDisplayCalendar\(\) \{(.*?)\n    \}", CORE, re.S)
    assert load and "dressDateInputs()" in load.group(1)
    assert "window.__DISPLAY_CALENDAR = data.calendar;" in SETTINGS
    after = SETTINGS.split("window.__DISPLAY_CALENDAR = data.calendar;", 1)[1][:400]
    assert "dressDateInputs()" in after


def test_the_native_input_stays_the_value():
    # a script's input.value = … repaints the box; the native input is still in the page
    assert "set(v) { _dateInputValue.set.call(this, v); paintDateField(this); }" in UI
    assert re.search(r"\.jdate \.jdate-native \{[^}]*opacity: 0", CSS)
    assert ".jdate + .jalali-hint { display: none; }" in CSS


def test_only_the_jalali_calendar_dresses():
    assert "const jalali = (window.__DISPLAY_CALENDAR || 'gregorian') === 'jalali';" in UI
    assert "jalali ? paintDateField(input) : _undressDateField(input)" in UI


def test_a_range_of_dates_reads_in_the_pages_direction():
    """"1405/01/17 → 1406/01/16" after Persian words ran backwards, its arrow
    pointing at the start; the time-billing preview printed raw ISO dates. A
    left-to-right page keeps it as one left-to-right run; Persian and Arabic say
    «a تا b» / «a إلى b» in the page's direction with each date isolated (the
    forced run put the end date first for a right-to-left reader, Persian copy
    review 2026-10-31). tests_e2e/test_jalali_date_field.py runs it."""
    core = (JS / "01-core.js").read_text(encoding="utf-8")
    assert "function formatDateRange(from, to)" in core
    assert "if (_uiIsRtl()) return tf('dateRange', { from: '\\u2066' + a + '\\u2069', to: '\\u2066' + b + '\\u2069' });" in core
    assert "return '\\u2066' + tf('dateRange', { from: a, to: b }) + '\\u2069';" in core
    from tests.i18n_source import pack_values
    values = pack_values()
    assert {lang: values[lang]["dateRange"] for lang in values} == {
        "en": "{from} → {to}", "es": "{from} → {to}", "fa": "{from} تا {to}", "ar": "{from} إلى {to}"}
    reports = (JS / "07-chat-reports.js").read_text(encoding="utf-8")
    payroll = (JS / "11-time-expenses-payroll.js").read_text(encoding="utf-8")
    assert "formatDateRange(from, to)" in reports
    assert "formatDateRange(rs.effective_from, rs.effective_to)" in payroll
    assert "formatDateRange(pv.period_from, pv.period_to)" in payroll


def test_the_placeholder_is_the_form_not_a_day():
    assert "text.placeholder = t('jdatePlaceholder');" in UI
    assert "'1405/07/09'" not in UI
