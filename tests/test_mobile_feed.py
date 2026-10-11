"""Notifications without a push provider (scenario N35; roadmap
ROADMAP_ANDROID_CHAT P0.8, P1.6): the feed the phone's background sync reads."""
from __future__ import annotations

from app.services.ai_accountant import guardrails
from app.db.tenant import use_company
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import _draft, phone, scripted  # noqa: F401  (fixtures)
from tests.test_mobile_edit_approvals import _phone_for

API = "/api/mobile/v1"


def _insights(monkeypatch, *pairs):
    from app.services import insight_service
    from app.services.insight_service import Insight
    made = [Insight(key=k, kind="payroll_change", severity=sev, page="payroll",
                    params={"direction_en": "rose", "direction_fa": "بالا رفت", "direction_es": "subió", "direction_ar": "ارتفعت",
                            "pct": 12, "month": "Mehr", "current": "1", "previous": "2", "joiners_en": "", "leavers_en": "",
                            "joiners_fa": "", "leavers_fa": "", "joiners_es": "", "leavers_es": "", "joiners_ar": "",
                            "leavers_ar": ""})
            for k, sev in pairs]
    monkeypatch.setattr(insight_service, "compute_insights", lambda db, **kw: made)


def test_n35_the_approver_is_told_what_waits_and_the_books_what_matters(client, db, co, phone, scripted, monkeypatch):
    _insights(monkeypatch, ("payroll-change-1405-07", "warning"), ("quiet-info", "info"))
    with use_company(co["cid"]):
        guardrails.save_settings(db, approval_threshold=1_000_000)
        db.commit()
    token = _draft(client, phone, scripted)["blocks"][0]["token"]
    client.post(f"{API}/proposals/{token}/confirm", headers=phone)          # it waits for a second person
    mine = client.get(f"{API}/feed", headers=phone).json()["items"]
    assert [i["key"] for i in mine] == ["insight:payroll-change-1405-07"]     # not my own approval; no info items
    cfo = _phone_for(client, co["cfo"])
    client.put(f"{API}/me/language", headers=cfo, json={"language": "fa"})
    items = client.get(f"{API}/feed", headers=cfo).json()["items"]
    assert [i["key"] for i in items] == [f"approval:{token}", "insight:payroll-change-1405-07"]
    assert items[0]["title"] == "سندی منتظر تأیید شماست" and "۸۰٬۰۰۰٬۰۰۰" in items[0]["body"]
    # the keys stay the same, so the phone tells each thing once
    assert client.get(f"{API}/feed", headers=cfo).json()["items"] == items


def test_n35_without_approval_rights_only_the_books_warnings(client, db, co, monkeypatch):
    _insights(monkeypatch, ("payroll-change-1405-07", "high"))
    accountant = _phone_for(client, co["accountant"])
    assert [i["key"] for i in client.get(f"{API}/feed", headers=accountant).json()["items"]] == ["insight:payroll-change-1405-07"]
