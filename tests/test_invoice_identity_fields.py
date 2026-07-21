"""Official-invoice identity (فاکتور رسمی): economic code / national ID /
province / city / postal code on entities + company profile, IBAN/account on the
profile, and their surfacing on document party cards."""
from __future__ import annotations

import uuid

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from tests.conftest import _CSRFTestClient

IDENTITY = {
    "economic_code": "14013316857",
    "national_id": "10101598487",
    "province": "Tehran, Tehran",
    "city": "Tehran",
    "postal_code": "1511943964",
}


def _api(client, *, admin=True):
    tok = create_session_token(
        user_id=str(uuid.uuid4()), username="owner", is_admin=admin,
        company_id=None, role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_entity_roundtrips_identity_fields(client):
    api = _api(client)
    r = api.post("/entities", json={
        "type": "client", "name": f"Roshan Flour {uuid.uuid4().hex[:6]}",
        "legal_name": "Roshan Flour Production Co. (Tehran)", **IDENTITY,
    })
    assert r.status_code == 201, r.text
    d = r.json()
    for k, v in IDENTITY.items():
        assert d[k] == v, (k, d.get(k))
    # PATCH updates a single identity field without touching the rest
    r2 = api.patch(f"/entities/{d['id']}", json={"postal_code": "1968656702"})
    assert r2.status_code == 200
    assert r2.json()["postal_code"] == "1968656702"
    assert r2.json()["economic_code"] == IDENTITY["economic_code"]


def test_company_profile_roundtrips_identity_and_bank(client):
    api = _api(client)
    r = api.put("/admin/company-profile", json={
        "legal_name": "Samaneh Novavaran Ideh Kasb Vira Co.", **IDENTITY,
        "bank_account_no": "0121518633003", "iban": "IR200170000000121518633003",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    for k, v in IDENTITY.items():
        assert d[k] == v
    assert d["bank_account_no"] == "0121518633003"
    assert d["iban"] == "IR200170000000121518633003"
    # GET returns them too
    g = api.get("/admin/company-profile").json()
    assert g["economic_code"] == IDENTITY["economic_code"]
    assert g["iban"] == "IR200170000000121518633003"


def test_document_party_cards_carry_identity(db):
    """build_recipient + the party-line builder print the official identity."""
    from app.models.entity import Entity
    from app.services.documents.branding import build_recipient
    from app.services.documents.labels import labels_for
    from app.services.documents.render import _recipient_party

    ent = Entity(name=f"Roshan {uuid.uuid4().hex[:6]}", type="client",
                 legal_name="Roshan Flour Production Co.", **IDENTITY)
    db.add(ent)
    db.flush()
    rec = build_recipient(ent)
    for k in IDENTITY:
        assert rec[k] == IDENTITY[k]

    # English card
    lines = [ln for ln in _recipient_party(rec, labels_for("uk"), "Bill To")["lines"] if ln]
    joined = " | ".join(lines)
    assert "Economic code: 14013316857" in joined
    assert "National ID: 10101598487" in joined
    assert "Postal code: 1511943964" in joined
    assert "Tehran, Tehran — Tehran" in joined
    # Persian card
    fa_lines = " | ".join(ln for ln in _recipient_party(rec, labels_for("ir"), "خریدار")["lines"] if ln)
    assert "شماره اقتصادی: 14013316857" in fa_lines
    assert "شناسه ملی: 10101598487" in fa_lines


def test_bank_details_composed_from_account_and_iban(db, client):
    """When bank_details is empty, the issuer 'how to pay' line is composed from
    the structured account + IBAN."""
    api = _api(client)
    api.put("/admin/company-profile", json={
        "bank_details": "", "bank_account_no": "0121518633003",
        "iban": "IR200170000000121518633003",
    })
    from app.services.documents.branding import build_brand
    brand = build_brand(db)
    bd = brand["issuer"]["bank_details"]
    assert bd and "0121518633003" in bd and "IR200170000000121518633003" in bd
