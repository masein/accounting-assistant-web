"""Correction memory (roadmap 2026-09 §5.4): when the user overrules a
suggested account or party — on a statement row, by editing an entry, or by
telling the assistant — the choice is kept for wording like that, and the
statement categoriser, search_accounts, find_entity and the assistant's
prompt use it before anything else."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.company import Company
from app.models.learned_preference import LearnedPreference
from app.services import learned_preferences as lp


def _login(client, cid, role="owner"):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role,
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _company(db, name="Sterling Ltd"):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    c = Company(id=uuid.uuid4(), name=name, slug=f"gb-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    return cid


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    cid = _company(db)
    db.expunge_all()
    yield _login(client, cid), cid
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


def _in(cid):
    from app.db.tenant import use_company
    return use_company(cid)


# ─── 1. Remembering and matching ───────────────────────────────────────────

def test_wording_is_normalised_and_the_latest_choice_wins(uk, db):
    _api, cid = uk
    with _in(cid):
        a = lp.remember(db, "POS SNAPP TEHRAN 1405/07/01 #88213", account_code="7400", source="statement")
        assert a.pattern == "snapp tehran" and a.times_chosen == 1          # card noise and numbers dropped
        b = lp.remember(db, "pos snapp tehran 99812", account_code="7500", source="edit")
        assert b.id == a.id and b.account_code == "7500" and b.times_chosen == 2 and b.source == "edit"
        assert lp.remember(db, "1405/07/01 88213", account_code="7400") is None        # nothing to learn from
        assert lp.remember(db, "Snapp", account_code=None, entity_id=None) is None


def test_lookup_exact_then_overlapping_then_contained(uk, db):
    _api, cid = uk
    with _in(cid):
        lp.remember(db, "Tesco Stores", account_code="7600")
        lp.remember(db, "snapp", account_code="7400")
        exact = lp.lookup(db, "TESCO STORES 2231")
        assert exact.preference.account_code == "7600" and exact.score == 1.0
        contained = lp.lookup(db, "POS SNAPP TEHRAN")
        assert contained.preference.account_code == "7400" and contained.score == 0.9
        assert lp.lookup(db, "Amazon marketplace") is None
        assert lp.lookup(db, "Tesco Stores", want="entity") is None               # no party learned for it


# ─── 2. Learning from the user's corrections ───────────────────────────────────

def _statement(db, cid, description, debit=0, credit=0, suggested=None):
    with _in(cid):
        stmt = BankStatement(bank_name="Test Bank", source_type="csv", source_filename="t.csv", currency="GBP",
                             from_date=date(2026, 9, 1), to_date=date(2026, 9, 30), status="parsed", total_rows=1)
        db.add(stmt)
        db.flush()
        row = BankStatementRow(statement_id=stmt.id, row_index=1, tx_date=date(2026, 9, 5), description=description,
                               debit=debit, credit=credit, suggested_account_code=suggested)
        db.add(row)
        db.commit()
        return stmt.id, row.id


def test_a_statement_row_posted_to_another_account_is_remembered(uk, db):
    api, cid = uk
    sid, rid = _statement(db, cid, "CARD PAYMENT TO NETFLIX.COM 4411", debit=12, suggested="7600")
    r = api.post(f"/brain/bank-statements/{sid}/approve",
                 json={"approvals": [{"row_id": str(rid), "action": "create", "account_code": "7500"}]})
    assert r.status_code == 200 and r.json()["created"] == 1, r.text
    from app.services.statement_categorizer import suggest_for_row
    with _in(cid):
        pref = db.execute(select(LearnedPreference)).scalars().one()
        assert pref.account_code == "7500" and pref.source == "statement"
        s = suggest_for_row(db, "CARD PAYMENT TO NETFLIX.COM 5520", is_debit=True)
        assert s.account_code == "7500" and s.source == "learned"


def test_accepting_the_suggestion_teaches_nothing(uk, db):
    api, cid = uk
    sid, rid = _statement(db, cid, "CARD PAYMENT TO NETFLIX.COM", debit=12, suggested="7600")
    api.post(f"/brain/bank-statements/{sid}/approve",
             json={"approvals": [{"row_id": str(rid), "action": "create", "account_code": "7600"}]})
    with _in(cid):
        assert db.execute(select(LearnedPreference)).scalars().all() == []


def _post(api, description, lines, entity_links=None):
    r = api.post("/transactions", json={"date": "2026-09-05", "description": description,
                                        "lines": [{"account_code": c, "debit": d, "credit": k} for c, d, k in lines],
                                        "entity_links": entity_links or []})
    assert r.status_code == 201, r.text
    return r.json()


def test_moving_an_entry_to_another_account_is_remembered(uk, db):
    api, cid = uk
    t = _post(api, "Uber to the airport", [("7600", 40, 0), ("1200", 0, 40)])
    r = api.patch(f"/transactions/{t['id']}", json={"lines": [
        {"account_code": "7400", "debit": 40, "credit": 0}, {"account_code": "1200", "debit": 0, "credit": 40}]})
    assert r.status_code == 200, r.text
    with _in(cid):
        pref = db.execute(select(LearnedPreference)).scalars().one()
        assert (pref.pattern, pref.account_code, pref.source) == ("uber the airport", "7400", "edit")


def test_restructuring_an_entry_is_not_a_recategorisation(uk, db):
    api, cid = uk
    t = _post(api, "Office party", [("7600", 100, 0), ("1200", 0, 100)])
    api.patch(f"/transactions/{t['id']}", json={"lines": [
        {"account_code": "7500", "debit": 60, "credit": 0}, {"account_code": "7600", "debit": 40, "credit": 0},
        {"account_code": "1200", "debit": 0, "credit": 100}]})
    with _in(cid):
        assert db.execute(select(LearnedPreference)).scalars().all() == []


def test_changing_the_party_is_remembered_and_find_entity_prefers_it(uk, db):
    from app.models.entity import Entity
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import FindEntity, FindEntityInput
    api, cid = uk
    with _in(cid):
        right = Entity(type="supplier", name="Kim Design Studio")
        wrong = Entity(type="supplier", name="Kim Nguyen")
        db.add_all([right, wrong])
        db.commit()
        right_id, wrong_id = str(right.id), str(wrong.id)
    t = _post(api, "Logo refresh invoice", [("7800", 500, 0), ("1200", 0, 500)],
              entity_links=[{"role": "supplier", "entity_id": wrong_id}])
    r = api.patch(f"/transactions/{t['id']}", json={"entity_links": [{"role": "supplier", "entity_id": right_id}]})
    assert r.status_code == 200, r.text
    with _in(cid):
        out = asyncio.run(FindEntity().run(ToolContext(db=db, user_id="u"),
                                           FindEntityInput(query="Kim", description="Logo refresh invoice #2")))
    assert out["matches"][0]["entity_id"] == right_id and out["matches"][0]["learned"] is True
    assert "Kim Design Studio" in out["learned_note"]


def test_the_journal_editor_teaches_too(uk, db):
    api, cid = uk
    t = _post(api, "Adobe subscription", [("7600", 20, 0), ("1200", 0, 20)])
    r = api.patch(f"/manager-reports/journal/{t['id']}", json={"lines": [
        {"account_code": "7800", "debit": 20, "credit": 0}, {"account_code": "1200", "debit": 0, "credit": 20}]})
    assert r.status_code == 200, r.text
    with _in(cid):
        assert lp.lookup(db, "ADOBE SUBSCRIPTION").preference.account_code == "7800"


# ─── 3. Using what was learned ─────────────────────────────────────────────────

def test_learned_beats_history_and_an_inactive_account_is_skipped(uk, db):
    from app.models.account import Account
    from app.services.statement_categorizer import suggest_for_row
    api, cid = uk
    _post(api, "Pret a Manger", [("7500", 8, 0), ("1200", 0, 8)])            # history says 7500
    with _in(cid):
        assert suggest_for_row(db, "PRET A MANGER", is_debit=True).source == "history"
        lp.remember(db, "Pret a Manger", account_code="7600")
        db.commit()
        s = suggest_for_row(db, "PRET A MANGER", is_debit=True)
        assert (s.account_code, s.source, s.confidence) == ("7600", "learned", 0.99)
        assert db.execute(select(LearnedPreference)).scalars().one().times_used == 1
        acc = db.execute(select(Account).where(Account.code == "7600")).scalar_one()
        acc.is_active = False
        db.commit()
        assert suggest_for_row(db, "PRET A MANGER", is_debit=True).account_code == "7500"
        acc.is_active = True
        db.commit()


def test_search_accounts_puts_the_learned_account_first(uk, db):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    _api, cid = uk
    with _in(cid):
        lp.remember(db, "Snapp ride", account_code="7400")
        db.commit()
        out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u"),
                                               SearchAccountsInput(query="travel", description="POS SNAPP RIDE 88")))
        assert out["matches"][0]["code"] == "7400" and out["matches"][0]["learned"] is True
        assert "7400" in out["learned_note"]
        plain = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u"), SearchAccountsInput(query="rent")))
        assert "learned_note" not in plain and not any(m.get("learned") for m in plain["matches"])


def test_the_prompt_lists_the_companys_choices(uk, db):
    _api, cid = uk
    with _in(cid):
        assert lp.prompt_block(db) == ""
        lp.remember(db, "Snapp ride", account_code="7400")
        block = lp.prompt_block(db)
    assert '"Snapp ride" → account 7400 (Motor expenses)' in block and "learned from the user's corrections" in block


# ─── 4. Telling the assistant ────────────────────────────────────────────────────

def test_the_assistant_proposes_and_saves_a_preference(uk, db):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.execute_service import execute_proposal
    from app.services.ai_accountant.memory_tools import ProposeRememberPreference, ProposeRememberPreferenceInput
    _api, cid = uk
    tool = ProposeRememberPreference()
    with _in(cid):
        ctx = ToolContext(db=db, user_id="u1", user_message="always put Snapp under motor")
        card = asyncio.run(tool.run(ctx, ProposeRememberPreferenceInput(description="Snapp", account_code="7400")))
        assert card["status"] == "pending" and "7400 Motor expenses" in card["summary"]
        assert db.execute(select(LearnedPreference)).scalars().all() == []     # nothing until confirmed
        res = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1")
        assert res.transaction_id is None and res.audit_log_id
        pref = db.execute(select(LearnedPreference)).scalars().one()
        assert (pref.account_code, pref.source) == ("7400", "chat")
        for bad in (dict(description="Snapp", account_code="7"), dict(description="Snapp"),
                    dict(description="12 34", account_code="7400")):
            with pytest.raises(ToolError):
                asyncio.run(tool.run(ctx, ProposeRememberPreferenceInput(**bad)))


# ─── 5. The list on the chat page ─────────────────────────────────────────────

def test_preferences_can_be_listed_added_and_forgotten(uk, db):
    api, cid = uk
    r = api.post("/ai-accountant/preferences", json={"description": "Amazon Web Services", "account_code": "7600"})
    assert r.status_code == 201, r.text
    assert r.json()["account_name"] and r.json()["source"] == "manual"
    assert api.post("/ai-accountant/preferences", json={"description": "Amazon", "account_code": "7"}).status_code == 422
    assert api.post("/ai-accountant/preferences", json={"description": "1234"}).status_code == 422
    listed = api.get("/ai-accountant/preferences").json()
    assert [p["label"] for p in listed] == ["Amazon Web Services"]
    assert api.delete(f"/ai-accountant/preferences/{listed[0]['id']}").status_code == 204
    assert api.get("/ai-accountant/preferences").json() == []
    assert api.delete(f"/ai-accountant/preferences/{listed[0]['id']}").status_code == 404


def test_viewers_read_and_other_companies_see_nothing(uk, client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = uk
    api.post("/ai-accountant/preferences", json={"description": "Amazon Web Services", "account_code": "7600"})
    viewer = _login(client, cid, role="viewer")
    assert len(viewer.get("/ai-accountant/preferences").json()) == 1
    assert viewer.post("/ai-accountant/preferences", json={"description": "x y z", "account_code": "7600"}).status_code == 403
    other = _company(db, "Other Ltd")
    try:
        assert _login(client, other).get("/ai-accountant/preferences").json() == []
    finally:
        _purge_company(db, other)
