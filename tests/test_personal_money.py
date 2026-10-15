"""A person's money in the chat (scenarios Z1–Z5).

From the owner's phone: «۸۶ ریال و ۱۰۰ بیسه توی کیف پول سبزم دارم» in personal
books that counted in Iranian rials became «۸۶٫۱ ریال», then two questions about
which account to set it against, each answered "not found in the chart". A
person now chooses their books' currency, the assistant is told it and the
words for money, money already held is a starting balance, and a quick
answer's card speaks the question's language."""
from __future__ import annotations

import uuid
from functools import partial

import pytest

from tests.test_ai_guardrails import _calls, _FakeClient, _text

STRONG = "Str0ng-personal-pass!"
API = "/api/mobile/v1"


@pytest.fixture()
def person(client, db):
    """Personal books in Iranian rials, as every person's started: the web session and a phone."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.services.company_service import provision_company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company
    company, user = provision_company(db, name="masein", locale="default", base_currency="IRR",
                                      username=f"masein-{uuid.uuid4().hex[:6]}", password=STRONG, kind="personal")
    user.preferred_language = "en"
    db.commit()
    cid, uid, uname = str(company.id), str(user.id), user.username
    db.expunge_all()

    def web():
        csrf = generate_csrf_token()
        client.cookies.clear()
        client.cookies.set(settings.auth_cookie_name, create_session_token(
            user_id=uid, username=uname, is_admin=False, role="personal", company_id=cid))
        client.cookies.set(CSRF_COOKIE, csrf)
        return _CSRFTestClient(client, csrf)

    def phone():
        client.cookies.clear()
        r = client.post(f"{API}/auth/login", json={"username": uname, "password": STRONG, "device_name": "Galaxy"})
        assert r.status_code == 200, r.text
        client.cookies.clear()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    yield {"cid": cid, "web": web, "phone": phone, "client": client}
    client.cookies.clear()
    _purge_company(db, cid)


@pytest.fixture()
def scripted(monkeypatch):
    """The real orchestrator and tools with a scripted model; ``prompts`` keeps each system prompt sent."""
    from app.api import ai_accountant as api_mod
    real = api_mod.run_chat_turn
    prompts: list[str] = []

    class Capturing(_FakeClient):
        async def chat(self, *, system_prompt, **kw):
            prompts.append(system_prompt)
            return await super().chat(system_prompt=system_prompt, **kw)

    def script(*responses):
        monkeypatch.setattr(api_mod, "run_chat_turn", partial(real, client=Capturing(list(responses))))
    script.prompts = prompts
    return script


# --- Z3: choosing the currency -------------------------------------------------------------------------------

def test_z3_a_person_counts_their_books_in_omani_rials(person, db):
    api = person["web"]()
    now = api.get("/personal/currency").json()
    assert now["currency"] == "IRR" and now["name"] == "Iranian rial"
    assert {"code": "OMR", "name": "Omani rial"} in now["options"]
    r = api.put("/personal/currency", json={"currency": "omr"})
    assert r.status_code == 200, r.text
    assert (r.json()["currency"], r.json()["name"], r.json()["changed"]) == ("OMR", "Omani rial", True)
    # the books say so everywhere: the company, and the phone's own view of it
    me = person["client"].get(f"{API}/me", headers=person["phone"]()).json()
    assert me["company"]["base_currency"] == "OMR"
    # the phone chooses too, in the account's language
    phone = person["phone"]()
    r = person["client"].put(f"{API}/books/currency", headers=phone, json={"currency": "AED"})
    assert r.status_code == 200 and r.json()["currency"] == "AED", r.text
    assert person["client"].put(f"{API}/books/currency", headers=phone, json={"currency": "XYZ"}).json()["detail"].startswith(
        "Choose one of the listed currencies: IRR, OMR")
    from app.models.audit_log import AuditLog
    from app.db.tenant import use_company
    from sqlalchemy import select
    with use_company(person["cid"]):
        details = [a.detail for a in db.execute(select(AuditLog).where(AuditLog.entity_type == "company")).scalars()]
    assert "the books' currency IRR → OMR" in details and "the books' currency OMR → AED" in details


def test_z3_a_companys_currency_stays_with_the_platform_admin(client, db):
    from tests.test_ai_guardrails import _login, co  # noqa: F401
    from app.services.company_service import provision_company
    from tests.test_admin_audit import _purge_company
    company, owner = provision_company(db, name="Arman", locale="ir", base_currency="IRR",
                                       username=f"arman-{uuid.uuid4().hex[:6]}", password=STRONG)
    db.commit()
    cid = str(company.id)
    try:
        api = _login(client, cid, owner)
        r = api.put("/personal/currency", json={"currency": "OMR"})
        assert r.status_code == 422 and r.json()["detail"] == "Only a person's own books choose their currency here."
        assert api.get("/personal/currency").status_code == 422
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


# --- Z1, Z2: what the assistant is told, and the card it proposes ---------------------------------------------

def test_z1_the_assistant_knows_the_books_currency_and_money_already_held(person, scripted):
    api = person["web"]()
    api.put("/personal/currency", json={"currency": "OMR"})
    wallet = ("propose_create_transaction", {
        "date": "2026-10-15", "description": "کیف پول سبز — موجودی اولیه", "currency": "OMR",
        "lines": [{"account_code": "1120", "debit": "86.100", "credit": 0},
                  {"account_code": "3110", "debit": 0, "credit": "86.100"}]})
    scripted(_calls(wallet), _text("۸۶٫۱۰۰ ریال عمان را موجودی اولیهٔ کیف پول سبزتان ثبت می‌کنم."))
    phone = person["phone"]()
    r = person["client"].post(f"{API}/chat", headers=phone,
                              json={"message": "من هشتاد و شش ریال و صد بیسه توی کیف پول سبزم دارم"})
    assert r.status_code == 200, r.text
    prompt = scripted.prompts[0]
    assert "These books count in OMR (Omani rial; amounts have 3 decimal places)." in prompt
    assert "STARTING BALANCE" in prompt and "Opening net worth (3110" in prompt and "86.100 OMR" in prompt
    card = next(b for b in r.json()["blocks"] if b["type"] == "proposal")
    assert card["amount"] == {"value": 86.1, "currency": "OMR"}          # the phone writes it 86.100 (OMR's 3 places)
    assert [(ln["account"], ln["debit"], ln["credit"]) for ln in card["lines"]] == [("1120", 86.1, 0), ("3110", 0, 86.1)]


def test_z2_money_in_another_currency_is_named_not_guessed(person, scripted):
    """In books still counting in Iranian rials the model is told so, and to propose nothing for baisa."""
    scripted(_text("Your books count in Iranian rials; baisa is Omani money."))
    phone = person["phone"]()
    person["client"].post(f"{API}/chat", headers=phone, json={"message": "I have 86 rials and 100 baisa in my green wallet"})
    [prompt] = scripted.prompts
    assert "These books count in IRR (Iranian rial; amounts have 0 decimal places)." in prompt
    assert "EVERYDAY MONEY IN ANOTHER CURRENCY" in prompt and "«ارز دفاتر»" in prompt


# --- Z4: the words for opening net worth ----------------------------------------------------------------------

@pytest.mark.parametrize("words", ["افتتاحیه", "موجودی اولیه", "خالص دارایی", "opening balance"])
def test_z4_opening_net_worth_is_found_by_its_words(person, db, words):
    import asyncio
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    with use_company(person["cid"]):
        out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                               SearchAccountsInput(query=words)))
    assert out["matches"][0]["code"] == "3110", (words, out["matches"][:3])


# --- Z5: a quick answer's card in the question's language ----------------------------------------------------

def test_z5_a_persian_question_on_an_english_phone_gets_a_persian_card(person):
    phone = person["phone"]()
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": "چقدر پول داریم الان ما تو حسابامون"})
    assert r.status_code == 200, r.text
    figure = next(b for b in r.json()["blocks"] if b["type"] == "figure")
    assert figure["label"] == "موجودی نقد و بانک", figure
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": "How much cash do we have?"})
    figure = next(b for b in r.json()["blocks"] if b["type"] == "figure")
    assert figure["label"] == "Cash and bank"
