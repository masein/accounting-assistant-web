"""Arabic and Persian letterforms are one letter to a search: «علی» didn't
find «علي», and «شرکت پارس» was created a second time next to «شركت پارس».
Stored names keep the letters they were typed with."""
from __future__ import annotations

import uuid

from app.utils.text import fold_fa


def test_fold_fa():
    assert fold_fa("علي كاظمي") == "علی کاظمی" and fold_fa("موسى") == "موسی"
    assert fold_fa("Ali") == "Ali" and fold_fa(None) is None


def test_searching_a_party_finds_either_letterform(auth_client):
    tag = uuid.uuid4().hex[:5]
    arabic = f"علي كريمي {tag}"                                     # typed on an Arabic keyboard
    r = auth_client.post("/entities", json={"type": "client", "name": arabic})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == arabic                                # kept as typed
    found = auth_client.get("/entities", params={"search": f"علی کریمی {tag}"}).json()
    assert [e["name"] for e in found] == [arabic]
    # the same party typed the Persian way is the same party
    twin = auth_client.post("/entities", json={"type": "client", "name": f"علی کریمی {tag}"})
    assert twin.status_code == 409, twin.text


def test_a_journal_search_and_party_filter_ignore_the_letterform(auth_client):
    tag = uuid.uuid4().hex[:5]
    accs = auth_client.get("/accounts").json()
    codes = [a["code"] for a in accs if len(a["code"]) >= 4][:2]
    r = auth_client.post("/transactions", json={"date": "2026-09-01", "description": f"اجاره دفتر يزد {tag}", "reference": f"L-{tag}",
                                                 "lines": [{"account_code": codes[0], "debit": 100, "credit": 0},
                                                           {"account_code": codes[1], "debit": 0, "credit": 100}]})
    assert r.status_code == 201, r.text
    hits = auth_client.get("/reports/transactions/search", params={"search": f"یزد {tag}"})
    assert hits.status_code == 200, hits.text
    assert hits.json()["total_count"] >= 1 and all(tag in (row.get("description") or "") for row in hits.json()["rows"])
