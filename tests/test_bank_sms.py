"""Bank SMS capture (roadmap 2026-09 §4.1): parsing the messages Iranian
banks send, splitting pasted batches, filing them into monthly SMS-feed
statements (duplicates skipped, balance gaps flagged), the paste route and
the API-key webhook, and the hand-off to the statement review."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.api_key_auth import generate_api_key
from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.api_key import ApiKey
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.company import Company
from app.services.bank_sms import ingest, normalize, parse_sms, split_messages

TODAY = date(2026, 9, 26)          # 1405/07/04

MELLAT = "بانک ملت\nبرداشت:125,000\nحساب:123456789\nمانده:1,234,567\n0703-13:45"
MELLAT_2 = "بانک ملت\nواریز:65,433+\nحساب:123456789\nمانده:1,300,000\n0704-09:10"
MELLI = "بانك ملي ايران\nواريز:2,500,000\nاز 0123456789\nمانده:9,876,543\n05/07/02_12:30"
SAMAN = "بانک سامان\nبرداشت از حساب 849-800-1234-1\nمبلغ: 150,000 ریال\nمانده: 3,000,000\n1405/07/01 - 18:20"
TEJARAT = "بانك تجارت\nحساب:0123456\nبرداشت(خريد):250,000\nمانده:1,000,000\n1405/07/03-09:10"
PASARGAD = "بانک پاسارگاد\nانتقال\nمبلغ:1,000,000-\nحساب:201.8000.1234.1\nمانده:5,000,000\n07/02 10:00"
AYANDEH = "بانک آینده واریز ۵۰۰٬۰۰۰ تومان به حساب ۰۲۰۱۲۳۴۵۶۷ مانده ۱٬۲۰۰٬۰۰۰ تومان ۱۴۰۵/۰۷/۰۳"
SADERAT = "بانک صادرات\nخرید کارت 6037****1234\nمبلغ 85,000-\nمانده 2,000,000\n1405/07/04 20:15"


# --- parsing ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, bank, direction, amount, balance, account, day, time", [
    (MELLAT, "Mellat", "debit", 125_000, 1_234_567, "123456789", date(2026, 9, 25), "13:45"),
    (MELLI, "Melli", "credit", 2_500_000, 9_876_543, "0123456789", date(2026, 9, 24), "12:30"),
    (SAMAN, "Saman", "debit", 150_000, 3_000_000, "84980012341", date(2026, 9, 23), "18:20"),
    (TEJARAT, "Tejarat", "debit", 250_000, 1_000_000, "0123456", date(2026, 9, 25), "09:10"),
    (PASARGAD, "Pasargad", "debit", 1_000_000, 5_000_000, "201800012341", date(2026, 9, 24), "10:00"),
    (AYANDEH, "Ayandeh", "credit", 5_000_000, 12_000_000, "0201234567", date(2026, 9, 25), None),  # تومان × 10
    (SADERAT, "Saderat", "debit", 85_000, 2_000_000, "6037****1234", date(2026, 9, 26), "20:15"),
    (MELLAT_2, "Mellat", "credit", 65_433, 1_300_000, "123456789", date(2026, 9, 26), "09:10"),
])
def test_bank_formats(text, bank, direction, amount, balance, account, day, time):
    p = parse_sms(text, today=TODAY)
    assert p.ok, p.problems
    assert (p.bank, p.direction, p.amount, p.balance, p.account, p.tx_date, p.time) == \
        (bank, direction, amount, balance, account, day, time)


def test_normalisation():
    assert normalize("بانك ملي‏ ۱۲۳") == "بانک ملی 123"


def test_a_date_is_always_in_the_past():
    # 0710 on 1405/07/04 is last year's 10 Mehr, never next week
    assert parse_sms("بانک ملت\nبرداشت:1,000\n0710-10:00", today=TODAY).tx_date == date(2025, 10, 2)
    # 1228 typed in early Farvardin is the Esfand just gone
    early = date(2026, 3, 23)      # 1405/01/03
    assert parse_sms("بانک ملت\nبرداشت:1,000\n1228-10:00", today=early).tx_date == date(2026, 3, 19)


@pytest.mark.parametrize("text, problem", [
    ("بانک رفاه\nانتقال:300,000\nمانده:100,000\n0702-08:00", "could not tell money in from money out"),
    ("سلام، کد تایید شما 12345 است", "no amount found"),
    ("بانک ملت\nبرداشت:125,000\nمانده:1,000", "no date found"),
    ("", "empty message"),
])
def test_unreadable_messages_say_why(text, problem):
    p = parse_sms(text, today=TODAY)
    assert not p.ok and problem in p.problems


def test_split_pasted_batches():
    assert len(split_messages(f"{MELLAT}\n\n{MELLI}\n\n\n{SAMAN}")) == 3
    assert len(split_messages(f"{MELLAT}\n{MELLI}")) == 2                 # a new bank header starts a message
    assert split_messages(MELLAT) == [MELLAT]
    assert split_messages("  \n ") == []


# --- filing ---------------------------------------------------------------------------------

@pytest.fixture()
def co(db, client):
    c = Company(id=uuid.uuid4(), name="SMS Co", slug=f"sms-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(c.id), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"company": c, "login": login, "client": client}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _ingest(db, co, text):
    with use_company(co["company"].id):
        out = ingest(db, text, today=TODAY)
        db.commit()
    return out


def _statements(db, co):
    with use_company(co["company"].id):
        return db.execute(select(BankStatement).where(BankStatement.source_type == "sms")).scalars().all()


def test_messages_become_rows_of_a_monthly_feed(db, co):
    out = _ingest(db, co, f"{MELLAT}\n\n{MELLAT_2}\n\n{MELLI}")
    assert (out["received"], out["added"], out["duplicates"], out["unparsed"], out["gaps"]) == (3, 3, 0, [], [])
    stmts = {s.bank_name: s for s in _statements(db, co)}
    assert set(stmts) == {"Mellat (SMS 1405/07)", "Melli (SMS 1405/07)"}
    mellat = stmts["Mellat (SMS 1405/07)"]
    assert mellat.total_rows == 2 and mellat.account_number == "123456789"
    assert (mellat.from_date, mellat.to_date) == (date(2026, 9, 25), date(2026, 9, 26))
    with use_company(co["company"].id):
        rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == mellat.id)
                          .order_by(BankStatementRow.row_index)).scalars().all()
    assert [(r.row_index, r.debit, r.credit, r.balance) for r in rows] == \
        [(0, 125_000, 0, 1_234_567), (1, 0, 65_433, 1_300_000)]
    assert rows[0].description.startswith("SMS Mellat") and rows[0].raw_text == normalize(MELLAT)
    assert rows[1].confidence == 0.95             # 1,234,567 + 65,433 = 1,300,000: the balances chain


def test_the_same_message_twice_is_filed_once(db, co):
    _ingest(db, co, MELLAT)
    out = _ingest(db, co, f"{MELLAT}\n\n{MELLAT_2}")
    assert (out["added"], out["duplicates"]) == (1, 1)


def test_a_balance_gap_is_flagged(db, co):
    _ingest(db, co, MELLAT)
    jump = "بانک ملت\nبرداشت:10,000\nحساب:123456789\nمانده:900,000\n0704-10:00"   # 1,234,567 − 10,000 ≠ 900,000
    out = _ingest(db, co, jump)
    assert out["added"] == 1 and len(out["gaps"]) == 1
    assert out["gaps"][0]["expected"] == 1_224_567 and out["gaps"][0]["reported"] == 900_000


def test_unreadable_ones_come_back_and_the_rest_are_filed(db, co):
    out = _ingest(db, co, f"{MELLAT}\n\nسلام، کد تایید شما 12345 است")
    assert out["added"] == 1 and len(out["unparsed"]) == 1 and "no amount found" in out["unparsed"][0]["problems"]


def test_a_new_month_opens_a_new_feed(db, co):
    _ingest(db, co, MELLAT)
    _ingest(db, co, "بانک ملت\nبرداشت:5,000\nحساب:123456789\nمانده:1,229,567\n0629-10:00")   # Shahrivar
    assert {s.bank_name for s in _statements(db, co)} == {"Mellat (SMS 1405/07)", "Mellat (SMS 1405/06)"}


def test_too_many_at_once(db, co):
    with use_company(co["company"].id), pytest.raises(ValueError):
        ingest(db, [MELLAT] * 201, today=TODAY)


# --- HTTP ----------------------------------------------------------------------------------------

def test_paste_route_preview_and_review(db, co):
    owner = co["login"]("owner")
    prev = owner.post("/bank-sms/preview", json={"text": f"{MELLAT}\n\nhello"}).json()
    assert [bool(m["problems"]) for m in prev["messages"]] == [False, True]
    r = owner.post("/bank-sms", json={"text": MELLI})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 1 and body["rows"][0]["bank"] == "Melli"
    sid = body["statements"][0]["id"]
    listed = owner.get("/brain/bank-statements").json()
    assert any(s["id"] == sid and s["source_type"] == "sms" for s in listed)
    assert owner.post(f"/brain/bank-statements/{sid}/review").status_code == 200   # the usual review works
    assert owner.post("/bank-sms", json={"text": ""}).status_code == 422
    assert co["login"]("viewer").post("/bank-sms", json={"text": MELLI}).status_code == 403
    assert co["login"]("personal").post("/bank-sms", json={"text": SAMAN}).status_code == 200


def _key(db, co, scopes):
    raw, digest, prefix = generate_api_key()
    db.add(ApiKey(company_id=co["company"].id, label="phone", key_hash=digest, prefix=prefix, scopes=scopes))
    db.commit()
    return {"Authorization": f"Bearer {raw}"}


def test_phone_webhook_needs_the_scope(db, co):
    c = co["client"]
    good = _key(db, co, "bank_sms:write")
    r = c.post("/api/v1/bank-sms", json={"text": SAMAN}, headers=good)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    r = c.post("/api/v1/bank-sms", json={"messages": [TEJARAT, SAMAN]}, headers=good)
    assert (r.json()["added"], r.json()["duplicates"]) == (1, 1)
    assert c.post("/api/v1/bank-sms", json={}, headers=good).status_code == 422
    assert c.post("/api/v1/bank-sms", json={"text": SAMAN}, headers=_key(db, co, "time:write")).status_code == 403
    assert {s.bank_name for s in _statements(db, co)} == {"Saman (SMS 1405/07)", "Tejarat (SMS 1405/07)"}


def test_the_new_scope_is_offered_on_keys():
    from app.core.api_key_auth import DEFAULT_SCOPES, SCOPES
    assert "bank_sms:write" in SCOPES and "bank_sms:write" not in DEFAULT_SCOPES
