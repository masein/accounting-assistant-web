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
