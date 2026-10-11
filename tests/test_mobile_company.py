"""The books as the phone shows them (scenario N38): sign-in and refresh say
their kind and the calendar their dates are written in."""
from __future__ import annotations

from app.db.tenant import use_company
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def test_n38_sign_in_and_refresh_say_the_books_kind_and_calendar(client, db, co):
    from app.services.locale_service import set_display_calendar
    client.cookies.clear()
    r = client.post(f"{API}/auth/login", json={"username": co["owner"].username, "password": "x" * 12, "device_name": "Pixel"})
    assert r.status_code == 200, r.text
    company = r.json()["company"]
    assert (company["kind"], company["calendar"]) == ("business", "jalali")        # an Iranian company
    # the company writes its dates in the Gregorian calendar now: the next refresh says so, and /me
    with use_company(co["cid"]):
        set_display_calendar(db, "gregorian")
        db.commit()
    client.cookies.clear()
    renewed = client.post(f"{API}/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["company"]["calendar"] == "gregorian"
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {renewed.json()['access_token']}"})
    assert (me.json()["company"]["calendar"], me.json()["company"]["kind"]) == ("gregorian", "business")
