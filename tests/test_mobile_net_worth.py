"""A personal user's net worth in the chat (scenario N36; roadmap
ROADMAP_ANDROID_CHAT P3.5): a figure, twelve months as a line, and the
gold and currency held at today's rates — from the books, no model."""
from __future__ import annotations

import asyncio

from app.services.ai_accountant import blocks as B
from app.services.ai_accountant.fast_paths import answer, match
from tests.test_mobile_contract import contract_errors
from tests.test_net_worth import BANK, GOLD, INCOME, _hold, _post, _rate, db  # noqa: F401  (fixture)


def test_n36_net_worth_with_the_gold_at_todays_rate(db):
    _post(db, BANK, INCOME, 100_000_000)
    _post(db, GOLD, BANK, 30_000_000)                    # 10 g bought for 30m …
    _hold(db, GOLD, "GOLD_GRAM", 10)
    _rate(db, "GOLD_GRAM", 5_000_000)                    # … worth 50m today
    assert match("دارایی خالص من چقدره؟") == "net_worth" and match("what's my net worth?") == "net_worth"
    ans = asyncio.run(answer(db, "دارایی خالص من چقدره؟", user_id="u1", username="t", lang="fa", mode="personal"))
    assert ans.text.startswith("دارایی خالص شما امروز: ۱۲۰٬۰۰۰٬۰۰۰ ریال")         # 70m in the bank + 50m of gold
    assert "طلا و ارز به نرخ امروز ۲۰٬۰۰۰٬۰۰۰ بیشتر می‌ارزند" in ans.text
    figure, chart, holdings = B.data_blocks(ans.tool_calls, lang="fa", calendar="jalali")
    assert figure["type"] == "figure" and figure["value"] == 120_000_000 and figure["label"] == "دارایی خالص"
    assert chart["kind"] == "net_worth" and len(chart["points"]) == 12 and chart["points"][-1]["value"] == 120_000_000
    assert holdings["kind"] == "holdings" and holdings["rows"][0]["sub"] == "۱۰ گرم طلا"
    assert holdings["rows"][0]["value"] == 50_000_000 and holdings["rows"][0]["gain"] == 20_000_000
    for b in (figure, chart, holdings):
        assert contract_errors(b) == [], b


def test_n36_a_companys_worth_is_left_to_the_model(db):
    assert asyncio.run(answer(db, "what's our net worth?", user_id="u1", username="t", lang="en", mode="default")) is None


def test_the_same_question_twice_keeps_both_answers(db):
    """The phone keeps one card per block id: asked twice in a thread, an
    answer's table had the first one's id and didn't show (phone QA, 2026-10-12)."""
    _post(db, BANK, INCOME, 100_000_000)
    ids = []
    for _ in range(2):
        ans = asyncio.run(answer(db, "how much cash do we have?", user_id="u1", username="t", lang="en"))
        ids.append([b["id"] for b in B.data_blocks(ans.tool_calls, lang="en", calendar="gregorian")])
    assert ids[0] and len(ids[0]) == len(ids[1]) and not set(ids[0]) & set(ids[1])
