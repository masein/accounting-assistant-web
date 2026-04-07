"""
Comprehensive CFO Mode tests.

Tests the CFO Intelligence service and API endpoints with:
1. Empty database (no transactions)
2. Rich financial data (revenue, expenses, receivables, payables)
3. Natural language Q&A
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.transaction import Transaction, TransactionLine


# ---------------------------------------------------------------------------
# Helper: create transactions for CFO testing
# ---------------------------------------------------------------------------
def _create_financial_data(db: Session):
    """
    Create realistic financial data spanning the last 6 months:
      - Monthly revenue (sales) transactions
      - Monthly expense transactions (payroll, operating, financial)
      - Accounts receivable entries
      - Accounts payable entries
      - Cash movements
      - Invoices (sales + purchase)
    """
    accounts = {a.code: a for a in db.query(Account).all()}

    # Ensure we have needed accounts
    cash = accounts.get("1110")
    receivable = accounts.get("1112")
    payable = accounts.get("2110")
    sales = accounts.get("4110")
    payroll = accounts.get("6110")
    operating = accounts.get("6112")
    financial_exp = accounts.get("6210")
    fixed_assets = accounts.get("1210")
    capital = accounts.get("3110")

    assert cash and receivable and payable and sales and payroll, \
        f"Missing required accounts. Available: {list(accounts.keys())}"

    today = date.today()
    entities_created = []

    # Create entities
    client1 = Entity(name="Innotech Solutions", type="client", code="CLI-001")
    client2 = Entity(name="DataFlow Corp", type="client", code="CLI-002")
    supplier1 = Entity(name="Office Supplies Co", type="supplier", code="SUP-001")
    supplier2 = Entity(name="Cloud Hosting Inc", type="supplier", code="SUP-002")
    employee1 = Entity(name="Ali Rezaei", type="employee", code="EMP-001")
    bank1 = Entity(name="Mellat Bank", type="bank", code="BNK-001")
    db.add_all([client1, client2, supplier1, supplier2, employee1, bank1])
    db.flush()

    transactions = []

    for months_ago in range(6, 0, -1):
        month_date = today - timedelta(days=months_ago * 30)
        base_rev = 50_000_000 + (6 - months_ago) * 5_000_000  # Growing revenue

        # --- Revenue: Cash sale ---
        t1 = Transaction(date=month_date, description=f"Cash sale - month {7-months_ago}", reference=f"REV-{7-months_ago:03d}")
        db.add(t1)
        db.flush()
        db.add(TransactionLine(transaction_id=t1.id, account_id=cash.id, debit=base_rev, credit=0, line_description="Cash received"))
        db.add(TransactionLine(transaction_id=t1.id, account_id=sales.id, debit=0, credit=base_rev, line_description="Sales revenue"))

        # --- Revenue: Credit sale (creates receivable) ---
        credit_rev = base_rev // 2
        t2 = Transaction(date=month_date + timedelta(days=5), description=f"Credit sale to Innotech - month {7-months_ago}", reference=f"INV-{7-months_ago:03d}")
        db.add(t2)
        db.flush()
        db.add(TransactionLine(transaction_id=t2.id, account_id=receivable.id, debit=credit_rev, credit=0, line_description="Accounts receivable"))
        db.add(TransactionLine(transaction_id=t2.id, account_id=sales.id, debit=0, credit=credit_rev, line_description="Credit sales"))

        # --- Collect receivable (except last 2 months) ---
        if months_ago > 2:
            t3 = Transaction(date=month_date + timedelta(days=20), description=f"Collection from Innotech - month {7-months_ago}", reference=f"COL-{7-months_ago:03d}")
            db.add(t3)
            db.flush()
            db.add(TransactionLine(transaction_id=t3.id, account_id=cash.id, debit=credit_rev, credit=0, line_description="Cash received"))
            db.add(TransactionLine(transaction_id=t3.id, account_id=receivable.id, debit=0, credit=credit_rev, line_description="Receivable cleared"))

        # --- Payroll expense ---
        payroll_amt = 30_000_000
        t4 = Transaction(date=month_date + timedelta(days=25), description=f"Payroll - month {7-months_ago}", reference=f"PAY-{7-months_ago:03d}")
        db.add(t4)
        db.flush()
        db.add(TransactionLine(transaction_id=t4.id, account_id=payroll.id, debit=payroll_amt, credit=0, line_description="Payroll expense"))
        db.add(TransactionLine(transaction_id=t4.id, account_id=cash.id, debit=0, credit=payroll_amt, line_description="Payroll payment"))

        # --- Operating expense ---
        op_amt = 10_000_000 + months_ago * 1_000_000
        t5 = Transaction(date=month_date + timedelta(days=10), description=f"Operating expenses - month {7-months_ago}", reference=f"OPX-{7-months_ago:03d}")
        db.add(t5)
        db.flush()
        if operating:
            db.add(TransactionLine(transaction_id=t5.id, account_id=operating.id, debit=op_amt, credit=0, line_description="Operating costs"))
            db.add(TransactionLine(transaction_id=t5.id, account_id=cash.id, debit=0, credit=op_amt, line_description="Operating payment"))

        # --- Purchase on credit (creates payable, last 3 months only) ---
        if months_ago <= 3:
            payable_amt = 8_000_000
            t6 = Transaction(date=month_date + timedelta(days=15), description=f"Purchase on credit - month {7-months_ago}", reference=f"PUR-{7-months_ago:03d}")
            db.add(t6)
            db.flush()
            if operating:
                db.add(TransactionLine(transaction_id=t6.id, account_id=operating.id, debit=payable_amt, credit=0, line_description="Supplies purchased"))
            db.add(TransactionLine(transaction_id=t6.id, account_id=payable.id, debit=0, credit=payable_amt, line_description="Accounts payable"))

    # --- Create invoices ---
    inv1 = Invoice(
        number="INV-2026-001",
        kind="sales",
        status="issued",
        issue_date=today - timedelta(days=15),
        due_date=today + timedelta(days=15),
        amount=75_000_000,
        description="Consulting services Q1",
        entity_id=client1.id,
    )
    inv2 = Invoice(
        number="INV-2026-002",
        kind="purchase",
        status="issued",
        issue_date=today - timedelta(days=10),
        due_date=today + timedelta(days=20),
        amount=8_000_000,
        description="Cloud hosting services",
        entity_id=supplier2.id,
    )
    inv3 = Invoice(
        number="INV-2026-003",
        kind="sales",
        status="paid",
        issue_date=today - timedelta(days=45),
        due_date=today - timedelta(days=15),
        amount=50_000_000,
        description="Software development",
        entity_id=client2.id,
    )
    db.add_all([inv1, inv2, inv3])
    db.flush()

    # Add invoice items
    db.add(InvoiceItem(invoice_id=inv1.id, product_name="Consulting", quantity=1, unit_price=75_000_000, line_total=75_000_000))
    db.add(InvoiceItem(invoice_id=inv2.id, product_name="Cloud Hosting", quantity=1, unit_price=8_000_000, line_total=8_000_000))
    db.add(InvoiceItem(invoice_id=inv3.id, product_name="Software Dev", quantity=1, unit_price=50_000_000, line_total=50_000_000))

    db.commit()
    return {
        "entities": [client1, client2, supplier1, supplier2, employee1, bank1],
        "invoices": [inv1, inv2, inv3],
    }


# ---------------------------------------------------------------------------
# Tests: CFO Report Endpoint
# ---------------------------------------------------------------------------
class TestCFOReportEmpty:
    """CFO report on a database with minimal/no financial transactions."""

    def test_cfo_report_returns_200(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        assert resp.status_code == 200

    def test_cfo_report_structure(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        data = resp.json()
        assert "kpis" in data
        assert "insights" in data
        assert "narrative" in data
        assert "risk_score" in data
        assert "runway_months" in data
        assert "burn_rate" in data
        assert "health_grade" in data
        assert data["health_grade"] in ("A", "B", "C", "D", "F")

    def test_cfo_report_kpi_keys(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        kpis = resp.json()["kpis"]
        kpi_keys = {k["key"] for k in kpis}
        expected_keys = {
            "total_revenue", "avg_monthly_revenue", "net_profit", "net_margin",
            "cash_on_hand", "burn_rate", "runway_months",
            "accounts_receivable", "accounts_payable", "expense_trend",
        }
        assert expected_keys.issubset(kpi_keys), f"Missing KPIs: {expected_keys - kpi_keys}"


class TestCFOReportWithData:
    """CFO report with rich financial data."""

    @pytest.fixture(autouse=True)
    def _seed_data(self, db):
        self.data = _create_financial_data(db)
        yield

    def test_revenue_is_positive(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        kpis = {k["key"]: k for k in resp.json()["kpis"]}
        assert kpis["total_revenue"]["value"] > 0, "Revenue should be > 0 with sales transactions"

    def test_expense_is_positive(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        kpis = {k["key"]: k for k in resp.json()["kpis"]}
        # Net profit can be positive or negative, but burn_rate should be > 0
        assert kpis["burn_rate"]["value"] > 0, "Burn rate should be > 0 with expense transactions"

    def test_receivables_are_positive(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        kpis = {k["key"]: k for k in resp.json()["kpis"]}
        assert kpis["accounts_receivable"]["value"] > 0, "AR should be > 0 (last 2 months uncollected)"

    def test_payables_are_positive(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        kpis = {k["key"]: k for k in resp.json()["kpis"]}
        assert kpis["accounts_payable"]["value"] > 0, "AP should be > 0 (purchase on credit)"

    def test_narrative_is_meaningful(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        narrative = resp.json()["narrative"]
        assert len(narrative) > 50, "Narrative should be a substantial text"
        assert "IRR" in narrative, "Narrative should mention currency"

    def test_insights_generated(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        insights = resp.json()["insights"]
        # With growing revenue and expenses, we should get at least a cost driver insight
        assert len(insights) >= 1, "Should generate at least one insight"

    def test_health_grade_is_reasonable(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        grade = resp.json()["health_grade"]
        # With positive revenue and cash, should not be F
        assert grade in ("A", "B", "C", "D"), f"Health grade {grade} seems too harsh for a profitable business"

    def test_runway_is_reasonable(self, auth_client):
        resp = auth_client.get("/brain/cfo/report")
        runway = resp.json()["runway_months"]
        assert runway > 0, "Runway should be positive when cash is positive"


# ---------------------------------------------------------------------------
# Tests: CFO Q&A Endpoint
# ---------------------------------------------------------------------------
class TestCFOQuestionAnswer:
    """Test natural language CFO Q&A."""

    @pytest.fixture(autouse=True)
    def _seed_data(self, db):
        _create_financial_data(db)
        yield

    def test_health_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "Is the business healthy?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "health_grade" in data
        assert len(data["answer"]) > 20

    def test_runway_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "How long can we survive?"})
        assert resp.status_code == 200
        assert "month" in resp.json()["answer"].lower()

    def test_burn_rate_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "What is the burn rate?"})
        assert resp.status_code == 200
        assert "IRR" in resp.json()["answer"]

    def test_cost_driver_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "What is the biggest expense?"})
        assert resp.status_code == 200
        assert len(resp.json()["answer"]) > 10

    def test_cash_leak_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "Where is the cash leak?"})
        assert resp.status_code == 200
        assert "cash" in resp.json()["answer"].lower() or "IRR" in resp.json()["answer"]

    def test_profit_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "Why did profit drop?"})
        assert resp.status_code == 200
        assert len(resp.json()["answer"]) > 10

    def test_default_question_returns_narrative(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "Give me an overview of finances"})
        assert resp.status_code == 200
        assert "IRR" in resp.json()["answer"]

    def test_persian_health_question(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "وضعیت مالی شرکت چطوره؟"})
        assert resp.status_code == 200
        assert len(resp.json()["answer"]) > 20

    def test_short_question_rejected(self, auth_client):
        resp = auth_client.post("/brain/cfo/ask", json={"question": "hi"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: Entity Filtering and Search
# ---------------------------------------------------------------------------
class TestEntityFilterAndSearch:
    """Test entity listing with type filter and name search."""

    @pytest.fixture(autouse=True)
    def _seed_data(self, db):
        self.data = _create_financial_data(db)
        yield

    def test_list_all_entities(self, auth_client):
        resp = auth_client.get("/entities")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 6  # We created 6 entities

    def test_filter_by_type_client(self, auth_client):
        resp = auth_client.get("/entities?type=client")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 2
        assert all(e["type"] == "client" for e in entities)

    def test_filter_by_type_supplier(self, auth_client):
        resp = auth_client.get("/entities?type=supplier")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 2
        assert all(e["type"] == "supplier" for e in entities)

    def test_filter_by_type_bank(self, auth_client):
        resp = auth_client.get("/entities?type=bank")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 1
        assert all(e["type"] == "bank" for e in entities)

    def test_search_by_name(self, auth_client):
        resp = auth_client.get("/entities?search=Innotech")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 1
        assert any("Innotech" in e["name"] for e in entities)

    def test_search_case_insensitive(self, auth_client):
        resp = auth_client.get("/entities?search=innotech")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 1

    def test_filter_and_search_combined(self, auth_client):
        resp = auth_client.get("/entities?type=client&search=Data")
        assert resp.status_code == 200
        entities = resp.json()
        assert len(entities) >= 1
        assert all(e["type"] == "client" for e in entities)
        assert any("DataFlow" in e["name"] for e in entities)

    def test_search_no_results(self, auth_client):
        resp = auth_client.get("/entities?search=NonExistentCompany12345")
        assert resp.status_code == 200
        assert resp.json() == []
