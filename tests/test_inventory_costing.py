"""Inventory costing (roadmap 2026-09 §4.4): weighted average and FIFO from
the same movements, stock valuation as of a date, reorder points and the
low-stock insight, barcodes, bills of materials and production runs, the
routes and their roles, and the AI tool."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.company import Company
from app.models.inventory import InventoryItem, InventoryMovement, InventoryMovementType
from app.services import inventory_costing as ic

TODAY = date.today()


def _state(moves, method):
    rows = [SimpleNamespace(id=i, item_id="x", movement_type=k, quantity=q, unit_cost=c)
            for i, (k, q, c) in enumerate(moves)]
    states, costs = ic.run_costing(rows, method)
    return states["x"], costs


# --- the engine --------------------------------------------------------------------------------------

def test_fifo_uses_the_oldest_layers_first():
    moves = [("IN", 10, 100), ("IN", 10, 200), ("OUT", 15, 0)]
    fifo, costs = _state(moves, ic.FIFO)
    assert (fifo.cogs, float(fifo.on_hand), fifo.value, fifo.unit_cost) == (2_000, 5.0, 1_000, 200)
    assert costs[2] == 2_000
    wa, _ = _state(moves, ic.WEIGHTED_AVERAGE)
    assert (wa.cogs, float(wa.on_hand), wa.value, wa.unit_cost) == (2_250, 5.0, 750, 150)


def test_fifo_oversold_then_restocked():
    s, _ = _state([("IN", 2, 100), ("OUT", 5, 0)], ic.FIFO)
    assert (s.cogs, float(s.on_hand), s.value, float(s.shortfall)) == (500, -3.0, 0, 3.0)   # 3 at the last cost
    s, _ = _state([("IN", 2, 100), ("OUT", 5, 0), ("IN", 10, 120)], ic.FIFO)
    assert (float(s.on_hand), s.value, float(s.shortfall)) == (7.0, 840, 0.0)            # the 3 filled first


def test_fractional_quantities_are_exact():
    s, _ = _state([("IN", 0.5, 1_000), ("IN", 0.25, 2_000), ("OUT", 0.5, 0)], ic.FIFO)
    assert (s.cogs, s.value, float(s.on_hand)) == (500, 500, 0.25)
    s, _ = _state([("IN", 0.1, 3), ("IN", 0.2, 3), ("OUT", 0.3, 0)], ic.WEIGHTED_AVERAGE)
    assert float(s.on_hand) == 0.0 and s.value == 0                     # no 0.30000000000000004 left over


def test_adjustments_add_a_layer_and_money_rounds_half_up():
    s, _ = _state([("IN", 5, 100), ("ADJUSTMENT", 5, 300), ("OUT", 6, 0)], ic.FIFO)
    assert s.cogs == 500 + 300 and s.value == 1_200
    s, _ = _state([("IN", 1, 2), ("IN", 1, 3)], ic.WEIGHTED_AVERAGE)
    assert s.unit_cost == 3                                             # 2.5 → 3 (half-to-even would give 2)


def test_an_explicit_out_cost_counts_only_under_weighted_average():
    wa, _ = _state([("IN", 10, 100), ("OUT", 2, 500)], ic.WEIGHTED_AVERAGE)
    fifo, _ = _state([("IN", 10, 100), ("OUT", 2, 500)], ic.FIFO)
    assert (wa.cogs, fifo.cogs) == (1_000, 200)


# --- the books --------------------------------------------------------------------------------------------

@pytest.fixture()
def co(db, client):
    c = Company(id=uuid.uuid4(), name="Stock Co", slug=f"stk-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = c.id                                          # a plain value: some tests expunge the session
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(cid), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"cid": cid, "login": login}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(cid))


def _item(owner, name, **kw):
    r = owner.post("/manager-reports/inventory/items", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _move(owner, item, kind, qty, cost=0, days_ago=10):
    r = owner.post("/manager-reports/inventory/movements", json={
        "item_id": item["id"], "movement_date": (TODAY - timedelta(days=days_ago)).isoformat(),
        "movement_type": kind, "quantity": qty, "unit_cost": cost})
    assert r.status_code == 201, r.text


def test_the_method_setting_and_the_reports_follow_it(db, co):
    owner = co["login"]()
    widget = _item(owner, "Widget")
    _move(owner, widget, "IN", 10, 100, days_ago=30)
    _move(owner, widget, "IN", 10, 200, days_ago=20)
    _move(owner, widget, "OUT", 15, days_ago=5)
    assert owner.get("/manager-reports/inventory/settings").json() == \
        {"method": "weighted_average", "methods": ["weighted_average", "fifo"]}
    wa = owner.get("/manager-reports/inventory/valuation").json()
    assert (wa["totals"]["value"], wa["totals"]["cogs"]) == (750, 2_250)
    assert wa["other_method"] == {"method": "fifo", "value": 1_000, "cogs": 2_000}
    bal = owner.get("/manager-reports/inventory/balance").json()["rows"][0]
    assert (bal["inventory_value"], bal["cogs"], bal["average_cost"]) == (750, 2_250, 150)

    assert owner.put("/manager-reports/inventory/settings", json={"method": "lifo"}).status_code == 422
    assert owner.put("/manager-reports/inventory/settings", json={"method": "fifo"}).json()["method"] == "fifo"
    fifo = owner.get("/manager-reports/inventory/valuation").json()
    assert (fifo["method"], fifo["totals"]["value"], fifo["totals"]["cogs"]) == ("fifo", 1_000, 2_000)
    bal = owner.get("/manager-reports/inventory/balance").json()["rows"][0]
    assert (bal["inventory_value"], bal["cogs"]) == (1_000, 2_000)
    # as of before the sale, and the other method on request
    early = owner.get(f"/manager-reports/inventory/valuation?as_of={(TODAY - timedelta(days=10)).isoformat()}").json()
    assert early["rows"][0]["on_hand"] == 20.0 and early["totals"]["value"] == 3_000
    assert owner.get("/manager-reports/inventory/valuation?method=weighted_average").json()["totals"]["value"] == 750


def test_barcodes(db, co):
    owner = co["login"]()
    a = _item(owner, "Tea 500g", barcode="6260000000017", sku="TEA")
    assert a["barcode"] == "6260000000017"
    r = owner.post("/manager-reports/inventory/items", json={"name": "Other", "barcode": "6260000000017"})
    assert r.status_code == 409
    b = _item(owner, "Coffee")
    assert owner.patch(f"/manager-reports/inventory/items/{b['id']}", json={"barcode": "6260000000017"}).status_code == 409
    assert owner.patch(f"/manager-reports/inventory/items/{a['id']}", json={"barcode": " 6260000000017 "}).status_code == 200
    found = owner.get("/manager-reports/inventory/items/by-barcode/6260000000017").json()
    assert (found["id"], found["name"]) == (a["id"], "Tea 500g")
    assert owner.get("/manager-reports/inventory/items/by-barcode/0000").status_code == 404
    r = owner.patch(f"/manager-reports/inventory/items/{b['id']}", json={"barcode": "", "name": "Coffee 1kg"})
    assert r.json()["barcode"] is None and r.json()["name"] == "Coffee 1kg"


def test_reorder_points_and_the_low_stock_insight(db, co):
    owner = co["login"]()
    tea = _item(owner, "Tea", reorder_level=5, reorder_qty=20)
    rice = _item(owner, "Rice")
    owner.patch(f"/manager-reports/inventory/items/{rice['id']}", json={"reorder_level": 3})
    _move(owner, tea, "IN", 10, 100)
    _move(owner, tea, "OUT", 6)
    _move(owner, rice, "IN", 10, 50)
    low = owner.get("/manager-reports/inventory/low-stock").json()
    assert low["count"] == 1 and low["rows"][0]["name"] == "Tea"
    assert (low["rows"][0]["on_hand"], low["rows"][0]["suggested_order"]) == (4.0, 20.0)
    val = owner.get("/manager-reports/inventory/valuation").json()
    assert val["totals"]["below_reorder"] == 1
    with use_company(co["cid"]):
        found = ic.detect_low_stock(db, TODAY)
        assert len(found) == 1 and found[0].kind == "low_stock" and found[0].page == "inventory"
        assert found[0].localize("en")["title"] == "1 item(s) at or below the reorder point"
        assert "Tea" in found[0].localize("fa")["message"]
        from app.services.insight_service import all_detectors
        assert ("low_stock", ic.detect_low_stock) in all_detectors()
    _move(owner, tea, "IN", 20, 100)
    assert owner.get("/manager-reports/inventory/low-stock").json()["count"] == 0


def test_a_stock_movement_refreshes_the_insights(db, co):
    from app.core.shared_state import books_version
    owner = co["login"]()
    tea = _item(owner, "Tea")
    with use_company(co["cid"]):
        before = books_version(db, str(co["cid"]))
    _move(owner, tea, "IN", 1, 1)
    with use_company(co["cid"]):
        assert books_version(db, str(co["cid"])) > before


def _bakery(owner):
    flour = _item(owner, "Flour (kg)")
    sugar = _item(owner, "Sugar (kg)")
    cake = _item(owner, "Cake", sku="CAKE")
    _move(owner, flour, "IN", 10, 1_000, days_ago=20)
    _move(owner, sugar, "IN", 5, 2_000, days_ago=20)
    r = owner.put(f"/manager-reports/inventory/items/{cake['id']}/bom", json={"lines": [
        {"component_id": flour["id"], "quantity": 0.5}, {"component_id": sugar["id"], "quantity": 0.25}]})
    assert r.status_code == 200, r.text
    return flour, sugar, cake


def test_bill_of_materials_rules(db, co):
    owner = co["login"]()
    flour, sugar, cake = _bakery(owner)
    bom = owner.get(f"/manager-reports/inventory/items/{cake['id']}/bom").json()
    assert [(ln["name"], ln["quantity"]) for ln in bom["lines"]] == [("Flour (kg)", 0.5), ("Sugar (kg)", 0.25)]
    url = f"/manager-reports/inventory/items/{cake['id']}/bom"
    assert owner.put(url, json={"lines": [{"component_id": cake["id"], "quantity": 1}]}).status_code == 422
    assert owner.put(url, json={"lines": [{"component_id": flour["id"], "quantity": 1},
                                          {"component_id": flour["id"], "quantity": 2}]}).status_code == 422
    assert owner.put(url, json={"lines": [{"component_id": flour["id"], "quantity": 0}]}).status_code == 422
    assert owner.put(url, json={"lines": [{"component_id": str(uuid.uuid4()), "quantity": 1}]}).status_code == 404
    # a cycle: flour made from cake, when cake is made from flour
    r = owner.put(f"/manager-reports/inventory/items/{flour['id']}/bom",
                  json={"lines": [{"component_id": cake["id"], "quantity": 1}]})
    assert r.status_code == 422 and "can't itself be made" in r.text
    assert len(owner.get(url).json()["lines"]) == 2                      # untouched by the refusals
    assert owner.put(url, json={"lines": []}).json()["lines"] == []


def test_a_production_run(db, co):
    owner = co["login"]()
    flour, sugar, cake = _bakery(owner)
    body = {"product_id": cake["id"], "quantity": 4, "on": (TODAY - timedelta(days=2)).isoformat()}
    r = owner.post("/manager-reports/inventory/production", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["components_cost"], out["unit_cost"], out["reference"]) == (4_000, 1_000, "PROD-CAKE")
    rows = {row["name"]: row for row in owner.get("/manager-reports/inventory/valuation").json()["rows"]}
    assert (rows["Flour (kg)"]["on_hand"], rows["Flour (kg)"]["value"]) == (8.0, 8_000)
    assert (rows["Sugar (kg)"]["on_hand"], rows["Sugar (kg)"]["value"]) == (4.0, 8_000)
    assert (rows["Cake"]["on_hand"], rows["Cake"]["value"], rows["Cake"]["unit_cost"]) == (4.0, 4_000, 1_000)
    with use_company(co["cid"]):
        outs = db.execute(select(InventoryMovement).where(InventoryMovement.reference == "PROD-CAKE",
                                                          InventoryMovement.movement_type == InventoryMovementType.OUT)
                          ).scalars().all()
        assert len(outs) == 2 and all(int(m.unit_cost) == 0 for m in outs)   # valued by the engine

    r = owner.post("/manager-reports/inventory/production", json=body | {"quantity": 100})
    assert r.status_code == 409
    short = r.json()["detail"]["short"]
    assert {s["component_id"] for s in short} == {flour["id"], sugar["id"]}
    assert owner.post("/manager-reports/inventory/production",
                      json=body | {"on": (TODAY + timedelta(days=1)).isoformat()}).status_code == 422
    plain = _item(owner, "No recipe")
    r = owner.post("/manager-reports/inventory/production", json=body | {"product_id": plain["id"]})
    assert r.status_code == 422 and "bill of materials" in r.text
    r = owner.post("/manager-reports/inventory/production", json=body | {"quantity": 100, "allow_short": True})
    assert r.status_code == 201 and len(r.json()["short"]) == 2


@pytest.mark.parametrize("method, unit_cost", [("fifo", 1_000), ("weighted_average", 1_500)])
def test_production_costs_components_by_the_method(db, co, method, unit_cost):
    owner = co["login"]()
    flour, sugar, cake = _bakery(owner)
    _move(owner, flour, "IN", 10, 3_000, days_ago=15)                  # a dearer second batch
    owner.put("/manager-reports/inventory/settings", json={"method": method})
    out = owner.post("/manager-reports/inventory/production", json={
        "product_id": cake["id"], "quantity": 8, "on": TODAY.isoformat()}).json()
    # flour 4 kg: FIFO 4 × 1,000; average (10×1,000 + 10×3,000)/20 = 2,000 → 8,000. Sugar 2 kg × 2,000.
    assert (out["method"], out["unit_cost"]) == (method, unit_cost)
    assert out["components_cost"] == unit_cost * 8


def test_roles_and_tenancy(db, co):
    owner = co["login"]("owner")
    tea = _item(owner, "Tea")
    viewer = co["login"]("viewer")
    assert viewer.get("/manager-reports/inventory/valuation").status_code == 200
    assert viewer.get("/manager-reports/inventory/low-stock").status_code == 200
    assert viewer.put("/manager-reports/inventory/settings", json={"method": "fifo"}).status_code == 403
    assert viewer.patch(f"/manager-reports/inventory/items/{tea['id']}", json={"name": "x"}).status_code == 403
    assert viewer.post("/manager-reports/inventory/production", json={
        "product_id": tea["id"], "quantity": 1, "on": TODAY.isoformat()}).status_code == 403
    assert co["login"]("employee").get("/manager-reports/inventory/valuation").status_code == 403
    theirs = uuid.uuid4()
    other = Company(id=theirs, name="Other", slug=f"oth-{uuid.uuid4().hex[:8]}", locale="ir", base_currency="IRR",
                    status="active", token_version=0)
    db.add(other)
    db.commit()
    try:
        with use_company(theirs):
            item = InventoryItem(name="Theirs", barcode="999")
            db.add(item)
            db.commit()
            their_id = item.id
        db.expunge_all()
        owner = co["login"]("owner")
        assert owner.get(f"/manager-reports/inventory/items/{their_id}/bom").status_code == 404
        assert owner.patch(f"/manager-reports/inventory/items/{their_id}", json={"name": "x"}).status_code == 404
        assert owner.get("/manager-reports/inventory/items/by-barcode/999").status_code == 404
        assert _item(owner, "Mine", barcode="999")["barcode"] == "999"        # barcodes are per company
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(theirs))


def test_the_ai_tool(db, co):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.inventory_tools import GetInventory, GetInventoryInput
    owner = co["login"]()
    tea = _item(owner, "Tea", reorder_level=5)
    _move(owner, tea, "IN", 3, 100)
    tool = GetInventory()
    with use_company(co["cid"]):
        ctx = ToolContext(db=db, user_id="u", username="owner")
        full = asyncio.run(tool.run(ctx, GetInventoryInput()))
        low = asyncio.run(tool.run(ctx, GetInventoryInput(low_stock_only=True)))
    assert full["method"] == "weighted_average" and full["totals"]["value"] == 300
    assert full["items"][0]["below_reorder"] is True
    assert low["count"] == 1 and low["items"][0]["suggested_order"] == 2.0
    from app.services.ai_accountant.orchestrator import build_default_registry
    assert "get_inventory" in {t.name for t in build_default_registry()}


def test_the_inventory_page_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "13-companies-products.js").read_text(encoding="utf-8")
    ops = (root / "js" / "12-ops.js").read_text(encoding="utf-8")
    for el in ("stock-method", "stock-table", "bom-product", "bom-lines", "prod-run", "mgr-inv-item-barcode",
               "mgr-inv-item-reorder"):
        assert f'id="{el}"' in html, el
    assert "loadPriceMgmtItems(); loadStockPanel();" in ops
    block = js.split("// ═══════ Stock value, reorder", 1)[1].split("// ═══════ Price Management", 1)[0]
    assert "onclick" not in block and "confirm(" not in block.replace("uiConfirm(", "")
