"""Exchange rates (roadmap 2026-09 §4.6): the daily feeds (ECB reference rates
and configurable JSON feeds for the rial and gold), the address guard on feed
URLs, the platform admin's routes, the scheduler job — and rates per company:
a company's own rates replace the shared ones for that pair, and no company
can see, change or delete another company's."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.models.app_setting import PLATFORM_SETTING_KEYS, AppSetting
from app.models.company import Company
from app.models.exchange_rate import ExchangeRate
from app.services import fx_service, rate_feeds

ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-09-25">
      <Cube currency="USD" rate="1.1000"/>
      <Cube currency="GBP" rate="0.8500"/>
      <Cube currency="JPY" rate="160.00"/>
      <Cube currency="TRY" rate="38.00"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""

MARKET_JSON = {
    "data": {"usd": {"value": "۱۰۵٬۰۰۰"}, "gold18": {"price": "9,500,000"}},
    "coins": [{"name": "emami", "sell": 95000000}],
    "note": "prices in toman",
}
FEED_URL = "https://rates.example.com/api/latest?api_key=SECRET123"


@pytest.fixture()
def clean(db):
    """Shared rates and the platform rows are global: leave none behind (a
    stray shared GBP→USD would give other tests a cross rate)."""
    def _wipe():
        db.rollback()
        db.execute(delete(ExchangeRate).where(ExchangeRate.company_id.is_(None)))
        db.execute(delete(AppSetting).where(AppSetting.key.in_([rate_feeds.SETTINGS_KEY, rate_feeds.STATUS_KEY])))
        db.commit()
    _wipe()
    yield db
    _wipe()


def _fake_fetch(monkeypatch, responses: dict[str, bytes | Exception]):
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        out = responses.get(url)
        if out is None:
            raise AssertionError(f"unexpected fetch {url}")
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(rate_feeds, "_fetch", fetch)
    return calls


def _shared(db, fc, tc, on=None):
    q = select(ExchangeRate).where(ExchangeRate.company_id.is_(None), ExchangeRate.from_currency == fc,
                                   ExchangeRate.to_currency == tc)
    if on:
        q = q.where(ExchangeRate.effective_date == on)
    return db.execute(q).scalars().all()


def _feed(items=None, **kw):
    return {"name": "Market", "url": FEED_URL, "enabled": True, "items": items if items is not None else [
        {"unit": "USD", "to": "IRR", "path": "data.usd.value", "multiply": 10},
        {"unit": "GOLDG", "to": "IRR", "path": "data.gold18.price", "multiply": 10},
        {"unit": "GOLDC", "to": "IRR", "path": "coins.0.sell", "multiply": 10},
    ], **kw}


# ─── 1. Parsing ─────────────────────────────────────────────────────────

def test_ecb_file_is_read_with_eur_and_the_aed_peg():
    day, rates = rate_feeds.parse_ecb(ECB_XML)
    assert day == date(2026, 9, 25)
    assert rates["EUR"] == 1 and rates["USD"] == Decimal("1.1000") and rates["GBP"] == Decimal("0.8500")
    assert rates["AED"] == Decimal("1.1000") * Decimal("3.6725")


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"not xml at all", b"<Cube/>"])
def test_a_broken_ecb_file_is_an_error_not_zero_rates(body):
    with pytest.raises(ValueError):
        rate_feeds.parse_ecb(body)


def test_json_paths_walk_keys_and_list_indices():
    assert rate_feeds.json_path(MARKET_JSON, "data.usd.value") == "۱۰۵٬۰۰۰"
    assert rate_feeds.json_path(MARKET_JSON, "coins.0.sell") == 95000000
    for bad in ("data.eur.value", "coins.5.sell", "coins.x", "note.deeper"):
        with pytest.raises(KeyError):
            rate_feeds.json_path(MARKET_JSON, bad)


@pytest.mark.parametrize("raw,expected", [("۱۰۵٬۰۰۰", 105000), ("9,500,000", 9500000), (95000000, 95000000),
                                          ("1.25", Decimal("1.25")), (" ٣٤٥ ", 345)])
def test_numbers_in_persian_digits_and_with_separators(raw, expected):
    assert rate_feeds.to_number(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "n/a", "0", "-5", None])
def test_a_price_must_be_a_positive_number(raw):
    with pytest.raises(ValueError):
        rate_feeds.to_number(raw)


def test_the_preview_lists_every_number_with_its_path():
    found = {n["path"]: n["value"] for n in rate_feeds._numbers(MARKET_JSON)}
    assert found == {"data.usd.value": 105000.0, "data.gold18.price": 9500000.0, "coins.0.sell": 95000000.0}


# ─── 2. The server never fetches inside its own network ──────────────────

@pytest.mark.parametrize("url", [
    "http://rates.example.com/x",              # not https
    "https://127.0.0.1/x", "https://10.1.2.3/x", "https://192.168.1.1/x", "https://172.16.0.9/x",
    "https://169.254.169.254/latest/meta-data", "https://[::1]/x", "https://0.0.0.0/x", "ftp://x.example/",
])
def test_private_and_plain_http_addresses_are_refused(url):
    with pytest.raises(ValueError):
        rate_feeds.check_public_url(url)


def test_a_public_name_passes_and_one_resolving_inside_is_refused(monkeypatch):
    monkeypatch.setattr(rate_feeds, "_resolve", lambda host, port: {"good.example": ["93.184.216.34"],
                                                                    "sneaky.example": ["93.184.216.34", "10.0.0.5"]}[host])
    rate_feeds.check_public_url("https://good.example/rates")
    with pytest.raises(ValueError, match="not a public address"):
        rate_feeds.check_public_url("https://sneaky.example/rates")


def test_fetch_checks_every_redirect_and_caps_the_size(monkeypatch):
    monkeypatch.setattr(rate_feeds, "_resolve",
                        lambda host, port: ["10.0.0.7"] if host == "internal.example" else ["93.184.216.34"])

    def handler(request: httpx.Request):
        if request.url.path == "/ok":
            return httpx.Response(200, content=b'{"a": 1}')
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "/ok"})
        if request.url.path == "/escape":
            return httpx.Response(302, headers={"location": "https://internal.example/admin"})
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if request.url.path == "/huge":
            return httpx.Response(200, content=b"x" * (rate_feeds.MAX_BYTES + 10))
        return httpx.Response(500)
    monkeypatch.setattr(rate_feeds, "_TRANSPORT", httpx.MockTransport(handler))
    assert rate_feeds._fetch("https://feed.example/ok") == b'{"a": 1}'
    assert rate_feeds._fetch("https://feed.example/hop") == b'{"a": 1}'
    with pytest.raises(ValueError, match="not a public address"):
        rate_feeds._fetch("https://feed.example/escape")
    with pytest.raises(ValueError, match="redirects"):
        rate_feeds._fetch("https://feed.example/loop")
    with pytest.raises(ValueError, match="2 MB"):
        rate_feeds._fetch("https://feed.example/huge")
    with pytest.raises(httpx.HTTPStatusError):
        rate_feeds._fetch("https://feed.example/boom")


# ─── 3. Settings ────────────────────────────────────────────────────────

def test_settings_default_to_off_and_are_platform_rows():
    assert {"rate_feeds", "rate_feeds_status"} <= PLATFORM_SETTING_KEYS
    assert rate_feeds.DEFAULTS["enabled"] is False


def test_feed_urls_are_encrypted_at_rest_and_masked_on_read(clean):
    db = clean
    out = rate_feeds.save_settings(db, {"enabled": True, "ecb": True, "hour": 6, "feeds": [_feed()]})
    db.commit()
    assert out["enabled"] is True and out["hour"] == 6
    assert out["feeds"][0]["url"] == "https://rates.example.com/api/latest?api_key=…"
    raw = db.execute(select(AppSetting.value).where(AppSetting.key == "rate_feeds")).scalar_one()
    assert "SECRET123" not in raw and "rates.example.com" not in raw
    assert rate_feeds.load_settings(db, reveal=True)["feeds"][0]["url"] == FEED_URL
    # Saving back what the screen showed (the masked URL) keeps the real one.
    rate_feeds.save_settings(db, {**out, "feeds": [{**out["feeds"][0], "enabled": False}]})
    again = rate_feeds.load_settings(db, reveal=True)["feeds"][0]
    assert again["url"] == FEED_URL and again["enabled"] is False
    assert [i["unit"] for i in again["items"]] == ["USD", "GOLDG", "GOLDC"]
    assert [i["multiply"] for i in again["items"]] == ["10", "10", "10"]       # plain, not "1E+1"
    rate_feeds.save_settings(db, {**out, "feeds": [_feed(items=[
        {"unit": "USD", "to": "IRT", "path": "a", "multiply": 0.1}, {"unit": "EUR", "to": "IRR", "path": "b", "multiply": 1.0}])]})
    assert [i["multiply"] for i in rate_feeds.load_settings(db)["feeds"][0]["items"]] == ["0.1", "1"]


@pytest.mark.parametrize("url,shown", [
    ("https://v6.example.com/v6/9f8e7d6c5b4a39281706/latest/USD", "https://v6.example.com/v6/…/latest/USD"),
    ("https://api.example.com/latest?key=abc&base=USD", "https://api.example.com/latest?key=…&base=…"),
    ("https://api.example.com/v1/prices/gold-18k", "https://api.example.com/v1/prices/gold-18k"),
    ("", ""),
])
def test_keys_in_the_path_or_the_query_are_masked(url, shown):
    assert rate_feeds._mask(url) == shown


def test_a_renamed_feed_needs_its_url_pasted_again(clean):
    db = clean
    saved = rate_feeds.save_settings(db, {"enabled": True, "ecb": True, "hour": 7, "feeds": [_feed()]})
    with pytest.raises(HTTPException) as exc:
        rate_feeds.save_settings(db, {**saved, "feeds": [{**saved["feeds"][0], "name": "Renamed"}]})
    assert exc.value.status_code == 422 and "paste the full URL" in exc.value.detail
    assert rate_feeds.load_settings(db, reveal=True)["feeds"][0]["url"] == FEED_URL


@pytest.mark.parametrize("change,message", [
    ({"hour": 24}, "hour"),
    ({"feeds": [_feed(url="http://rates.example.com/x")]}, "https"),
    ({"feeds": [_feed(name="  ")]}, "name"),
    ({"feeds": [_feed(items=[{"unit": "usd dollar", "to": "IRR", "path": "a"}])]}, "capitals"),
    ({"feeds": [_feed(items=[{"unit": "USD", "to": "USD", "path": "a"}])]}, "capitals"),
    ({"feeds": [_feed(items=[{"unit": "USD", "to": "IRR", "path": ""}])]}, "JSON path"),
    ({"feeds": [_feed(items=[{"unit": "USD", "to": "IRR", "path": "a", "multiply": 0}])]}, "multiplier"),
    ({"feeds": [_feed(items=[{"unit": "USD", "to": "IRR", "path": "a", "multiply": "lots"}])]}, "multiplier"),
])
def test_bad_settings_are_refused_with_a_reason(clean, change, message):
    with pytest.raises(HTTPException) as exc:
        rate_feeds.save_settings(clean, {"enabled": True, "ecb": True, "hour": 7, "feeds": [], **change})
    assert exc.value.status_code == 422 and message in exc.value.detail


# ─── 4. Fetching and storing ─────────────────────────────────────────────

def test_ecb_stores_crosses_into_every_currency_companies_report_in(clean, monkeypatch):
    db = clean
    _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: ECB_XML})
    out = rate_feeds.run_ecb(db)
    db.commit()
    assert out["date"] == "2026-09-25" and out["added"] > 0 and out["kept"] == 0
    usd_gbp = _shared(db, "USD", "GBP")
    assert len(usd_gbp) == 1 and usd_gbp[0].effective_date == date(2026, 9, 25)     # the ECB's date
    assert usd_gbp[0].rate == pytest.approx(0.85 / 1.10) and usd_gbp[0].note == "feed:ecb"
    assert _shared(db, "EUR", "USD")[0].rate == pytest.approx(1.10)
    assert _shared(db, "AED", "USD")[0].rate == pytest.approx(1 / 3.6725)
    assert _shared(db, "JPY", "GBP")[0].rate == pytest.approx(0.85 / 160)
    assert not _shared(db, "USD", "JPY")          # JPY is a source, not a target nobody reports in
    # A second run the same day updates in place.
    again = rate_feeds.run_ecb(db)
    assert again["added"] == 0 and again["updated"] == out["added"]
    assert len(_shared(db, "USD", "GBP")) == 1


def test_a_rate_the_platform_admin_typed_for_that_day_is_never_overwritten(clean, monkeypatch):
    db = clean
    db.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=0.5, effective_date=date(2026, 9, 25),
                        note="agreed with the bank"))
    db.commit()
    _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: ECB_XML})
    out = rate_feeds.run_ecb(db)
    assert out["kept"] == 1
    assert _shared(db, "USD", "GBP")[0].rate == 0.5


def test_a_json_feed_prices_the_rial_and_gold_with_the_multiplier(clean, monkeypatch):
    db = clean
    _fake_fetch(monkeypatch, {FEED_URL: json.dumps(MARKET_JSON).encode()})
    feed = _feed(items=_feed()["items"] + [{"unit": "EUR", "to": "IRR", "path": "data.eur.value", "multiply": "10"}])
    out = rate_feeds.run_feed(db, {**feed, "items": [{**i, "multiply": str(i.get("multiply", 1))} for i in feed["items"]]},
                              today=date(2026, 9, 28))
    db.commit()
    assert out["added"] == 3 and out["errors"] == ["EUR: nothing at data.eur.value"]
    assert out["values"]["USD→IRR"] == 1_050_000
    assert _shared(db, "GOLDG", "IRR", date(2026, 9, 28))[0].rate == 95_000_000
    assert _shared(db, "GOLDC", "IRR")[0].rate == 950_000_000 and _shared(db, "GOLDC", "IRR")[0].note == "feed:Market"


def test_one_failing_source_never_stops_the_others_and_the_run_is_recorded(clean, monkeypatch):
    db = clean
    rate_feeds.save_settings(db, {"enabled": True, "ecb": True, "hour": 7, "feeds": [_feed()]})
    _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: httpx.ConnectError("ECB down"),
                              FEED_URL: json.dumps(MARKET_JSON).encode()})
    status = rate_feeds.run_all(db, today=date(2026, 9, 28))
    db.commit()
    by = {r["source"]: r for r in status["results"]}
    assert "ECB down" in by["ECB"]["error"] and by["Market"]["added"] == 3
    saved = rate_feeds.load_status(db)
    assert saved["date"] == "2026-09-28" and saved["attempts"] == 1 and len(saved["results"]) == 2


def test_switched_off_nothing_runs_unless_the_admin_asks(clean, monkeypatch):
    db = clean
    calls = _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: ECB_XML})
    assert rate_feeds.run_all(db)["skipped"] == "switched off" and calls == []
    status = rate_feeds.run_all(db, force=True)
    assert calls == [rate_feeds.ECB_URL] and status["results"][0]["source"] == "ecb"


def test_due_once_a_day_from_the_hour_with_hourly_retries_after_a_failure(clean):
    db = clean
    now = datetime(2026, 9, 28, 8, 0)
    assert rate_feeds.due(db, now) is False                           # off
    rate_feeds.save_settings(db, {"enabled": True, "ecb": True, "hour": 7, "feeds": []})
    assert rate_feeds.due(db, datetime(2026, 9, 28, 6, 59)) is False  # before the hour
    assert rate_feeds.due(db, now) is True

    def ran(attempts, error, at):
        rate_feeds._save_status(db, {"date": "2026-09-28", "attempts": attempts,
                                     "ran_at": at.replace(tzinfo=timezone.utc).isoformat(),
                                     "results": [{"source": "ecb", **({"error": "down"} if error else {"added": 5})}]})
    ran(1, False, now)
    assert rate_feeds.due(db, now + timedelta(hours=5)) is False       # done for today
    assert rate_feeds.due(db, datetime(2026, 9, 29, 7, 0)) is True     # tomorrow
    ran(1, True, now)
    assert rate_feeds.due(db, now + timedelta(minutes=30)) is False    # retry waits an hour
    assert rate_feeds.due(db, now + timedelta(hours=1)) is True
    ran(rate_feeds.RETRIES, True, now)
    assert rate_feeds.due(db, now + timedelta(hours=4)) is False       # gave up for today


def test_the_scheduler_runs_the_feeds_once_for_the_whole_platform(clean, monkeypatch):
    from app.db import session as db_session
    from app.jobs import scheduler as sched
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)
    for name in ("job_recurring_run_due", "job_notifications_refresh", "job_daily_digest",
                 "job_recurring_invoices", "job_invoice_reminders"):
        monkeypatch.setattr(sched, name, lambda db, today: {})
    sched.STATUS.clear()
    sched._last_refresh_tick = None
    db = clean
    rate_feeds.save_settings(db, {"enabled": True, "ecb": True, "hour": 7, "feeds": []})
    db.commit()
    calls = _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: ECB_XML})
    assert "rate_feeds" not in sched.run_pending_jobs(datetime(2026, 9, 28, 6, 0))
    assert "rate_feeds" in sched.run_pending_jobs(datetime(2026, 9, 28, 7, 5))
    assert "rate_feeds" not in sched.run_pending_jobs(datetime(2026, 9, 28, 9, 0))
    assert calls == [rate_feeds.ECB_URL]                                # one fetch, not one per company
    st = sched.STATUS["rate_feeds"].as_dict()
    assert st["last_error"] is None and st["detail"]["results"][0]["source"] == "ecb"
    db.expire_all()
    assert _shared(db, "USD", "GBP")


def test_a_failing_run_is_reported_in_the_job_status(clean, monkeypatch):
    from app.db import session as db_session
    from app.jobs import scheduler as sched
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)
    sched.STATUS.clear()
    rate_feeds.save_settings(clean, {"enabled": True, "ecb": True, "hour": 7, "feeds": []})
    clean.commit()
    _fake_fetch(monkeypatch, {rate_feeds.ECB_URL: httpx.ConnectError("no route")})
    assert sched.run_platform_rate_feeds(datetime(2026, 9, 28, 8, 0)) is True
    assert sched.STATUS["rate_feeds"].last_error == "failed: ECB"


# ─── 5. Rates per company ───────────────────────────────────────────────

def _login(client, cid, *, role="owner", superadmin=False):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=f"{role}-{cid[:4] if cid else 'x'}",
                               is_admin=role == "owner", role=role, company_id=cid, is_superadmin=superadmin)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def two_cos(client, clean):
    from tests.test_admin_audit import _purge_company
    db = clean
    ids = []
    for name, ccy in (("Rial Co", "IRR"), ("Sterling Co", "GBP")):
        c = Company(id=uuid.uuid4(), name=name, slug=f"fx-{uuid.uuid4().hex[:6]}", locale="ir",
                    base_currency=ccy, status="active", token_version=0)
        db.add(c)
        db.commit()
        ids.append(str(c.id))
    db.expunge_all()
    yield client, ids
    client.cookies.clear()
    for cid in ids:
        _purge_company(db, cid)


def _post(api, fc, tc, rate, when, **extra):
    return api.post("/fx/rates", json={"from_currency": fc, "to_currency": tc, "rate": rate,
                                       "effective_date": when, **extra})


def test_a_companys_own_rate_is_invisible_to_others_and_wins_over_the_shared_one(two_cos, db):
    from app.db.tenant import use_company
    client, (a, b) = two_cos
    db.add(ExchangeRate(from_currency="USD", to_currency="IRR", rate=1_000_000, effective_date=date(2026, 9, 1),
                        note="feed:Market"))
    db.commit()
    api_a = _login(client, a)
    own = _post(api_a, "USD", "IRR", 700_000, "2026-08-01")
    assert own.status_code == 201 and own.json()["shared"] is False
    listed = api_a.get("/fx/rates").json()
    assert {(r["rate"], r["shared"], r["source"]) for r in listed} == {(700_000, False, None),
                                                                       (1_000_000, True, "Market")}
    with use_company(a):   # its own rate for the pair, even though the shared one is newer
        assert fx_service.get_rate(db, "USD", "IRR", date(2026, 9, 28)) == 700_000
        assert fx_service.get_rate(db, "IRR", "USD", date(2026, 9, 28)) == pytest.approx(1 / 700_000)

    api_b = _login(client, b)
    assert [r["rate"] for r in api_b.get("/fx/rates").json()] == [1_000_000]
    with use_company(b):
        assert fx_service.get_rate(db, "USD", "IRR", date(2026, 9, 28)) == 1_000_000
    # B can neither delete A's rate nor the shared one.
    assert api_b.delete(f"/fx/rates/{own.json()['id']}").status_code == 404
    shared_id = next(r["id"] for r in listed if r["shared"])
    r = api_b.delete(f"/fx/rates/{shared_id}")
    assert r.status_code == 403 and "platform admin" in r.json()["detail"]
    # B's own rate for the same day is its own row, not A's.
    assert _post(api_b, "USD", "IRR", 900_000, "2026-08-01").status_code == 201
    with use_company(a):
        assert fx_service.get_rate(db, "USD", "IRR", date(2026, 9, 28)) == 700_000
    assert _login(client, a).delete(f"/fx/rates/{own.json()['id']}").status_code == 204


def test_only_the_platform_admin_sets_or_deletes_shared_rates(two_cos, db):
    client, (a, _b) = two_cos
    r = _post(_login(client, a), "EUR", "IRR", 1_200_000, "2026-09-01", shared=True)
    assert r.status_code == 403
    admin = _login(client, a, superadmin=True)
    r = _post(admin, "EUR", "IRR", 1_200_000, "2026-09-01", shared=True)
    assert r.status_code == 201 and r.json()["shared"] is True
    assert admin.delete(f"/fx/rates/{r.json()['id']}").status_code == 204


def test_the_latest_view_shows_one_row_per_pair_and_owner(two_cos, db):
    client, (a, _b) = two_cos
    for day, rate in (("2026-09-01", 1.0), ("2026-09-02", 1.1), ("2026-09-03", 1.2)):
        db.add(ExchangeRate(from_currency="EUR", to_currency="USD", rate=rate, effective_date=date.fromisoformat(day),
                            note="feed:ecb"))
    db.commit()
    api = _login(client, a)
    _post(api, "EUR", "USD", 1.05, "2026-08-01")
    _post(api, "EUR", "USD", 1.06, "2026-08-02")
    assert len(api.get("/fx/rates").json()) == 5
    latest = api.get("/fx/rates", params={"latest": True}).json()
    assert sorted((r["rate"], r["shared"]) for r in latest) == [(1.06, False), (1.2, True)]


def test_pairs_without_a_rate_are_crossed_through_a_common_currency(clean):
    db = clean
    on = date(2026, 9, 28)
    db.add_all([ExchangeRate(from_currency="GOLDG", to_currency="IRR", rate=95_000_000, effective_date=on),
                ExchangeRate(from_currency="GBP", to_currency="IRR", rate=1_400_000, effective_date=on),
                ExchangeRate(from_currency="USD", to_currency="GBP", rate=0.75, effective_date=on)])
    db.commit()
    assert fx_service.get_rate(db, "GOLDG", "GBP", on) == pytest.approx(95_000_000 / 1_400_000)   # via IRR
    assert fx_service.get_rate(db, "GBP", "GOLDG", on) == pytest.approx(1_400_000 / 95_000_000)
    assert fx_service.get_rate(db, "USD", "IRR", on) == pytest.approx(0.75 * 1_400_000)             # via GBP
    assert fx_service.get_rate(db, "TRY", "GBP", on) is None


def test_gold_held_by_a_person_is_valued_at_the_feed_price(clean, monkeypatch):
    from app.db.tenant import use_company
    from app.models.personal_holding import PersonalHolding
    from app.services.net_worth_service import _market_value
    db = clean
    _fake_fetch(monkeypatch, {FEED_URL: json.dumps(MARKET_JSON).encode()})
    feed = _feed()
    rate_feeds.run_feed(db, {**feed, "items": [{**i, "multiply": str(i["multiply"])} for i in feed["items"]]},
                        today=date.today())
    db.commit()
    cid = str(uuid.uuid4())
    with use_company(cid):
        value, missing, rate = _market_value(db, [PersonalHolding(account_code="1130", unit="GOLDG", quantity=2)],
                                             "IRR", date.today())
    assert missing == [] and rate == 95_000_000 and value == 190_000_000


# ─── 6. The platform admin's routes ───────────────────────────────────

def test_feed_routes_are_the_platform_admins_alone(two_cos):
    client, (a, _b) = two_cos
    owner = _login(client, a)
    assert owner.get("/admin/rate-feeds").status_code == 403
    assert owner.put("/admin/rate-feeds", json={"enabled": True}).status_code == 403
    assert owner.post("/admin/rate-feeds/run").status_code == 403
    assert owner.post("/admin/rate-feeds/test", json={"url": FEED_URL}).status_code == 403


def test_admin_saves_tests_and_runs_the_feeds(two_cos, monkeypatch, db):
    client, (a, _b) = two_cos
    admin = _login(client, a, superadmin=True)
    first = admin.get("/admin/rate-feeds").json()
    assert first["enabled"] is False and first["ecb"] is True and first["feeds"] == [] and first["status"] == {}
    assert "GBP" in first["ecb_currencies"] and "AED" in first["ecb_currencies"]

    r = admin.put("/admin/rate-feeds", json={"enabled": True, "ecb": True, "hour": 6, "feeds": [_feed()]})
    assert r.status_code == 200, r.text
    assert r.json()["feeds"][0]["url"].endswith("api_key=…") and "SECRET123" not in r.text
    assert admin.put("/admin/rate-feeds", json={"enabled": True, "hour": 30}).status_code == 422

    _fake_fetch(monkeypatch, {FEED_URL: json.dumps(MARKET_JSON).encode(), rate_feeds.ECB_URL: ECB_XML})
    preview = admin.post("/admin/rate-feeds/test", json={"url": r.json()["feeds"][0]["url"], "name": "Market"}).json()
    assert preview["ok"] is True and {"path": "coins.0.sell", "value": 95000000.0} in preview["numbers"]
    assert not _shared(db, "GOLDC", "IRR")                                  # a test stores nothing

    run = admin.post("/admin/rate-feeds/run").json()
    assert {x["source"] for x in run["results"]} == {"ecb", "Market"}
    after = admin.get("/admin/rate-feeds").json()
    assert after["status"]["date"] == date.today().isoformat() and len(after["status"]["results"]) == 2
    db.expire_all()
    assert _shared(db, "GOLDC", "IRR") and _shared(db, "USD", "GBP")


def test_a_preview_of_a_bad_feed_says_why(two_cos, monkeypatch):
    client, (a, _b) = two_cos
    admin = _login(client, a, superadmin=True)
    out = admin.post("/admin/rate-feeds/test", json={"url": "https://10.0.0.1/rates"}).json()
    assert out["ok"] is False and "public" in out["error"]
    _fake_fetch(monkeypatch, {"https://feed.example/html": b"<html>login</html>"})
    out = admin.post("/admin/rate-feeds/test", json={"url": "https://feed.example/html"}).json()
    assert out["ok"] is False and out["numbers"] == []


# ─── 7. Caches notice a shared rate change ───────────────────────────────

def test_a_shared_rate_bumps_the_platform_version_even_inside_a_company(two_cos, db):
    from app.core.shared_state import books_version, platform_version
    from app.db.tenant import use_company
    _client, (cid, _b) = two_cos
    with use_company(cid):
        before, own_before = platform_version(db), books_version(db, cid)
        db.add(ExchangeRate(from_currency="CHF", to_currency="GBP", rate=0.9, effective_date=date(2026, 9, 28)))
        db.commit()
        assert platform_version(db) == before + 1 and books_version(db, cid) == own_before
        db.add(ExchangeRate(company_id=uuid.UUID(cid), from_currency="CHF", to_currency="GBP", rate=0.95,
                            effective_date=date(2026, 9, 28)))
        db.commit()
        assert platform_version(db) == before + 1 and books_version(db, cid) == own_before + 1
