"""Persian amount/date understanding + entity details in chat (acc-issues.pdf).

Server-reported failures: «۲۱۵ میلیون تومان» proposed as 215,000,000 IRR and
the user's rial correction rejected by the amount guard; «۴ مرداد» dated as
today; the bank's IBAN invisible to the assistant ("not recorded")."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

from app.models.entity import Entity
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.date_resolver import jalali_named_date, resolve_entry_date
from app.services.ai_accountant.orchestrator import _numbers_in_text
from app.services.ai_accountant.read_tools import FindEntity, FindEntityInput

M_TOMAN = "در تاریخ ۴ مرداد ماه مبلغ ۲۱۵ میلیون تومان از دی فرمانیه زدیم به حساب اینده جردن"
M_CORRECTION = "نه ببین ۲۱۵ میلیون تومان میشه ۲ میلیارد و ۱۵۰ میلیون ریال"


def test_persian_magnitudes_and_toman_variants():
    amounts = set(_numbers_in_text(M_TOMAN))
    assert 215_000_000 in amounts          # ۲۱۵ میلیون
    assert 2_150_000_000 in amounts        # the ×10 toman→rial conversion


def test_compound_magnitude_parses():
    amounts = set(_numbers_in_text(M_CORRECTION))
    assert 2_150_000_000 in amounts        # ۲ میلیارد و ۱۵۰ میلیون


def test_jalali_named_date_resolves_serverside():
    today = date(2026, 8, 2)  # 1405/05/11
    assert jalali_named_date(M_TOMAN, today) == date(2026, 7, 26)      # 1405/05/04
    assert jalali_named_date("۱۲ دی ماه حقوق دادیم", today) == date(2027, 1, 2)
    assert jalali_named_date("۴ مرداد ۱۴۰۴", today) == date(2025, 7, 26)
    # the bank named دی must NOT read as the month دی
    assert jalali_named_date("از بانک دی فرمانیه پرداخت شد", today) is None
    resolved = resolve_entry_date(M_TOMAN, date(2026, 8, 2), today=today,
                                  has_attachment=False, scheduled=False)
    assert resolved == date(2026, 7, 26)


def test_find_entity_returns_bank_details(db):
    name = f"بانک جزئیات {uuid.uuid4().hex[:6]}"
    db.add(Entity(type="bank", name=name, code="1119",
                  iban="IR820540102680020817909002",
                  account_number="0106881965003", bank_name=name))
    db.flush()
    ctx = ToolContext(db=db, user_id="u-details", username="t", user_message="q")
    out = asyncio.run(FindEntity().run(ctx, FindEntityInput(query=name)))
    match = out["matches"][0]
    assert match["iban"] == "IR820540102680020817909002"
    assert match["account_number"] == "0106881965003"
