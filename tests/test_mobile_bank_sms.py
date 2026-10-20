"""Bank SMS read on the phone (scenarios Y1–Y4; roadmap ROADMAP_ANDROID_CHAT P2.2, §7.1).

In the builds outside Play the app reads SMS from senders the user said are
their bank and sends them here with the sender and the time. Each becomes a
row of the bank's SMS statement to record, as a pasted SMS does; the phone is
told on its feed and in the briefing, and records them one voucher at a time."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import jdatetime
from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import API, phone  # noqa: F401  (fixture)

YESTERDAY = date.today() - timedelta(days=1)
_J = jdatetime.date.fromgregorian(date=YESTERDAY)
STAMP = f"{_J.month:02d}{_J.day:02d}"
MONTH = f"{_J.year}/{_J.month:02d}"
WITHDRAWAL = f"بانک ملت\nبرداشت:125,000\nحساب:123456789\nمانده:1,234,567\n{STAMP}-13:45"
DEPOSIT = f"بانک ملت\nواریز:65,433+\nحساب:123456789\nمانده:1,300,000\n{STAMP}-19:10"
AT = datetime.combine(YESTERDAY, datetime.min.time(), tzinfo=timezone(timedelta(hours=3, minutes=30))) + timedelta(hours=20)


def _send(client, phone, *messages, expect=200):
    body = {"messages": [{"sender": "98700717", "text": t, "received_at": AT.isoformat(), **extra}
                         for t, extra in ((m, {}) if isinstance(m, str) else m for m in messages)]}
    r = client.post(f"{API}/bank-sms", headers=phone, json=body)
    assert r.status_code == expect, r.text
    return r.json()


def _rows(db, co):
    from app.models.bank_statement import BankStatement, BankStatementRow
    with use_company(co["cid"]):
        return db.execute(select(BankStatement.bank_name, BankStatementRow.tx_date, BankStatementRow.debit,
                                 BankStatementRow.credit)
                          .join(BankStatementRow, BankStatementRow.statement_id == BankStatement.id)
                          .order_by(BankStatementRow.row_index)).all()


def test_y1_the_phone_files_bank_sms_once(client, db, co, phone):
    from app.models.audit_log import AuditLog
    out = _send(client, phone, WITHDRAWAL, DEPOSIT)
    assert (out["received"], out["added"], out["duplicates"], out["unread"]) == (2, 2, 0, 0)
    assert len(out["statements"]) == 1 and out["statements"][0]["rows"] == 2
    rows = _rows(db, co)
    assert [(r.tx_date, r.debit, r.credit) for r in rows] == [(YESTERDAY, 125_000, 0), (YESTERDAY, 0, 65_433)]
    assert rows[0].bank_name.startswith("Mellat")
    with use_company(co["cid"]):
        audit = db.execute(select(AuditLog).where(AuditLog.action == "bank_sms_forward")).scalars().all()
    assert [a.detail for a in audit] == ["from the phone: 2 added, 0 already on file, 0 unread"]
    again = _send(client, phone, WITHDRAWAL, DEPOSIT)                   # the outbox sent them twice
    assert (again["added"], again["duplicates"]) == (0, 2) and len(_rows(db, co)) == 2


def test_the_phone_dates_and_names_what_the_text_does_not(client, db, co, phone):
    """A bank SMS without a date or the bank's name: the day it arrived, and the
    bank the user said its sender is."""
    out = _send(client, phone, ("برداشت:40,000\nمانده:960,000", {"bank": "Saman"}))
    assert out["added"] == 1, out
    [row] = _rows(db, co)
    assert row.tx_date == YESTERDAY and row.bank_name.startswith("Saman") and row.debit == 40_000
    # a hint that isn't a bank is not taken
    out = _send(client, phone, ("برداشت:41,000\nمانده:919,000", {"bank": "Nowhere"}))
    # by its amount: both statements' rows are row 1, in no set order on Postgres
    [other] = [r for r in _rows(db, co) if r.debit == 41_000]
    assert out["added"] == 1 and other.bank_name.startswith("Bank")


def test_y3_what_isnt_a_movement_and_too_many(client, db, co, phone):
    out = _send(client, phone, "بانک ملت\nرمز پویا: 482913\nاعتبار ۲ دقیقه")
    assert (out["added"], out["unread"]) == (0, 1) and _rows(db, co) == []
    r = _send(client, phone, *[WITHDRAWAL] * 51, expect=422)
    assert r["detail"] == "At most 50 messages at a time"
    assert _rows(db, co) == []


def test_y4_the_feed_and_the_briefing_say_what_to_record(client, db, co, phone):
    _send(client, phone, WITHDRAWAL, DEPOSIT)
    feed = client.get(f"{API}/feed", headers=phone).json()["items"]
    [item] = [i for i in feed if i["kind"] == "bank_sms"]
    assert item["title"] == "Bank SMS to record" and item["body"] == f"Mellat ({MONTH}): 2 to record."
    sid = item["key"].split(":")[1]
    assert item["key"] == f"bank_sms:{sid}:2"

    brief = client.post(f"{API}/briefing", headers=phone, json={}).json()
    [line] = [b for b in brief["blocks"] if b.get("kind") == "statement_next"]
    assert line["statement_id"] == sid and line["text"] == f"Mellat ({MONTH}): 2 to record."
    # Next offers the first row as a voucher
    nxt = client.post(f"{API}/statements/{sid}/next", headers=phone, json={"thread_id": brief["thread_id"]}).json()
    assert nxt["blocks"][0]["type"] == "proposal", nxt

    # in Persian: the bank by its name, the figures in Persian digits
    assert client.put(f"{API}/me/language", headers=phone, json={"language": "fa"}).status_code == 200
    feed = client.get(f"{API}/feed", headers=phone).json()["items"]
    [item] = [i for i in feed if i["kind"] == "bank_sms"]
    from app.services.documents.formatting import to_persian_digits
    assert item["title"] == "پیامک بانکی برای ثبت"
    assert item["body"] == to_persian_digits(f"بانک ملت ({MONTH}): 2 پیامک برای ثبت.")


def test_y4_once_recorded_the_line_goes(client, db, co, phone):
    _send(client, phone, WITHDRAWAL)
    sid = next(i for i in client.get(f"{API}/feed", headers=phone).json()["items"] if i["kind"] == "bank_sms")["key"].split(":")[1]
    nxt = client.post(f"{API}/statements/{sid}/next", headers=phone, json={}).json()
    voucher = nxt["blocks"][0]
    assert voucher["type"] == "proposal", nxt
    r = client.post(f"{API}/proposals/{voucher['token']}/confirm", headers=phone, json={})
    assert r.status_code == 200, r.text
    feed = client.get(f"{API}/feed", headers=phone).json()["items"]
    assert [i for i in feed if i["kind"] == "bank_sms"] == []
    brief = client.post(f"{API}/briefing", headers=phone, json={}).json()
    assert [b for b in brief["blocks"] if b.get("kind") == "statement_next"] == []
