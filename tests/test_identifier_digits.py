"""Identifiers typed on a Persian keyboard are stored in 0–9: an IBAN in
Persian digits was refused as malformed, and a national ID or postal code
kept its Persian digits all the way to the Moadian export."""
from __future__ import annotations

import uuid

from app.utils.digits import ascii_digits


def test_ascii_digits():
    assert ascii_digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789"
    assert ascii_digits("٠١٢٣٤٥٦٧٨٩") == "0123456789"
    assert ascii_digits("IR۸۲ ۰۵۴۰") == "IR82 0540"
    assert ascii_digits(None) is None and ascii_digits(12) == 12


def test_an_entity_typed_in_persian_digits_is_stored_in_ascii(auth_client):
    body = {"type": "supplier", "name": f"کاغذ {uuid.uuid4().hex[:6]}", "national_id": "۰۰۱۲۳۴۵۶۷۸",
            "economic_code": "۴۱۱۱۱۱۱۱۱۱۱۱", "postal_code": "۱۹۱۶۹۸۳۷۱۱", "phone": "۰۹۱۲۱۲۳۴۵۶۷",
            "iban": "IR۸۲ ۰۵۴۰ ۱۰۲۶ ۸۰۰۲ ۰۸۱۷ ۹۰۹۰ ۰۲"}
    r = auth_client.post("/entities", json=body)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["national_id"], e["economic_code"], e["postal_code"], e["phone"]) == \
        ("0012345678", "411111111111", "1916983711", "09121234567")
    assert e["iban"] == "IR820540102680020817909002"                          # valid once its digits are read
    r = auth_client.patch(f"/entities/{e['id']}", json={"postal_code": "١٢٣٤٥٦٧٨٩٠"})
    assert r.status_code == 200 and r.json()["postal_code"] == "1234567890"


def test_the_company_profile_stores_its_identifiers_in_ascii(auth_client):
    r = auth_client.put("/admin/company-profile", json={"national_id": "۱۴۰۰۵۰۰۰۰۰۰", "postal_code": "۱۵۸۷۶۵۴۳۲۱",
                                                  "economic_code": "۴۱۱۱۱۱۱۱۱۱۱۱"})
    assert r.status_code == 200, r.text
    got = auth_client.get("/admin/company-profile").json()
    assert (got["national_id"], got["postal_code"], got["economic_code"]) == ("14005000000", "1587654321", "411111111111")
