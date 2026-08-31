"""Request/response models for the financial-brain endpoints.

Bank-statement ingestion and reconciliation, the audit views, and the CFO/CEO
reports. These lived inline in app/api/brain.py; every other router in the app
keeps its models under app/schemas, and the router is easier to read as a list
of handlers.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class BankStatementUploadResponse(BaseModel):
    id: UUID | None = None
    status: str
    total_rows: int
    bank_name: str
    source_type: str
    errors: list[str] = Field(default_factory=list)
    # Number of malformed rows skipped during import (the rest still imported).
    skipped_rows: int = 0
    # Set when the file's columns couldn't be auto-detected: the UI shows a
    # mapping step. `headers` are the detected column names; `required_fields`
    # the roles that must be mapped.
    needs_mapping: bool = False
    headers: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    # Set when an identical file (same content hash) was already imported.
    duplicate: bool = False
    duplicate_of: UUID | None = None
    # Rows that were individually recognised as already imported (overlapping
    # date ranges), flagged rather than posted a second time.
    duplicate_rows: int = 0


class BankStatementRowRead(BaseModel):
    id: UUID
    row_index: int
    tx_date: date
    description: str | None
    reference: str | None
    debit: int
    credit: int
    balance: int | None
    counterparty: str | None
    confidence: float
    category: str | None
    suggested_account_code: str | None
    recon_status: str
    matched_transaction_id: UUID | None
    user_approved: bool


class BankStatementRead(BaseModel):
    id: UUID
    bank_name: str
    account_number: str | None
    source_type: str
    source_filename: str
    currency: str
    from_date: date | None
    to_date: date | None
    status: str
    total_rows: int
    matched_rows: int
    new_rows: int
    rows: list[BankStatementRowRead] = Field(default_factory=list)


class FeeSuggestion(BaseModel):
    """An unmatched bank line that looks like a bank fee or interest, offered
    as a one-click confirm-gated posting (never auto-posted)."""
    row_id: UUID
    row_index: int
    tx_date: date
    description: str | None
    amount: int  # positive minor units
    direction: str  # "debit" (fee/charge out) or "credit" (interest in)
    kind: str  # "bank_fee" or "interest_income"
    account_code: str
    account_name: str


class ReconcileResponse(BaseModel):
    total_rows: int
    matched: int
    partial: int
    unmatched: int
    duplicates: int
    auto_matched: int
    missing_in_bank: int
    # Net unreconciled difference in minor units: sum of bank rows that didn't
    # match a ledger transaction. We report it exactly — never force-balance.
    unreconciled_difference: int = 0
    currency: str = "IRR"
    fee_suggestions: list[FeeSuggestion] = Field(default_factory=list)


class RowApproval(BaseModel):
    row_id: UUID
    action: str = Field(..., description="approve, reject, skip, create")
    account_code: str | None = None


class BatchApprovalRequest(BaseModel):
    approvals: list[RowApproval]


class BatchApprovalResponse(BaseModel):
    approved: int
    rejected: int
    skipped: int
    created: int
    errors: list[str] = Field(default_factory=list)


class AuditFindingRead(BaseModel):
    severity: str
    category: str
    title: str
    detail: str
    entity_id: str | None = None
    amount: int | None = None
    domain: str = "financial"
    verification_status: str = "pending"


class AuditReportResponse(BaseModel):
    integrity_score: int
    health_score: int
    findings: list[AuditFindingRead]
    checks_passed: int
    checks_failed: int
    total_transactions: int
    liability_total: int = 0
    liability_threshold: int = 0


class AuditLogRead(BaseModel):
    id: UUID
    timestamp: str
    action: str
    entity_type: str
    entity_id: str | None
    username: str | None
    actor_role: str | None = None
    detail: str | None


class CFOKpiRead(BaseModel):
    key: str
    label: str
    value: float | int
    unit: str
    trend: str
    trend_pct: float
    risk_level: str


class CFOInsightRead(BaseModel):
    priority: int
    category: str
    title: str
    body: str
    severity: str


class CFOReportResponse(BaseModel):
    kpis: list[CFOKpiRead]
    insights: list[CFOInsightRead]
    narrative: str
    risk_score: int
    runway_months: float
    burn_rate: int
    health_grade: str
    # The currency unit applied to every monetary KPI on this report.
    # Resolved server-side from the active reporting-currency AppSetting
    # so the frontend renders the right unit (e.g. £ vs IRR) without
    # second-guessing.
    currency: str = "IRR"


class CEOReportResponse(BaseModel):
    revenue_total: int
    revenue_trend: float
    profit_total: int
    profit_margin: float
    cash_position: int
    cash_runway_months: float
    burn_rate: int
    health_grade: str
    risk_score: int
    total_assets: int
    total_liabilities: int
    total_equity: int
    assets_breakdown: list[dict] = []
    liabilities_breakdown: list[dict] = []
    equity_breakdown: list[dict] = []
    monthly_revenue: list[dict]
    monthly_expenses: list[dict]
    monthly_profit: list[dict]
    top_expenses: list[dict]
    alerts: list[dict]
    accounts_receivable: int
    accounts_payable: int
    liability_ratio: float
    # Active reporting currency (same resolver as CFOReportResponse.currency).
    currency: str = "IRR"


class CFOQuestionRequest(BaseModel):
    question: str = Field(..., min_length=3)


class CFOQuestionResponse(BaseModel):
    question: str
    answer: str
    health_grade: str
    risk_score: int


class TransactionVersionRead(BaseModel):
    id: UUID
    transaction_id: str
    version: int
    action: str
    snapshot: str
    created_at: str


class SettingPayload(BaseModel):
    key: str
    value: str


class SeedDataResponse(BaseModel):
    transactions_created: int
    invoices_created: int
    entities_created: int
    inventory_items_created: int
    recurring_rules_created: int
    budget_limits_created: int
    bank_statement_rows: int
    message: str
