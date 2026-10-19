"""A bank turnover posted by the user's rules (scenarios AF1–AF8).

From the owner's father at a school: the bank's own export («ردیف، تاریخ، توضیحات، واریز/ برداشت،
مبلغ (ریال) "+ 28,000,000" / "- 5,241,000"، موجودی» and a summary panel beside it) dropped in the
chat with «مبالغی که علامت + دارند در بدهکار حساب بانک دی و بستانکار درآمد شهریه و مبالغی که علامت −
دارند در بستانکار بانک و بدهکار مخارج مدرسه، کارمزد بانکی … به ترتیب تاریخ». It was read as a journal
of 83 vouchers with 95 unmapped accounts, answered in English, and his rules never reached the
assistant. The file here is made up in the bank's layout; the real one stays out of the repository."""
from __future__ import annotations

import io
import json
import re
import uuid
from functools import partial

import openpyxl
import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import Transaction
from app.services.ai_accountant.llm_protocol import ChatMessage, LLMResponse, LLMUsage, ToolCall

RULES = ("از فایل گردش ۳ ماهه دوم ۱۴۰۵ ستون مبلغ (ریال) مبالغی که علامت + دارند در بدهکار حساب بانک دی و "
         "بستانکار درآمد شهریه و مبالغی که علامت - دارند در ستون بستانکار بانک و بدهکار مخارج مدرسه، کارمزد بانکی "
         "به ترتیب تاریخ ثبت شوند")
ROWS = [  # newest first, as the bank lists them
    ("1405/06/31 - 14:28:51", "خرید کالا یا سرویس با کارت 6221 از ترمینال به پرداخت ملت", "برداشت", "- 5,241,000", "3,910,384,886"),
    ("1405/06/24 - 4:33:33", " کارمزد پذیرنده شاپرک در پایا050623 تاریخ1405/06/24", "برداشت", "- 5,600", "3,915,625,886"),
    ("1405/06/24 - 4:29:13", "شاپرک تجمیعی . رهگیری :050623 فرستنده:مرکزشاپرک", "واریز", "+ 28,000,000", "3,915,631,486"),
    ("1405/06/18 - 4:19:18", " کارمزد پذیرنده شاپرک در پایا050617 تاریخ1405/06/18", "برداشت", "- 105,000", "3,887,631,486"),
    ("1405/06/18 - 4:16:32", "شاپرک تجمیعی . رهگیری :050617 فرستنده:مرکزشاپرک", "واریز", "+ 525,000,000", "3,887,736,486"),
    ("1405/04/01 - 2:45:41", "واریز سود علی الحساب حساب 00201246611000", "واریز", "+ 14,115,893", "3,362,736,486"),
]
IN = 28_000_000 + 525_000_000 + 14_115_893
FEES = 5_600 + 105_000
OUT_OTHER = 5_241_000


def _bump(amount: str, by: int) -> str:
    sign, digits = amount.split(" ", 1)
    return f"{sign} {int(digits.replace(',', '')) + by:,}"


def _xlsx(tag: str = "", by: int = 0) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ردیف", "تاریخ", "توضیحات", "واریز/ برداشت", "مبلغ (ریال)", "موجودی(ریال)", "شرح", None, None,
               "موجودی (ریال)", "445,702,803"])
    for i, (d, desc, kind, amt, bal) in enumerate(ROWS, start=1):
        ws.append([str(i), d, desc + (f" {tag}" if i == len(ROWS) and tag else ""), kind, _bump(amt, by), bal, "", None, None,
                   *(("واریز (ریال)", "+ 5,144,811,663") if i == 1 else (None, None))])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _upload(client, name=None, by=None):
    tag = uuid.uuid4().hex[:4]
    by = int(tag, 16) % 900 + 1 if by is None else by        # its own amounts, so no row is another test's again
    name = name or f"تنخواه پارسیان {tag}.xlsx"
    r = client.post("/transactions/attachments", files={"file": (name, io.BytesIO(_xlsx(tag, by)),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _account(client, name, parent):
    r = client.post("/accounts", json={"name": name, "parent_code": parent})
    assert r.status_code == 201, r.text
    return r.json()["code"]


@pytest.fixture()
def school(auth_client):
    """A school's chart: its bank (بانک دی), tuition income, school expenses, bank fees."""
    codes = {a["code"] for a in auth_client.get("/accounts").json()}
    bank_parent = next(c for c in ("1110", "1111", "1200") if c in codes)
    return {
        "bank": _account(auth_client, "بانک دی حساب 0105222028000", bank_parent),
        "tuition": _account(auth_client, "درآمد شهریه", next(c for c in ("4110", "4100", "4000") if c in codes)),
        "spend": _account(auth_client, "مخارج مدرسه", next(c for c in ("6190", "6112", "6100", "6000") if c in codes)),
        "fees": _account(auth_client, "کارمزد های بانکی طی ماه", next(c for c in ("6190", "6112", "6100", "6000") if c in codes)),
    }


class _Model:
    """A scripted model that reads the imported statement's id from its context and posts it by the rules."""
    shape = "fake"

    def __init__(self, school, **overrides):
        self.school, self.overrides, self.sent, self.calls = school, overrides, [], 0

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        self.sent.append(list(messages))
        self.calls += 1
        if self.calls > 1:
            return LLMResponse(message=ChatMessage(role="assistant", text="کارت آماده است."), stop_reason="end_turn", usage=LLMUsage())
        said = " ".join(m.text or "" for m in messages if m.role == "user")
        sid = re.search(r"statement_id=([0-9a-f-]{36})", said).group(1)
        args = {"statement_id": sid, "bank_account_code": self.school["bank"],
                "money_in_account_code": self.school["tuition"], "money_out_account_code": self.school["spend"],
                "rules": [{"contains": "کارمزد", "account_code": self.school["fees"], "direction": "out"}], "per": "day",
                **self.overrides}
        return LLMResponse(message=ChatMessage(role="assistant", text=None, tool_calls=[
            ToolCall(id=f"c_{uuid.uuid4().hex[:4]}", name="propose_post_statement", input=args)]),
            stop_reason="tool_use", usage=LLMUsage())


def _with_model(monkeypatch, model):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant import orchestrator
    monkeypatch.setattr(api_mod, "run_chat_turn", partial(orchestrator.run_chat_turn, client=model))


def _chat(client, message, att):
    r = client.post("/ai-accountant/chat", json={"message": message, "attachment_ids": [att]})
    assert r.status_code == 200, r.text
    return r.json()


def test_af1_the_banks_export_is_a_statement_not_a_journal(auth_client, monkeypatch):
    from app.api import ai_accountant as api_mod

    async def boom(*a, **k):
        raise AssertionError("no words, no rules: the import answers without the model")
    monkeypatch.setattr(api_mod, "run_chat_turn", boom)
    body = _chat(auth_client, "", _upload(auth_client))
    intake = body["intake"]
    assert intake["kind"] == "bank_statement" and intake["status"] == "imported" and intake["total_rows"] == 6, intake
    assert "transaction list" not in body["text"] and body["stop_reason"] == "intake"


def test_af2_af3_posted_by_his_rules_a_voucher_a_day(auth_client, db, school, monkeypatch):
    model = _Model(school)
    _with_model(monkeypatch, model)
    body = _chat(auth_client, RULES, _upload(auth_client, by=0))
    context = " ".join(m.text or "" for m in model.sent[0] if m.role == "user")
    assert "imported as a bank statement" in context and "6 rows" in context
    assert body["intake"]["kind"] == "bank_statement"                       # the statement card stays on screen
    card = body["proposals"][0]
    assert card["tool_name"] == "propose_post_statement"
    preview = card["preview"]
    assert preview["vouchers"] == 4 and len(preview["row_ids"]) == 6        # four days, six rows
    by = {(ln["account_code"], "Dr" if ln["debit"] else "Cr"): ln["debit"] or ln["credit"] for ln in preview["lines"]}
    assert by == {(school["bank"], "Dr"): IN, (school["tuition"], "Cr"): IN, (school["fees"], "Dr"): FEES,
                  (school["spend"], "Dr"): OUT_OTHER, (school["bank"], "Cr"): FEES + OUT_OTHER}
    assert card["card"]["title"].startswith("صورتحساب") and len(card["card"]["lines"]) == 5

    r = auth_client.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]})
    assert r.status_code == 200, r.text
    stmt_id = uuid.UUID(preview["statement_id"])
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt_id)).scalars().all()
    assert all(r.recon_status == "matched" and r.created_transaction_id for r in rows)
    txns = sorted({db.get(Transaction, r.created_transaction_id) for r in rows}, key=lambda t: t.date)
    assert [t.date.isoformat() for t in txns] == ["2026-06-22", "2026-09-09", "2026-09-15", "2026-09-22"]   # oldest first
    for t in txns:
        assert sum(ln.debit for ln in t.lines) == sum(ln.credit for ln in t.lines)
        assert all(ln.line_description for ln in t.lines)                   # each row keeps its own words
    day = next(t for t in txns if t.date.isoformat() == "2026-09-15")      # 24 Shahrivar: a fee and a deposit
    assert len(day.lines) == 4


def test_af5_what_is_left_out_and_why(auth_client, db, school, monkeypatch):
    model = _Model(school, money_out_account_code=None, rules=[])           # only the «+» side has a rule
    _with_model(monkeypatch, model)
    att = _upload(auth_client)
    first = _chat(auth_client, "", att)                                     # imported, nothing posted
    stmt_id = uuid.UUID(first["intake"]["statement_id"])
    row = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt_id,
                                                    BankStatementRow.credit > 0)).scalars().first()
    posted = auth_client.post("/transactions", json={"date": row.tx_date.isoformat(), "description": "posted before",
                                                      "lines": [{"account_code": school["bank"], "debit": row.credit, "credit": 0},
                                                                {"account_code": school["tuition"], "debit": 0, "credit": row.credit}]})
    assert posted.status_code == 201, posted.text
    row.created_transaction_id = uuid.UUID(posted.json()["id"])             # one deposit already in the books
    row.recon_status = "matched"
    db.commit()
    body = _chat(auth_client, RULES, att)                                   # the same file: the earlier import
    preview = body["proposals"][0]["preview"]
    assert preview["left"] == {"in_the_books": 1, "closed_period": 0, "no_rule": 3}
    assert len(preview["row_ids"]) == 2


def test_af6_undo_takes_every_voucher_back(auth_client, db, school, monkeypatch):
    _with_model(monkeypatch, _Model(school))
    card = _chat(auth_client, RULES, _upload(auth_client))["proposals"][0]
    done = auth_client.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]}).json()
    r = auth_client.post("/ai-accountant/undo", json={"audit_log_id": done["audit_log_id"]})
    assert r.status_code == 200, r.text
    stmt_id = uuid.UUID(card["preview"]["statement_id"])
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt_id)).scalars().all()
    assert all(r.recon_status == "unmatched" and r.created_transaction_id is None for r in rows)
    refs = db.execute(select(Transaction).where(Transaction.reference.like(f"BS-{str(stmt_id)[:6]}-%"))
                      .execution_options(include_deleted=True)).scalars().all()
    assert len(refs) == 4 and all(t.deleted_at is not None for t in refs)


def test_af4_an_account_not_in_the_chart_is_refused_with_a_way_out(db):
    import asyncio
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.statement_posting import ProposePostStatement
    stmt = BankStatement(bank_name="Parsian", source_type="excel", status="reviewing", source_filename="p.xlsx")
    db.add(stmt)
    db.flush()
    tool = ProposePostStatement()
    ctx = ToolContext(db=db, user_id="u1", username="u", chat_session_id=None, user_message="")
    args = tool.InputSchema(statement_id=str(stmt.id), bank_account_code="1110", money_in_account_code="999999")
    with pytest.raises(ToolError) as e:
        asyncio.run(tool.run(ctx, args))
    assert "search_accounts" in str(e.value) or "ask_with_choices" in str(e.value)


def test_af7_without_the_assistant_the_statement_is_still_in(auth_client, db, monkeypatch):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.anthropic_client import AIAccountantError

    async def down(*a, **k):
        from app.services.ai_accountant.orchestrator import _get_or_create_session
        from app.models.ai_accountant import AIChatMessage
        s = _get_or_create_session(db, user_id=k["user_id"], session_id=k.get("session_id"))
        db.add(AIChatMessage(session_id=s.id, role="user", content={"role": "user", "text": k["user_message"]}))
        db.commit()
        raise AIAccountantError("provider unreachable")
    monkeypatch.setattr(api_mod, "run_chat_turn", down)
    body = _chat(auth_client, RULES, _upload(auth_client))
    assert body["intake"]["kind"] == "bank_statement" and body["intake"]["status"] == "imported"
    assert "دستیار الان در دسترس نیست" in body["text"]
    msgs = auth_client.get(f"/ai-accountant/sessions/{body['session_id']}/messages").json()
    assert [m["role"] for m in msgs].count("user") == 1                     # his words once, not twice


def test_af8_a_journal_is_said_in_the_readers_language(db, tmp_path):
    from app.models.transaction import TransactionAttachment
    from app.services.ai_accountant.file_intake import build_spreadsheet_intake
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح", "بدهکار", "بستانکار"])
    ws.append([1, "1405/06/01", "11", "دارایی جاری", "1110", "بانک", "واریز", 1000000, 0])
    ws.append([1, "1405/06/01", "41", "درآمد", "4110", "فروش", "واریز", 0, 1000000])
    p = tmp_path / "journal.xlsx"
    wb.save(p)
    att = TransactionAttachment(file_name="journal.xlsx", file_path=str(p),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    db.add(att)
    db.flush()
    intake = build_spreadsheet_intake(db, [att], can_migrate=True, lang="fa")
    assert intake is not None and intake.kind == "journals", intake and intake.kind
    assert intake.detected.startswith("به نظر فهرست سندهاست") and "transaction list" not in intake.detected


def test_a_fee_rule_picks_the_banks_fees_not_payments_carrying_one():
    """«تراکنش به همراه کارمزد …» is a 2,000,000,000 payment with a fee of its own on another row: a «کارمزد» rule
    took all of them, 11.5 billion of «fees» on the school's statement (real model, 2026-10-18)."""
    from app.services.ai_accountant.statement_posting import is_fee
    assert is_fee(" کارمزد پذیرنده شاپرک در پایا050623061100000438269121974160")
    assert is_fee("کارمزد پایا به شماره پیگیری 14050617054200620677")
    assert is_fee("برداشت کارمزد دستور پرداخت ساتنا 0504170541000892 به بانک ملت")
    assert not is_fee("تراکنش به همراه کارمزد با مشخصات :  رهگیری: 14050617054200620677 بابت بازپرداخت شهریه")
    assert not is_fee("شاپرک تجمیعی . رهگیری :050623061100000438269121974160")


def test_af9_it_really_is_parsians_a_new_bank_made_on_confirm(auth_client, db, school, monkeypatch):
    """«این حساب پارسیان است، نه بانک دی»: the books have no Parsian account; the card makes one on confirm."""
    from app.models.entity import Entity
    from app.services.statement_import import bank_account_for_statement
    model = _Model(school, bank_account_code=None, new_bank_name="بانک پارسیان")
    _with_model(monkeypatch, model)
    body = _chat(auth_client, RULES, _upload(auth_client))
    context = " ".join(m.text or "" for m in model.sent[0] if m.role == "user")
    assert "NO account for Parsian" in context and "new_bank_name" in context
    card = body["proposals"][0]
    assert card["preview"]["new_bank_name"] == "بانک پارسیان"
    assert any("بانک پارسیان" in ln["name"] for ln in card["card"]["lines"])          # its line on the card, by its name
    assert db.execute(select(Entity).where(Entity.name == "بانک پارسیان")).scalars().first() is None   # nothing made yet
    r = auth_client.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]})
    assert r.status_code == 200, r.text
    db.expire_all()
    bank = db.execute(select(Entity).where(Entity.type == "bank", Entity.name == "بانک پارسیان")).scalars().one()
    stmt = db.get(BankStatement, uuid.UUID(card["preview"]["statement_id"]))
    assert bank.code and stmt.bank_account_code == bank.code
    assert card["preview"]["account_code"] == bank.code                               # the card said the code it got
    made = db.execute(select(Account).where(Account.code == bank.code)).scalars().one()
    dey = db.execute(select(Account).where(Account.code == school["bank"])).scalars().one()
    assert made.parent_id == dey.parent_id and len(made.code) == len(dey.code)        # beside بانک دی (111002), not 1201
    assert made.level == dey.level
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)).scalars().all()
    txn = db.get(Transaction, rows[0].created_transaction_id)
    assert bank.code in {ln.account.code for ln in txn.lines}                          # the vouchers post to it
    # the next Parsian statement finds it, nothing asked
    nxt = _chat(auth_client, "", _upload(auth_client))
    assert bank_account_for_statement(db, db.get(BankStatement, uuid.UUID(nxt["intake"]["statement_id"]))) == bank.code


def test_af10_a_bank_in_the_books_is_used_never_made_twice(auth_client, db, school, monkeypatch):
    """The books have a Parsian bank (this one, or one an earlier test made): it is used, none is made beside it."""
    from app.models.entity import Entity

    def parsian():
        db.expire_all()
        return {e.code for e in db.execute(select(Entity).where(Entity.type == "bank",
                                                               Entity.name.like("%پارسیان%"))).scalars()}
    if not parsian():
        made = auth_client.post("/entities", json={"type": "bank", "name": "بانک پارسیان شعبه مرکزی"})
        assert made.status_code in (200, 201), made.text
    before = parsian()
    model = _Model(school, bank_account_code=None, new_bank_name="بانک پارسیان")
    _with_model(monkeypatch, model)
    body = _chat(auth_client, RULES, _upload(auth_client))
    context = " ".join(m.text or "" for m in model.sent[0] if m.role == "user")
    assert "The books' own account for this bank" in context
    card = body["proposals"][0]
    assert "new_bank_name" not in card["preview"] and card["preview"]["bank_account_code"] in before
    r = auth_client.post("/ai-accountant/execute", json={"confirmation_token": card["confirmation_token"]})
    assert r.status_code == 200, r.text
    assert parsian() == before                                                      # no second Parsian
