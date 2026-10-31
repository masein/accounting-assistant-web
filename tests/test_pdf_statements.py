"""A bank's statement as a scanned PDF, every page of it (scenarios AJ1–AJ9).

From the owner: Day Bank's own statement of فرهنگ نو's account, 22 pages of
pictures. The vision reader read it right and stopped after page 4, saying
nothing of the other 18 (2026-10-20). Here the PDF is made of pictures too
(no text layer) and the model is scripted: each page answers with its own
rows, newest first, the running balance following from row to row."""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.bank_statement import BankStatement, BankStatementRow

XLSX = "application/pdf"
HEAD = {"is_bank_statement": True, "bank": "Day Bank", "account_number": "۰۱۰۵۲۲۲۰۲۸۰۰۰"}


def _pdf(pages: int) -> bytes:
    """A PDF of pictures: shapes on each page, no text to read."""
    import fitz
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(50, 50 + i, 300, 200), color=(0, 0, 0))
    out = doc.tobytes()
    doc.close()
    return out


def _statement(pages: int, per_page: int = 3, *, salt: int = 0) -> dict[int, list[dict]]:
    """Rows printed newest first, a page at a time, each balance following from the one below it."""
    balance = 10_000_000 + salt
    day = date(2026, 6, 22)
    oldest_first = []
    for n in range(pages * per_page):
        amount = 100_000 + 1_000 * n + salt
        direction = "credit" if n % 3 else "debit"
        balance += amount if direction == "credit" else -amount
        day += timedelta(days=1)
        oldest_first.append({"date": day.isoformat(), "description": f"ردیف {n + 1}", "amount": amount,
                             "balance": balance, "direction": direction})
    printed = list(reversed(oldest_first))
    return {p + 1: printed[p * per_page:(p + 1) * per_page] for p in range(pages)}


@pytest.fixture()
def vision(monkeypatch):
    """The vision model, scripted by page: ``vision.pages`` answers, ``vision.fail`` pages that never read."""
    from app.services import ocr_extract

    class Script:
        pages: dict[int, list[dict]] = {}
        fail: set[int] = set()
        head = dict(HEAD)
        calls: list[int] = []

    def raster(path, ctype, max_pages=4, first_page=0):
        return [("page", str(first_page + 1))]

    async def vision_raw(pages, prompt):
        n = int(pages[0][1])
        if prompt is ocr_extract._STATEMENT_HEAD_PROMPT:
            return json.dumps(Script.head, ensure_ascii=False)
        Script.calls.append(n)
        if n in Script.fail:
            raise RuntimeError("a page the model can't read")
        return json.dumps(Script.pages.get(n, []), ensure_ascii=False)

    monkeypatch.setattr(ocr_extract, "_rasterize_pages", raster)
    monkeypatch.setattr(ocr_extract, "_vision_raw", vision_raw)
    from app.core.config import settings
    monkeypatch.setattr(settings, "statement_read_inline", True)
    monkeypatch.setattr(settings, "statement_llm_categorization", False)
    Script.calls = []
    Script.fail = set()
    return Script


def _upload(api, data: bytes, name: str):
    r = api.post("/brain/bank-statements/upload", files={"file": (name, io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def _rows(db, sid):
    return db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == uuid.UUID(str(sid)))
                      .order_by(BankStatementRow.row_index)).scalars().all()


# --- AJ1, AJ2, AJ5: every page, the balance agreeing, its bank and account --------------------------------------

def test_aj1_aj2_aj5_every_page_is_read_and_its_balance_agrees(auth_client, db, vision):
    vision.pages = _statement(6, salt=11)
    out = _upload(auth_client, _pdf(6), f"day-{uuid.uuid4().hex[:4]}.pdf")
    assert out["status"] == "parsed" and out["total_rows"] == 18 and out["pages_total"] == 6
    assert sorted(set(vision.calls)) == [1, 2, 3, 4, 5, 6]                     # every page, not the first four
    rep = out["read_report"]
    assert (rep["pages"], rep["pages_read"], rep["rows"], rep["failed_pages"]) == (6, 6, 18, [])
    assert rep["balance"]["links"] == 17 and rep["balance"]["broken_count"] == 0
    assert rep["balance"]["order"] == "newest_first"
    stmt = db.get(BankStatement, uuid.UUID(out["id"]))
    assert (stmt.bank_name, stmt.account_number) == ("Day", "0105222028000")   # from its first page (AJ5)
    rows = _rows(db, out["id"])
    assert [r.description for r in rows[:2]] == ["ردیف 18", "ردیف 17"]          # in the statement's order
    assert stmt.from_date == date(2026, 6, 23) and stmt.to_date == date(2026, 7, 10)
    from app.services.ai_accountant.statement_intake import reading_said
    assert reading_said(rep, "fa") == "۶ صفحه، ۱۸ ردیف خوانده شد؛ مانده‌ها از هر ردیف به ردیف بعد جور است."


# --- AJ3, AJ4: a misread named, a page it can't read named -------------------------------------------------------

def test_aj3_aj4_a_misread_and_an_unread_page_are_named(auth_client, db, vision):
    vision.pages = _statement(5, salt=23)
    vision.pages[3][1] = {**vision.pages[3][1], "balance": vision.pages[3][1]["balance"] + 9_000}   # a digit misread
    vision.fail = {4}
    out = _upload(auth_client, _pdf(5), f"day-{uuid.uuid4().hex[:4]}.pdf")
    rep = out["read_report"]
    assert rep["failed_pages"] == [4] and rep["pages_read"] == 4 and out["total_rows"] == 12
    assert vision.calls.count(4) == 2                                            # tried once more
    assert rep["balance"]["broken"][0]["page"] == 3 and rep["balance"]["broken"][0]["row"] == 2
    from app.services.ai_accountant.statement_intake import reading_said
    said = reading_said(rep, "fa")
    assert "مانده در صفحهٔ ۳، ردیف ۲ جور نیست" in said and "صفحهٔ ۴ خوانده نشد" in said



def test_aj3_a_misread_amount_is_set_right_from_the_balances_either_side(auth_client, db, vision):
    """Day Bank's own: 847,522,020 read as 47,522,020 and 320,000 as 32,000 — both
    exactly what the balances on either side said (2026-10-20)."""
    vision.pages = _statement(5, salt=29)
    true = dict(vision.pages[2][1])
    vision.pages[2][1] = {**true, "amount": true["amount"] // 10}                    # its last digit missed
    flipped = dict(vision.pages[4][0])
    vision.pages[4][0] = {**flipped, "direction": "debit" if flipped["direction"] == "credit" else "credit"}
    out = _upload(auth_client, _pdf(5), f"day-{uuid.uuid4().hex[:4]}.pdf")
    rep = out["read_report"]
    assert rep["balance"]["broken_count"] == 0 and rep["balance"]["agree"] == rep["balance"]["links"] == 14
    assert [(m["page"], m["row"], m["read"], m["amount"]) for m in rep["mended"]] == [
        (2, 2, true["amount"] // 10, true["amount"]), (4, 1, flipped["amount"], flipped["amount"])]
    rows = _rows(db, out["id"])
    assert rows[4].credit == true["amount"] and rows[4].debit == 0                # the books get the amount printed
    assert rows[4].confidence == 0.6 and rows[3].confidence == 0.85               # amber in the table
    assert (rows[9].credit, rows[9].debit) == ((flipped["amount"], 0) if flipped["direction"] == "credit"
                                               else (0, flipped["amount"]))
    from app.services.ai_accountant.statement_intake import reading_said
    said = reading_said(rep, "en")
    assert "the balance agrees" in said and "2 misread amount(s) set right" in said
    assert f"page 2, row 2: {true['amount'] // 10:,} → {true['amount']:,}" in said
    assert "صفحهٔ ۲، ردیف ۲: ۱۱,۰۰۲ ← ۱۱۰,۰۲۹" in reading_said(rep, "fa")

# --- AJ6, AJ7: in the chat, a statement called a receipt, read after the reply, then the instructions ------------

def test_aj6_aj7_in_the_chat_read_after_the_reply_then_continued(auth_client, db, vision, monkeypatch):
    from app.api import ai_accountant as api_mod
    from app.services import statement_import
    vision.pages = _statement(4, salt=37)
    real_reading = statement_import._read_in_background
    started = []

    async def later(*args, **kwargs):            # the reading isn't done by the reply
        started.append(args)
    monkeypatch.setattr(statement_import, "_read_in_background", later)
    data = _pdf(4)
    r = auth_client.post("/transactions/attachments",
                         files={"file": ("20261015_133322_receipt.pdf", io.BytesIO(data), "application/pdf")})
    att = r.json()["id"]
    rules = "واریزها بستانکار درآمد شهریه، برداشت‌ها بدهکار مخارج مدرسه، کارمزدها کارمزد بانکی، به تفکیک تاریخ"
    turns = []

    async def model(db_, **kw):
        turns.append(kw)
        from app.services.ai_accountant.orchestrator import ChatResult
        return ChatResult(session_id=kw.get("session_id") or str(uuid.uuid4()), text="کارت آماده است.")
    monkeypatch.setattr(api_mod, "run_chat_turn", model)
    body = auth_client.post("/ai-accountant/chat", json={"message": rules, "attachment_ids": [att]}).json()
    intake = body["intake"]
    assert intake["kind"] == "bank_statement" and intake["status"] == "reading"     # a receipt? its first page says not (AJ7)
    assert intake["pages_total"] == 4 and intake["rules"] is True
    assert intake["bank_name"] == "Day"                                         # «شهریه» isn't Bank Shahr
    assert "صفحه به صفحه" in body["text"] and "ادامه با دستور شما" in body["text"]
    assert turns == []                                                              # nothing to post yet
    card = auth_client.get(f"/ai-accountant/statements/{intake['statement_id']}/card").json()
    assert card["status"] == "reading" and card["pages_total"] == 4

    # the reading, as the server does it after the reply
    sid, company, path, ctype = started[0]
    asyncio.run(real_reading(sid, company, path, ctype, bind=db.get_bind()))
    card = auth_client.get(f"/ai-accountant/statements/{intake['statement_id']}/card").json()
    assert card["status"] == "parsed" and card["intake"]["total_rows"] == 12
    assert "4 pages, 12 rows read; the balance agrees" in card["text"]     # in the reader's (English) language

    # «ادامه با دستور شما»: the same words and file, the statement read — the model gets it now
    auth_client.post("/ai-accountant/chat", json={"message": rules, "attachment_ids": [att]})
    assert turns and f"statement_id={intake['statement_id']}" in (turns[0].get("ocr_context") or "")


# --- AJ8: stopped half-way ----------------------------------------------------------------------------------------

def test_aj8_a_reading_that_stopped_is_said_and_the_file_comes_again(auth_client, db, vision):
    from datetime import datetime, timezone
    stmt = BankStatement(bank_name="Day", source_type="ocr_pdf", source_filename="day.pdf", status="reading",
                         content_hash=uuid.uuid4().hex, total_rows=0, pages_total=22, pages_read=7)
    db.add(stmt)
    db.commit()
    fresh = auth_client.get(f"/brain/bank-statements/{stmt.id}/reading").json()
    assert (fresh["status"], fresh["pages_read"], fresh["pages_total"]) == ("reading", 7, 22)
    stmt.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)                  # nothing for months
    db.commit()
    stopped = auth_client.get(f"/brain/bank-statements/{stmt.id}/reading").json()
    assert stopped["status"] == "failed" and stopped["read_report"]["stopped"] is True
    from app.services.ai_accountant.statement_intake import reading_said
    assert reading_said(stopped["read_report"], "fa").startswith("خواندن صورت‌حساب پیش از پایان متوقف شد")


def test_aj8_the_same_file_after_a_stopped_read_is_read_again(auth_client, db, vision):
    import hashlib
    data = _pdf(3)
    vision.pages = _statement(3, salt=41)
    db.add(BankStatement(bank_name="Day", source_type="ocr_pdf", source_filename="day.pdf", status="failed",
                         content_hash=hashlib.sha256(data).hexdigest(), total_rows=0))
    db.commit()
    out = _upload(auth_client, data, "day.pdf")
    assert out["status"] == "parsed" and out["total_rows"] == 9                # not "already imported"


# --- AJ9: short ones as before ------------------------------------------------------------------------------------

def test_aj9_a_two_page_pdf_is_read_while_the_upload_waits(auth_client, db, vision, monkeypatch):
    from app.services import statement_import

    async def never(*a, **k):
        raise AssertionError("two pages are read at once")
    monkeypatch.setattr(statement_import, "_read_in_background", never)
    vision.pages = _statement(2, salt=53)
    out = _upload(auth_client, _pdf(2), f"day-{uuid.uuid4().hex[:4]}.pdf")
    assert out["status"] == "parsed" and out["total_rows"] == 6 and out["read_report"]["balance"]["broken_count"] == 0


# --- the parts ----------------------------------------------------------------------------------------------------

def test_the_balance_check_reads_either_order():
    from app.services.statement_checks import balance_check
    rows = [{"amount": 100, "direction": "credit", "balance": 1100, "page": 1},
            {"amount": 50, "direction": "debit", "balance": 1050, "page": 1},
            {"amount": 25, "direction": "credit", "balance": 1075, "page": 2}]
    oldest = balance_check(rows)
    assert oldest["order"] == "oldest_first" and oldest["broken_count"] == 0 and oldest["links"] == 2
    newest = balance_check(list(reversed(rows)))
    assert newest["order"] == "newest_first" and newest["broken_count"] == 0


def test_day_bank_is_a_bank_but_the_month_dey_is_not():
    from app.services.statement_import import guess_bank_name
    assert guess_bank_name("Day Bank") == "Day" and guess_bank_name("بانک دی") == "Day"
    assert guess_bank_name("۱ دی ۱۴۰۵") == "Unknown"


def test_one_misread_balance_is_one_row_however_many_links_it_breaks():
    from app.services.statement_checks import balance_check
    rows = [{"amount": 100, "direction": "credit", "balance": 1100, "page": 1},
            {"amount": 50, "direction": "debit", "balance": 1950, "page": 1},       # read 1950 for 1050
            {"amount": 25, "direction": "credit", "balance": 1075, "page": 2},
            {"amount": 10, "direction": "debit", "balance": 1065, "page": 2}]
    out = balance_check(rows)
    assert out["order"] == "oldest_first" and out["broken_count"] == 1
    assert out["broken"] == [{"page": 1, "row": 2, "balance": "1950"}]



def test_only_a_slip_of_a_digit_is_mended_never_a_skipped_row_or_a_page_between():
    from app.services.statement_checks import balance_check, mend_amounts
    chain = [{"amount": 100, "direction": "credit", "balance": 1100, "page": 1},
             {"amount": 50, "direction": "debit", "balance": 1050, "page": 1},
             {"amount": 25, "direction": "credit", "balance": 1075, "page": 1},
             {"amount": 10, "direction": "debit", "balance": 1065, "page": 1}]
    skipped = [dict(r) for r in chain]
    skipped[2] = {"amount": 7, "direction": "credit", "balance": 1075, "page": 1}   # 25 = 7 + a row not read
    assert mend_amounts(skipped) == [] and balance_check(skipped)["broken_count"] == 1
    gap = [dict(r) for r in chain]
    gap[2] = {**gap[2], "amount": 2, "page": 3}                                     # page 2 wasn't read
    gap[3] = {**gap[3], "page": 3}
    assert mend_amounts(gap) == []
    slip = [dict(r) for r in chain]
    slip[2] = {**slip[2], "amount": 2}                                              # 25 read as 2
    assert mend_amounts(slip) == [{"page": 1, "row": 3, "read": 2, "amount": 25, "direction": "credit"}]
    assert balance_check(slip)["broken_count"] == 0
    first = [dict(r) for r in chain]
    first[1] = {**first[1], "amount": 5}                                            # the second row: no row before to vouch
    assert mend_amounts(first) == []
