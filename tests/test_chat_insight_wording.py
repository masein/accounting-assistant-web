"""The insights the chat says (the opening briefing, the phone's
notifications) name the month and write the figures as the reader does: the
personal-books pass (2026-10-12) found «درآمد در 1405-06 100٪ کم شد», shown in
a right-to-left bubble as «100 06-1405٪»."""
from __future__ import annotations

from app.services.insight_service import Insight, briefing_text, chat_wording

DROP = Insight(key="revenue-drop-1405-06", kind="revenue_drop", severity="warning", page="reports",
               params={"pct": "100", "month": "1405-06", "current": "0", "average": "420,000,000"})


def test_the_briefing_names_the_month_and_writes_persian_figures():
    fa = briefing_text([DROP], "fa")
    assert "درآمد در شهریور ۱۴۰۵ ۱۰۰٪ کم شد" in fa, fa
    assert "۰ در برابر میانگین ۴۲۰٬۰۰۰٬۰۰۰" in fa, fa
    assert not any(ch in "0123456789" for ch in fa), fa


def test_other_languages_name_the_month_in_their_words_with_western_digits():
    en = briefing_text([DROP], "en")
    assert "Shahrivar 1405" in en and "1405-06" not in en and "420,000,000" in en, en
    ar = chat_wording(DROP, "ar")
    assert "شهريور 1405" in ar["title"] and not any(ch in "٠١٢٣٤٥٦٧٨٩" for ch in ar["title"] + ar["message"]), ar
    # the insight itself keeps its plain values for the pages and the data
    assert DROP.params["month"] == "1405-06" and DROP.localize("fa")["title"] != chat_wording(DROP, "fa")["title"]
