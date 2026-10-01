"""Web push for the bell (roadmap 2026-09 §4.10, part 2): RFC 8291 payload
encryption (against the RFC's own example, and decrypted end to end), the
VAPID token (RFC 8292), the key pair kept once, which push services are
accepted, the subscription routes, and delivery — who gets which alert, a
summary instead of a flood, gone devices removed, nothing sent twice."""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import tenant_bypass, use_company
from app.models.app_setting import AppSetting
from app.models.company import Company
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import web_push as wp

RFC_BODY = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK"
            "6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
RFC_UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SENDER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"


# --- the receiving side, written from RFC 8291 §3 independently of the sender --------------------

def _hkdf(salt, ikm, info, n):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def decrypt(body: bytes, ua_private_b64: str, auth_b64: str) -> bytes:
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    assert rs >= 18 and len(ciphertext) <= rs
    ua_priv = ec.derive_private_key(int.from_bytes(wp.b64url_decode(ua_private_b64), "big"), ec.SECP256R1())
    ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(wp.b64url_decode(auth_b64), secret, b"WebPush: info\x00" + ua_pub + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    body = padded.rstrip(b"\x00")
    assert body.endswith(b"\x02"), "last-record delimiter"
    return body[:-1]


def _device():
    """A fresh browser-side key pair + auth secret, as pushManager.subscribe makes."""
    import os
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return (wp.b64url(priv.private_numbers().private_value.to_bytes(32, "big")), wp.b64url(pub),
            wp.b64url(os.urandom(16)))


# --- encryption and VAPID ---------------------------------------------------------------------------

def test_encryption_reproduces_the_rfc_example():
    raw = wp.b64url_decode(RFC_BODY)
    body = wp.encrypt(b"When I grow up, I want to be a watermelon", RFC_UA_PUBLIC, RFC_AUTH,
                      sender_private=wp.b64url_decode(RFC_SENDER_PRIVATE), salt=raw[:16], record_size=4096)
    assert wp.b64url(body) == RFC_BODY
    assert decrypt(raw, RFC_UA_PRIVATE, RFC_AUTH) == b"When I grow up, I want to be a watermelon"


def test_a_real_message_decrypts_on_the_device():
    priv, pub, auth = _device()
    msg = json.dumps({"title": "فاکتور ۱۲ سررسید گذشته", "page": "invoices"}, ensure_ascii=False).encode()
    body = wp.encrypt(msg, pub, auth)
    assert decrypt(body, priv, auth) == msg
    assert wp.encrypt(msg, pub, auth) != body                       # a fresh key and salt every time


@pytest.mark.parametrize("pub, auth", [("AAAA", "BTBZMqHH6r4Tts7J_aSIgg"), (RFC_UA_PUBLIC, "short")])
def test_bad_keys_are_refused(pub, auth):
    with pytest.raises(ValueError):
        wp.encrypt(b"x", pub, auth)


def test_the_vapid_token():
    key = ec.generate_private_key(ec.SECP256R1())
    header = wp.vapid_authorization("https://fcm.googleapis.com/fcm/send/abc", key, "mailto:ops@example.com",
                                    now=1_800_000_000)
    token, k = header.removeprefix("vapid t=").split(", k=")
    head, claims, sig = token.split(".")
    assert json.loads(wp.b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(wp.b64url_decode(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1_800_043_200,
                                                    "sub": "mailto:ops@example.com"}
    raw = wp.b64url_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), wp.b64url_decode(k))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))     # raises if forged


@pytest.mark.parametrize("url, ok", [
    ("https://fcm.googleapis.com/fcm/send/x", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://web.push.apple.com/QG...", True),
    ("https://db5p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/x", False),
    ("https://fcm.googleapis.com.evil.example/x", False),
    ("https://fcm.googleapis.com@evil.example/x", False),
    ("https://evil.example/fcm.googleapis.com", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("not a url", False),
])
def test_only_the_push_services_are_accepted(url, ok):
    assert wp.endpoint_allowed(url) is ok


def test_the_key_pair_is_made_once_and_kept_encrypted(db, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", None, raising=False)
    with tenant_bypass():
        for row in db.execute(select(AppSetting).where(AppSetting.key == wp.VAPID_KEY)).scalars():
            db.delete(row)
        db.commit()
    k1, pub1 = wp.vapid_keys(db)
    db.commit()
    k2, pub2 = wp.vapid_keys(db)
    assert pub1 == pub2 and len(wp.b64url_decode(pub1)) == 65
    with tenant_bypass():
        row = db.execute(select(AppSetting).where(AppSetting.key == wp.VAPID_KEY)).scalar_one()
        assert row.company_id is None and json.loads(row.value)["private"].startswith("enc:v1:")
        assert json.loads(row.value)["public"] == pub1
    raw = wp.b64url(k1.private_numbers().private_value.to_bytes(32, "big"))
    monkeypatch.setattr(settings, "vapid_private_key", RFC_SENDER_PRIVATE, raising=False)
    _k, pinned = wp.vapid_keys(db)
    assert pinned != pub1 and raw not in pinned
    assert "web_push_vapid" in __import__("app.models.app_setting", fromlist=["x"]).PLATFORM_SETTING_KEYS


# --- the company, users, devices -----------------------------------------------------------------------

@pytest.fixture()
def co(db, client):
    cid = uuid.uuid4()
    db.add(Company(id=cid, name="Push Co", slug=f"push-{uuid.uuid4().hex[:8]}", locale="ir", base_currency="IRR",
                   status="active", token_version=0))
    db.commit()
    users = {}
    for role in ("owner", "viewer", "employee"):
        u = User(username=f"push-{role}-{uuid.uuid4().hex[:6]}", password_hash="x", password_salt="x", role=role,
                 is_active=True, company_id=cid)
        db.add(u)
        db.commit()
        users[role] = str(u.id)

    def login(role="owner"):
        tok = create_session_token(user_id=users[role], username=role, is_admin=(role == "owner"),
                                   company_id=str(cid), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"cid": cid, "users": users, "login": login}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(cid))
    with tenant_bypass():
        for uid in users.values():
            row = db.get(User, uuid.UUID(uid))
            if row is not None:
                db.delete(row)
        db.commit()


class PushService:
    """Stands in for FCM/Mozilla/Apple: records each POST, answers per endpoint."""

    def __init__(self, status=None):
        self.requests: list[httpx.Request] = []
        self.status = status or {}

    def client(self):
        def handler(req: httpx.Request):
            self.requests.append(req)
            return httpx.Response(self.status.get(str(req.url), 201))
        return httpx.Client(transport=httpx.MockTransport(handler))


def _subscribe(db, co, role, name="phone"):
    priv, pub, auth = _device()
    endpoint = f"https://fcm.googleapis.com/fcm/send/{role}-{name}-{uuid.uuid4().hex[:6]}"
    with use_company(co["cid"]):
        db.add(PushSubscription(user_id=co["users"][role], endpoint=endpoint, p256dh=pub, auth=auth, failures=0))
        db.commit()
    return {"endpoint": endpoint, "priv": priv, "auth": auth}


def _alert(db, co, **kw):
    data = {"kind": "invoice_overdue", "level": "high", "title": "Invoice 7 overdue", "message": "12 days",
            "link_page": "invoices", "dedupe_key": f"k-{uuid.uuid4().hex[:8]}"} | kw
    with use_company(co["cid"]):
        n = Notification(**data)
        db.add(n)
        db.commit()
        return n.id


def _deliver(db, co, service):
    with use_company(co["cid"]):
        out = wp.deliver_pending(db, client=service.client())
        db.commit()
    return out


def _received(service, device) -> list[dict]:
    return [json.loads(decrypt(r.content, device["priv"], device["auth"]))
            for r in service.requests if str(r.url) == device["endpoint"]]


# --- delivery ------------------------------------------------------------------------------------------------

def test_each_alert_goes_to_the_people_who_see_it(db, co):
    owner, viewer, employee = (_subscribe(db, co, r) for r in ("owner", "viewer", "employee"))
    _alert(db, co)                                                            # books alert: owner (not viewer, employee)
    _alert(db, co, kind="reminder", title="Call the bank", link_page=None, user_id=co["users"]["viewer"])
    svc = PushService()
    out = _deliver(db, co, svc)
    assert out == {"alerts": 2, "sent": 2, "gone": 0, "failed": 0}
    assert _received(svc, owner) == [{"title": "Invoice 7 overdue", "body": "12 days", "page": "invoices",
                                      "tag": _received(svc, owner)[0]["tag"]}]
    assert [m["title"] for m in _received(svc, viewer)] == ["Call the bank"]
    assert _received(svc, employee) == []
    req = svc.requests[0]
    assert req.headers["content-encoding"] == "aes128gcm" and req.headers["ttl"] == "86400"
    assert req.headers["authorization"].startswith("vapid t=")
    assert _deliver(db, co, PushService())["sent"] == 0                       # never twice
    with use_company(co["cid"]):
        assert all(n.pushed_at for n in db.execute(select(Notification)).scalars())


def test_many_alerts_become_one_summary(db, co):
    owner = _subscribe(db, co, "owner")
    for i in range(5):
        _alert(db, co, title=f"Invoice {i} overdue")
    svc = PushService()
    _deliver(db, co, svc)
    got = _received(svc, owner)
    assert len(got) == 1 and got[0]["title"] == "5 new alerts" and got[0]["tag"] == "summary"


def test_read_or_dismissed_alerts_are_not_pushed(db, co):
    _subscribe(db, co, "owner")
    now = datetime.now(timezone.utc)
    _alert(db, co, read_at=now)
    _alert(db, co, dismissed_at=now)
    svc = PushService()
    assert _deliver(db, co, svc)["sent"] == 0 and svc.requests == []
    with use_company(co["cid"]):
        assert all(n.pushed_at for n in db.execute(select(Notification)).scalars())


def test_without_devices_alerts_are_marked_so_there_is_no_backlog(db, co):
    _alert(db, co)
    _deliver(db, co, PushService())
    phone = _subscribe(db, co, "owner")
    svc = PushService()
    _deliver(db, co, svc)
    assert _received(svc, phone) == []                                        # the old alert isn't sent later


def test_a_gone_device_is_forgotten_and_a_failing_one_counted(db, co):
    gone = _subscribe(db, co, "owner", "old")
    flaky = _subscribe(db, co, "owner", "flaky")
    _alert(db, co)
    svc = PushService(status={gone["endpoint"]: 410, flaky["endpoint"]: 503})
    assert _deliver(db, co, svc) == {"alerts": 1, "sent": 0, "gone": 1, "failed": 1}
    with use_company(co["cid"]):
        rows = {s.endpoint: s for s in db.execute(select(PushSubscription)).scalars()}
    assert gone["endpoint"] not in rows and rows[flaky["endpoint"]].failures == 1


def test_an_unsafe_page_never_reaches_the_device(db, co):
    phone = _subscribe(db, co, "owner")
    _alert(db, co, link_page="javascript:alert(1)")
    svc = PushService()
    _deliver(db, co, svc)
    assert _received(svc, phone)[0]["page"] is None


def test_the_scheduler_job_pushes_after_the_refresh(db, co, monkeypatch):
    """A reminder coming due: the refresh puts it in the bell, the same job pushes it."""
    from datetime import timedelta

    from app.jobs.scheduler import job_notifications_refresh
    from app.models.notification import Reminder
    phone = _subscribe(db, co, "owner")
    today = datetime.now().date()
    with use_company(co["cid"]):
        db.add(Reminder(user_id=co["users"]["owner"], title="Renew the lease", due_date=today + timedelta(days=1),
                        days_before=3, repeat="none", status="active"))
        db.commit()
    svc = PushService()
    monkeypatch.setattr(wp, "_client", svc.client)
    with use_company(co["cid"]):
        out = job_notifications_refresh(db, today)
    assert out["push"]["sent"] == 1
    assert "Renew the lease" in _received(svc, phone)[0]["title"]


# --- routes -----------------------------------------------------------------------------------------------------

def test_subscribe_list_test_and_unsubscribe(db, co, monkeypatch):
    owner = co["login"]("owner")
    key = owner.get("/notifications/push/key").json()["public_key"]
    assert len(wp.b64url_decode(key)) == 65
    _priv, pub, auth = _device()
    endpoint = "https://updates.push.services.mozilla.com/wpush/v2/abc"
    body = {"endpoint": endpoint, "keys": {"p256dh": pub, "auth": auth}}
    assert owner.post("/notifications/push/subscriptions", json=body).status_code == 201
    assert owner.post("/notifications/push/subscriptions", json=body).status_code == 201      # refresh, no duplicate
    assert owner.get("/notifications/push/subscriptions").json() == {"devices": 1}
    svc = PushService()
    monkeypatch.setattr(wp, "_client", svc.client)
    assert owner.post("/notifications/push/test").json() == {"sent": 1, "gone": 0, "failed": 0}
    assert str(svc.requests[0].url) == endpoint
    viewer = co["login"]("viewer")
    viewer.request("DELETE", "/notifications/push/subscriptions", json={"endpoint": endpoint})  # not theirs: no effect
    assert co["login"]("owner").get("/notifications/push/subscriptions").json() == {"devices": 1}
    owner = co["login"]("owner")
    assert owner.request("DELETE", "/notifications/push/subscriptions", json={"endpoint": endpoint}).status_code == 204
    assert owner.get("/notifications/push/subscriptions").json() == {"devices": 0}


@pytest.mark.parametrize("endpoint, keys, fragment", [
    ("https://evil.example/push", None, "isn't supported"),
    ("http://fcm.googleapis.com/fcm/send/x", None, "isn't supported"),
    ("https://fcm.googleapis.com/fcm/send/x", {"p256dh": "A" * 87, "auth": "B" * 22}, "not valid"),
])
def test_bad_subscriptions_are_refused(db, co, endpoint, keys, fragment):
    _p, pub, auth = _device()
    r = co["login"]("owner").post("/notifications/push/subscriptions",
                                  json={"endpoint": endpoint, "keys": keys or {"p256dh": pub, "auth": auth}})
    assert r.status_code == 422 and fragment in r.text


def test_a_shared_device_follows_whoever_signs_in(db, co):
    _p, pub, auth = _device()
    body = {"endpoint": "https://fcm.googleapis.com/fcm/send/shared", "keys": {"p256dh": pub, "auth": auth}}
    co["login"]("owner").post("/notifications/push/subscriptions", json=body)
    co["login"]("viewer").post("/notifications/push/subscriptions", json=body)
    with use_company(co["cid"]):
        rows = db.execute(select(PushSubscription)).scalars().all()
    assert [r.user_id for r in rows] == [co["users"]["viewer"]]


def test_every_role_can_manage_its_own_devices(db, co):
    for role in ("owner", "viewer", "employee"):
        assert co["login"](role).get("/notifications/push/subscriptions").status_code == 200, role


def test_the_worker_shows_pushes_and_opens_the_page():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    sw = (root / "pwa" / "sw.js").read_text(encoding="utf-8")
    assert "self.addEventListener('push'" in sw and "showNotification" in sw
    assert "self.addEventListener('notificationclick'" in sw and "/^[a-z0-9-]{1,40}$/" in sw
    ui = (root / "js" / "03-ui.js").read_text(encoding="utf-8").split("// ═══════ Push notifications", 1)[1]
    assert "userVisibleOnly: true" in ui and "/notifications/push/subscriptions" in ui and "onclick" not in ui


def test_a_push_speaks_each_recipients_language(db, co):
    with tenant_bypass():
        db.get(User, uuid.UUID(co["users"]["owner"])).preferred_language = "fa"
        db.commit()
    owner = _subscribe(db, co, "owner")
    _alert(db, co, text_key="invoice_overdue",
           params={"number": "7", "side": "receivable", "days": 12, "date": "2026-09-19"})
    svc = PushService()
    _deliver(db, co, svc)
    got = _received(svc, owner)
    assert got[0]["title"] == "فاکتور ⁨7⁩ سررسید گذشته" and "روز" in got[0]["body"]
    for _ in range(5):
        _alert(db, co, text_key="invoice_overdue", params={"number": "8", "side": "payable", "days": 1, "date": "2026-09-30"})
    svc = PushService()
    _deliver(db, co, svc)
    assert _received(svc, owner)[0]["title"] == "5 هشدار تازه"
