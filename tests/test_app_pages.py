"""The chat knows where the app's own files are (chat review 2026-11-10, scenario BX1).

From production: asked for the journal as Excel, the assistant ran the month-end checklist for the month under
way, said it had no tool, and pointed to "Manager reports → Monthly close pack" in English, the wrong report.
"""
from __future__ import annotations

import asyncio


def test_bx1_the_journal_excel_is_where_the_page_shows_it():
    from app.services.ai_accountant import tool_routing as r
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert "# The app's own pages" in SYSTEM_PROMPT
    assert "«گزارش‌های مدیریتی» ← گزارش «دفتر روزنامه» ← دکمهٔ «دفتر روزنامه (اکسل)»" in SYSTEM_PROMPT
    assert "Manager reports → Monthly close pack." not in SYSTEM_PROMPT.split("# The app's own pages")[0]
    # sent with every message, whatever it is routed to
    groups = r.route("دفتر روزنامه سه ماه اول رو اکسل می‌خوام برای دارایی", personal=False)
    assert "# The app's own pages" in r.instructions(SYSTEM_PROMPT, groups)


def test_bx1_the_close_pack_in_the_users_language(db):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.period_tools import GetCloseChecklist, GetCloseChecklistInput
    out = asyncio.run(GetCloseChecklist().run(ToolContext(db=db, user_id="u-pages"), GetCloseChecklistInput()))
    assert out["pack"]["fa"] == "گزارش‌های مدیریتی ← بستهٔ پایان ماه" and out["pack"]["en"].startswith("Manager reports")
