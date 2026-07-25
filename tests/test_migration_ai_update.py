"""Completing imported records must UPDATE the existing entity, never duplicate.

Covers the reported failure: "Complete with AI" on a migration-queue row led
the assistant to propose_create_entity for a party that already exists — for a
bank that would mint a second GL cash account. Guards:

1. propose_update_entity accepts detail fields (iban/address/phone/…) and the
   confirmed card patches the SAME record.
2. propose_create_entity with a name+type that already exists (matched
   case/whitespace/Persian-letterform-insensitively) converts to an update
   proposal (with details) or refuses with entity_exists (without) — never a
   duplicate, never a second cash account.
3. POST /migration/pending/{id}/resolve patches fields update-in-place.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models.account import Account
from app.models.entity import Entity
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.entity_create import find_entity_normalized
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateEntity,
    ProposeCreateEntityInput,
    ProposeUpdateEntity,
    ProposeUpdateEntityInput,
)

USER = "u-mig-upd"


def _ctx(db, msg="complete the record"):
    return ToolContext(db=db, user_id=USER, username="t", user_message=msg)


def _counts(db):
    return (
        db.execute(select(func.count()).select_from(Entity)).scalar(),
        db.execute(select(func.count()).select_from(Account)).scalar(),
    )


def test_update_entity_fills_detail_fields_in_place(db):
    ent = Entity(type="bank", name=f"بانک آپدیت {uuid.uuid4().hex[:6]}", code="1111")
    db.add(ent)
    db.flush()
    out = asyncio.run(ProposeUpdateEntity().run(
        _ctx(db, "شبا رو اضافه کن"),
        ProposeUpdateEntityInput(entity_id=str(ent.id), iban="IR820540102680020817909002"),
    ))
    assert out["status"] == "pending"
    assert "iban" in out["summary"].lower()
    db.refresh(ent)
    assert not ent.iban  # confirm-gated: nothing before Confirm

    n_ent, n_acc = _counts(db)
    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    db.refresh(ent)
    assert ent.iban == "IR820540102680020817909002"  # SAME record patched
    assert ent.code == "1111"                        # GL link untouched
    assert _counts(db) == (n_ent, n_acc)             # no duplicate, no new account


def test_update_entity_requires_some_change():
    with pytest.raises(ValueError):
        ProposeUpdateEntityInput(entity_id=str(uuid.uuid4()))


def test_create_with_existing_name_converts_to_update(db):
    name = f"آینده کالیژن {uuid.uuid4().hex[:6]}"
    ent = Entity(type="bank", name=name, code="1112")
    db.add(ent)
    db.flush()

    n_ent, n_acc = _counts(db)
    # The model wrongly proposes a CREATE (with the Arabic-letterform name and
    # an IBAN it gathered) — must become an update of the existing record.
    arabic_variant = name.replace("ی", "ي")
    out = asyncio.run(ProposeCreateEntity().run(
        _ctx(db, "create the bank and save the IBAN"),
        ProposeCreateEntityInput(type="bank", name=arabic_variant,
                                 iban="IR120570028200010101790457"),
    ))
    assert out["tool_name"] == "propose_update_entity"
    assert "update existing" in out["summary"].lower()
    assert "new cash account" not in out["summary"]

    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    db.refresh(ent)
    assert ent.iban == "IR120570028200010101790457"
    assert ent.code == "1112"                # kept its GL account
    assert _counts(db) == (n_ent, n_acc)     # no duplicate entity, no new GL account


def test_create_with_existing_name_and_no_details_refuses(db):
    name = f"مشتری تکراری {uuid.uuid4().hex[:6]}"
    db.add(Entity(type="client", name=name))
    db.flush()
    n_before = _counts(db)
    with pytest.raises(ToolError) as exc:
        asyncio.run(ProposeCreateEntity().run(
            _ctx(db, f"add {name} as a client"),
            ProposeCreateEntityInput(type="client", name=name),
        ))
    assert exc.value.code == "entity_exists"
    assert _counts(db) == n_before


def test_find_entity_normalized_matches_letterform_variants(db):
    name = f"گسترش كيميا {uuid.uuid4().hex[:6]}"  # Arabic ك / ي
    ent = Entity(type="supplier", name=name)
    db.add(ent)
    db.flush()
    persian = name.replace("ك", "ک").replace("ي", "ی")
    found = find_entity_normalized(db, "supplier", f"  {persian}  ")
    assert found is not None and found.id == ent.id
    assert find_entity_normalized(db, "client", persian) is None  # type-scoped


def test_pending_resolve_patches_fields_in_place(auth_client, db):
    # Stage a minimal import that queues one incomplete client.
    from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml, _upload

    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["50001", "مشتري ريزولوفيلد", "طرف مقابل", "0", "0", "70.0000", "0"],
    ])
    kol_file = _ss_xml([
        HEADER_4,
        ["7780", "موازنه ريزولوفيلد", "0", "0", "0.0000", "70.0000"],
        ["7790", "مقابل ريزولوفيلد", "0", "0", "70.0000", "0.0000"],
    ])
    r = auth_client.post("/migration/import/preview",
                         files=_upload([("tafsili.xls", tafsili), ("kol.xls", kol_file)]))
    auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    rec = next(p for p in auth_client.get("/migration/pending").json()
               if p["entity_name"] == "مشتری ریزولوفیلد")

    n_ent = db.execute(select(func.count()).select_from(Entity)).scalar()
    resp = auth_client.post(
        f"/migration/pending/{rec['id']}/resolve",
        json={"fields": {"address": "تهران، سعادت‌آباد", "phone": "021-22334455"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"

    ent = db.get(Entity, uuid.UUID(rec["entity_id"]))
    assert ent.address == "تهران، سعادت‌آباد"      # patched in place
    assert ent.phone == "021-22334455"
    assert db.execute(select(func.count()).select_from(Entity)).scalar() == n_ent
    assert all(p["id"] != rec["id"] for p in auth_client.get("/migration/pending").json())


def test_pending_resolve_rejects_unknown_fields(auth_client, db):
    from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml, _upload

    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["50002", "مشتري بدفيلد", "طرف مقابل", "0", "0", "80.0000", "0"],
    ])
    kol_file = _ss_xml([
        HEADER_4,
        ["7800", "موازنه بدفيلد", "0", "0", "0.0000", "80.0000"],
        ["7801", "مقابل بدفيلد", "0", "0", "80.0000", "0.0000"],
    ])
    r = auth_client.post("/migration/import/preview",
                         files=_upload([("tafsili.xls", tafsili), ("kol.xls", kol_file)]))
    auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    rec = next(p for p in auth_client.get("/migration/pending").json()
               if p["entity_name"] == "مشتری بدفیلد")
    resp = auth_client.post(f"/migration/pending/{rec['id']}/resolve",
                            json={"fields": {"type": "supplier"}})
    assert resp.status_code == 400
