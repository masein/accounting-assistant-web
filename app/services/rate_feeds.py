"""Automatic exchange rates (roadmap 2026-09 §4.6).

Rates were only ever typed in. A daily job now fills ``exchange_rates`` from:

* **ECB** — the European Central Bank's daily reference rates (keyless XML).
  They are EUR-based; the job stores the cross rates each company needs —
  every major currency into USD, EUR, GBP and each reporting currency in use —
  because a rate lookup only tries the pair or its inverse. AED rides its
  fixed peg (3.6725 per USD).
* **JSON feeds** the platform admin configures for what the ECB doesn't
  publish — the free-market rial, gold by the gram, coins — from any provider
  with a JSON API (Navasan, BrsApi, TGJU…): a URL (kept encrypted, it holds the
  key) and, per item, the unit, the currency it is priced in, the JSON path
  to the number and a multiplier (×10 for a price in toman). A holding's unit
  (GOLDG for a gram of gold, GOLDC for a coin) is just a pseudo-currency, so
  personal net worth picks the prices up with no other change.

Fetched rates are dated the day they're fetched and never overwrite a rate
someone typed for that day. Nothing runs unless enabled; a failure is recorded
and the rest carries on.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("app.rate_feeds")

SETTINGS_KEY = "rate_feeds"               # platform app_settings row
STATUS_KEY = "rate_feeds_status"          # platform app_settings row: last run and results
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_MAJORS = ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "CNY", "TRY", "SEK", "NOK", "DKK", "INR")
AED_PER_USD = Decimal("3.6725")           # the UAE dirham's peg
NOTE_PREFIX = "feed:"
UNIT_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")
MAX_ITEMS = 40
DEFAULTS: dict[str, Any] = {"enabled": False, "ecb": True, "hour": 7, "feeds": []}
MAX_BYTES = 2_000_000
_TRANSPORT = None                         # tests put an httpx.MockTransport here


def _resolve(host: str, port: int) -> list[str]:
    import socket
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def check_public_url(url: str) -> None:
    """A feed is fetched by the server, so its URL must not point inside the
    network the server sits in (the metadata service, the database, a router):
    https only, and every address the host resolves to must be public."""
    import ipaddress
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("feed URLs must be https")
    try:
        addresses = _resolve(parts.hostname, parts.port or 443)
    except OSError as exc:
        raise ValueError(f"can't resolve {parts.hostname}") from exc
    for addr in addresses:
        if not ipaddress.ip_address(addr.split("%", 1)[0]).is_global:
            raise ValueError(f"{parts.hostname} is not a public address")


def _fetch(url: str) -> bytes:
    """GET with the address check on every hop (redirects are followed by
    hand for that), at most 2 MB."""
    import httpx
    with httpx.Client(timeout=15, follow_redirects=False, transport=_TRANSPORT) as client:
        for _hop in range(4):
            check_public_url(url)
            with client.stream("GET", url, headers={"User-Agent": "accounting-assistant/rate-feeds",
                                                    "Accept": "application/json, application/xml"}) as res:
                if res.is_redirect and res.headers.get("location"):
                    url = str(res.url.join(res.headers["location"]))
                    continue
                res.raise_for_status()
                body = b""
                for chunk in res.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise ValueError("the feed is larger than 2 MB")
                return body
    raise ValueError("too many redirects")


# --- settings -----------------------------------------------------------------------------------------

def _row(db: Session, key: str):
    from app.models.app_setting import AppSetting
    return db.execute(select(AppSetting).where(AppSetting.key == key, AppSetting.company_id.is_(None))).scalars().first()


def load_settings(db: Session, *, reveal: bool = False) -> dict[str, Any]:
    from app.core.secrets import decrypt_secret
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        row = _row(db, SETTINGS_KEY)
    try:
        raw = json.loads(row.value) if row and row.value else {}
    except ValueError:
        raw = {}
    out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
    feeds = []
    for f in raw.get("feeds") or []:
        url = decrypt_secret(f.get("url", ""))
        feeds.append({**f, "url": url if reveal else _mask(url)})
    out["feeds"] = feeds
    return out


_TOKEN_RE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-]{16,}$")


def _mask(url: str) -> str:
    """Show where a feed points without its key: query values hidden, and a
    path segment that looks like a key (16+ letters and digits) too."""
    if not url:
        return ""
    base, _, query = url.partition("?")
    scheme, sep, rest = base.partition("://")
    host, slash, path = rest.partition("/")
    path = "/".join("…" if _TOKEN_RE.match(seg) else seg for seg in path.split("/"))
    base = f"{scheme}{sep}{host}{slash}{path}"
    if not query:
        return base
    return base + "?" + "&".join(p.split("=", 1)[0] + "=…" for p in query.split("&") if p)


def save_settings(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    from app.core.secrets import encrypt_secret
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    hour = int(data.get("hour", DEFAULTS["hour"]))
    if not 0 <= hour <= 23:
        raise HTTPException(status_code=422, detail="The hour is 0–23.")
    current = load_settings(db, reveal=True)
    old_urls = {f.get("name"): f.get("url") for f in current["feeds"]}
    feeds = []
    for f in data.get("feeds") or []:
        name = (f.get("name") or "").strip()[:60]
        url = (f.get("url") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Every feed needs a name.")
        if "…" in url:                               # the masked URL came back: keep the stored one
            if name not in old_urls:
                raise HTTPException(status_code=422, detail=f"{name}: paste the full URL again (with its key).")
            url = old_urls[name]
        if not re.match(r"^https://", url):
            raise HTTPException(status_code=422, detail=f"{name}: the feed URL must be https.")
        items = []
        for it in (f.get("items") or [])[:MAX_ITEMS]:
            unit = (it.get("unit") or "").strip().upper()
            to = (it.get("to") or "").strip().upper()
            path = (it.get("path") or "").strip()
            if not UNIT_RE.match(unit) or not UNIT_RE.match(to) or unit == to:
                raise HTTPException(status_code=422, detail=f"{name}: a unit and the currency it's priced in, "
                                                            "in capitals (e.g. GOLDG → IRR).")
            if not path:
                raise HTTPException(status_code=422, detail=f"{name}: say where the number is (a JSON path).")
            try:
                mult = Decimal(str(it.get("multiply", 1)))
            except InvalidOperation:
                mult = Decimal(0)
            if mult <= 0:
                raise HTTPException(status_code=422, detail=f"{name}: the multiplier must be above zero.")
            items.append({"unit": unit, "to": to, "path": path[:120], "multiply": format(mult.normalize(), "f")})
        feeds.append({"name": name, "url": encrypt_secret(url), "enabled": bool(f.get("enabled", True)),
                      "items": items})
    value = json.dumps({"enabled": bool(data.get("enabled")), "ecb": bool(data.get("ecb", True)), "hour": hour,
                        "feeds": feeds})
    with tenant_bypass():
        row = _row(db, SETTINGS_KEY)
        if row is None:
            db.add(AppSetting(key=SETTINGS_KEY, value=value, company_id=None))
        else:
            row.value = value
        db.flush()
    return load_settings(db)


def load_status(db: Session) -> dict[str, Any]:
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        row = _row(db, STATUS_KEY)
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _save_status(db: Session, status: dict[str, Any]) -> None:
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    with tenant_bypass():
        row = _row(db, STATUS_KEY)
        value = json.dumps(status, ensure_ascii=False)
        if row is None:
            db.add(AppSetting(key=STATUS_KEY, value=value, company_id=None))
        else:
            row.value = value
        db.flush()


# --- parsing ------------------------------------------------------------------------------------------------

def parse_ecb(xml_bytes: bytes) -> tuple[date, dict[str, Decimal]]:
    """(the rates' date, {currency: units per EUR})."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"not ECB XML: {exc}") from exc
    day, rates = None, {"EUR": Decimal(1)}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag != "Cube":
            continue
        if el.get("time"):
            day = date.fromisoformat(el.get("time"))
        if el.get("currency") and el.get("rate"):
            rates[el.get("currency").upper()] = Decimal(el.get("rate"))
    if day is None or len(rates) < 2:
        raise ValueError("no rates in the ECB file")
    if "USD" in rates:
        rates["AED"] = rates["USD"] * AED_PER_USD
    return day, rates


def json_path(data: Any, path: str) -> Any:
    """``a.b.0.c`` — keys and list indices."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(path) from exc
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def to_number(value: Any) -> Decimal:
    from app.services.migration_import import normalize_fa
    s = normalize_fa(str(value)).replace("٬", "").replace(",", "").strip()
    try:
        n = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if n <= 0:
        raise ValueError(f"not a positive price: {value!r}")
    return n


# --- storing --------------------------------------------------------------------------------------------------

def _store(db: Session, from_ccy: str, to_ccy: str, rate: Decimal, on: date, source: str) -> str:
    """A shared rate. "added" | "updated" | "kept" (the platform admin typed
    one for that day)."""
    from app.models.exchange_rate import ExchangeRate
    row = db.execute(select(ExchangeRate).where(ExchangeRate.company_id.is_(None),
                                                ExchangeRate.from_currency == from_ccy,
                                                ExchangeRate.to_currency == to_ccy,
                                                ExchangeRate.effective_date == on)).scalars().first()
    value = float(rate)
    if row is None:
        db.add(ExchangeRate(from_currency=from_ccy, to_currency=to_ccy, rate=value, effective_date=on,
                            note=f"{NOTE_PREFIX}{source}"))
        return "added"
    if not (row.note or "").startswith(NOTE_PREFIX):
        return "kept"
    row.rate, row.note = value, f"{NOTE_PREFIX}{source}"
    return "updated"


def _targets(db: Session) -> set[str]:
    """Currencies rates are needed in: the majors plus every company's reporting currency."""
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    with tenant_bypass():
        bases = {(c or "").upper() for c in db.execute(select(Company.base_currency)).scalars() if c}
    return {"USD", "EUR", "GBP"} | bases


def run_ecb(db: Session) -> dict[str, Any]:
    """Dated the day the ECB published them (Friday's rates on a weekend)."""
    day, per_eur = parse_ecb(_fetch(ECB_URL))
    targets = sorted(t for t in _targets(db) if t in per_eur)
    sources = [c for c in (*ECB_MAJORS, "AED", *targets) if c in per_eur]
    counts = {"added": 0, "updated": 0, "kept": 0}
    for src in dict.fromkeys(sources):
        for tgt in targets:
            if src == tgt:
                continue
            rate = (per_eur[tgt] / per_eur[src]).quantize(Decimal("0.0000000001"))
            counts[_store(db, src, tgt, rate, day, "ecb")] += 1
    return {"source": "ecb", "date": day.isoformat(), **counts, "currencies": len(per_eur)}


def run_feed(db: Session, feed: dict[str, Any], *, today: date) -> dict[str, Any]:
    data = json.loads(_fetch(feed["url"]).decode("utf-8-sig"))
    out: dict[str, Any] = {"source": feed["name"], "added": 0, "updated": 0, "kept": 0, "errors": [], "values": {}}
    for it in feed["items"]:
        try:
            price = to_number(json_path(data, it["path"])) * Decimal(it["multiply"])
        except KeyError:
            out["errors"].append(f"{it['unit']}: nothing at {it['path']}")
            continue
        except ValueError as exc:
            out["errors"].append(f"{it['unit']}: {exc}")
            continue
        out[_store(db, it["unit"], it["to"], price, today, feed["name"])] += 1
        out["values"][f"{it['unit']}→{it['to']}"] = float(price)
    return out


def _numbers(data: Any, prefix: str = "", out: list | None = None) -> list[dict[str, Any]]:
    """Every value that reads as a positive number, with its path."""
    out = [] if out is None else out
    if len(out) >= 300:
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            _numbers(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(data, list):
        for i, v in enumerate(data[:100]):
            _numbers(v, f"{prefix}.{i}" if prefix else str(i), out)
    elif not isinstance(data, bool) and data is not None:
        try:
            out.append({"path": prefix, "value": float(to_number(data))})
        except ValueError:
            pass
    return out


def preview(db: Session, url: str, *, name: str | None = None) -> dict[str, Any]:
    """Fetch without storing: the numbers in the feed and their paths."""
    url = (url or "").strip()
    if "…" in url and name:
        url = next((f["url"] for f in load_settings(db, reveal=True)["feeds"] if f["name"] == name), url)
    try:
        data = json.loads(_fetch(url).decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 — a bad URL or reply is reported, never raised
        return {"ok": False, "error": str(exc)[:300], "numbers": []}
    return {"ok": True, "numbers": _numbers(data)}


def run_all(db: Session, *, today: date | None = None, force: bool = False) -> dict[str, Any]:
    """Fetch everything configured. ``force`` runs even when switched off (the
    admin's "fetch now")."""
    settings = load_settings(db, reveal=True)
    today = today or date.today()
    status: dict[str, Any] = {"ran_at": datetime.now(timezone.utc).isoformat(), "date": today.isoformat(),
                              "results": []}
    if not (settings["enabled"] or force):
        return {**status, "skipped": "switched off"}
    previous = load_status(db)
    status["attempts"] = int(previous.get("attempts", 0)) + 1 if previous.get("date") == status["date"] else 1
    jobs = ([("ECB", lambda: run_ecb(db))] if settings["ecb"] else []) + [
        (f["name"], (lambda f=f: run_feed(db, f, today=today))) for f in settings["feeds"] if f.get("enabled", True)]
    for name, job in jobs:
        try:
            result = job()
        except Exception as exc:  # noqa: BLE001 — one provider's outage never stops the others
            log.warning("rate feed failed source=%s error=%r", name, exc)
            result = {"source": name, "error": str(exc)[:300]}
        status["results"].append(result)
    db.flush()
    _save_status(db, status)
    return status


RETRIES = 3                               # a failed source is tried again, hourly, this many times a day


def due(db: Session, now: datetime) -> bool:
    """Switched on, past the hour, and not run today — or run today with a
    source failing, an hour ago or more, fewer than RETRIES times."""
    settings = load_settings(db)
    if not settings["enabled"] or now.hour < int(settings["hour"]):
        return False
    status = load_status(db)
    if status.get("date") != now.date().isoformat():
        return True
    if not any(r.get("error") for r in status.get("results", [])) or int(status.get("attempts", 1)) >= RETRIES:
        return False
    try:
        last = datetime.fromisoformat(status["ran_at"])
    except (KeyError, ValueError):
        return True
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (now_utc - last).total_seconds() >= 3600
