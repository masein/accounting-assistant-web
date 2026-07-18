"""AI propose_update_entity — rename / retype an EXISTING party instead of
creating a duplicate (the reported failure: user asked to rename, AI opened a
new account)."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models.entity import Entity
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.proposal_tools import (
    ProposeUpdateEntity,
    ProposeUpdateEntityInput,
)

USER = "u-upd"


def _entity(db, name, type_="employee"):
    e = Entity(name=name, type=type_)
    db.add(e)
    db.flush()
    return e


def _ctx(db, msg="rename it"):
    return ToolContext(db=db, user_id=USER, username="t", user_message=msg)


def test_rename_is_confirm_gated_and_updates_same_record(db):
    ent = _entity(db, f"Temp Name {uuid.uuid4().hex[:6]}")
    out = asyncio.run(ProposeUpdateEntity().run(
        _ctx(db), ProposeUpdateEntityInput(entity_id=str(ent.id), new_name="Sahra Studio")))
    assert out["status"] == "pending"
    db.refresh(ent)
    assert ent.name != "Sahra Studio"  # nothing changes before Confirm

    n_before = db.execute(select(func.count()).select_from(Entity)).scalar()
    res = execute_proposal(db, confirmation_token=out["confirmation_token"],
                           actor_user_id=USER, actor_username="t")
    assert res.transaction_id is None
    db.refresh(ent)
    assert ent.name == "Sahra Studio"  # SAME record renamed
    assert db.execute(select(func.count()).select_from(Entity)).scalar() == n_before  # no duplicate


def test_retype_employee_to_shareholder(db):
    ent = _entity(db, f"Mehdi {uuid.uuid4().hex[:6]}", type_="employee")
    out = asyncio.run(ProposeUpdateEntity().run(
        _ctx(db, "مهدی سهامداره نه کارمند"),
        ProposeUpdateEntityInput(entity_id=str(ent.id), new_type="shareholder")))
    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    db.refresh(ent)
    assert ent.type == "shareholder"


def test_lookup_by_unique_name(db):
    marker = uuid.uuid4().hex[:8]
    ent = _entity(db, f"Solo-{marker}")
    out = asyncio.run(ProposeUpdateEntity().run(
        _ctx(db), ProposeUpdateEntityInput(current_name=f"Solo-{marker}", new_name=f"Renamed-{marker}")))
    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    db.refresh(ent)
    assert ent.name == f"Renamed-{marker}"


def test_ambiguous_name_and_missing_entity_error(db):
    marker = uuid.uuid4().hex[:8]
    _entity(db, f"Twin-{marker}", "client")
    _entity(db, f"Twin-{marker}", "supplier")
    with pytest.raises(ToolError):
        asyncio.run(ProposeUpdateEntity().run(
            _ctx(db), ProposeUpdateEntityInput(current_name=f"Twin-{marker}", new_name="X Y")))
    with pytest.raises(ToolError):
        asyncio.run(ProposeUpdateEntity().run(
            _ctx(db), ProposeUpdateEntityInput(entity_id=str(uuid.uuid4()), new_name="X Y")))


def test_no_change_and_bad_input_rejected(db):
    ent = _entity(db, f"Same {uuid.uuid4().hex[:6]}", "client")
    with pytest.raises(ToolError):  # requested values already match
        asyncio.run(ProposeUpdateEntity().run(
            _ctx(db), ProposeUpdateEntityInput(entity_id=str(ent.id), new_type="client")))
    with pytest.raises(ValueError):  # nothing to change at all
        ProposeUpdateEntityInput(entity_id=str(ent.id))
    with pytest.raises(ValueError):  # no identifier
        ProposeUpdateEntityInput(new_name="X Y")


def test_tool_is_registered():
    from app.services.ai_accountant.orchestrator import build_default_registry
    reg = build_default_registry()
    names = {t.name for t in reg.all()} if hasattr(reg, "all") else set(getattr(reg, "_tools", {}).keys())
    assert "propose_update_entity" in names
