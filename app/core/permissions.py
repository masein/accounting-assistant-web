"""Role-based access control (RBAC) — the single, central, default-deny guard.

Roles live *within* a company (they pair with the tenant `company_id` filter in
`app/db/tenant.py`; every check is company AND role). `is_superadmin` stays
orthogonal — it's a platform-level flag, not a company role.

Enforcement is deliberately NOT ad-hoc per route. Instead:

* `ROLE_PERMISSIONS` maps each role to the set of capabilities it holds.
* `ROUTE_PERMISSIONS` maps every `(HTTP method, route path template)` to the
  permission(s) that unlock it.
* `enforce_route_permission` is one dependency attached to every business router
  (`include_router(..., dependencies=[Depends(enforce_route_permission)])`). It
  runs after routing, so `request.scope["route"].path` is the templated path
  (e.g. `/transactions/{transaction_id}`), which it looks up in the table.

Default-deny: a route with no mapping is denied to everyone except the Owner
(and superadmin). So forgetting to map a new endpoint fails closed, never open.

Object-level ownership (an Employee may read only their *own* time / expenses /
payslips) and sensitive-field stripping (salary, bank numbers) are enforced in
the data layer on top of this — this module is the route-level gate.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.auth import SessionUser, get_current_user


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
class Role:
    OWNER = "owner"
    CFO = "cfo"
    ACCOUNTANT = "accountant"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    VIEWER = "viewer"


ALL_ROLES = (Role.OWNER, Role.CFO, Role.ACCOUNTANT, Role.MANAGER, Role.EMPLOYEE, Role.VIEWER)
DEFAULT_ROLE = Role.OWNER  # the pre-RBAC single company login becomes the Owner


# ---------------------------------------------------------------------------
# Permissions (capability:action)
# ---------------------------------------------------------------------------
class Perm:
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    USERS_MANAGE = "users:manage"
    BOOKS_READ = "books:read"
    BOOKS_WRITE = "books:write"
    PAYROLL_READ = "payroll:read"
    PAYROLL_WRITE = "payroll:write"
    PAYROLL_OWN = "payroll:own"        # an employee's own payslip only
    BANK_READ = "bank:read"            # bank account numbers / balances
    REPORTS_READ = "reports:read"      # dashboard + standard/manager reports
    REPORTS_LIMITED = "reports:limited"  # the slice a Manager may see
    CFO_READ = "cfo:read"              # CFO / CEO mode
    APPROVALS_WRITE = "approvals:write"
    TIME_OWN = "time:own"              # log/view own time
    EXPENSES_OWN = "expenses:own"      # submit/view own expenses


ALL_PERMS = frozenset(
    v for k, v in vars(Perm).items() if not k.startswith("_") and isinstance(v, str)
)

# Any authenticated user with a known role may hit these harmless lookups
# (currency metadata, chart of accounts, payment methods) — the forms need them.
ANY_ROLE = "*"


# ---------------------------------------------------------------------------
# Role -> permissions
# ---------------------------------------------------------------------------
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # Owner: everything within the company.
    Role.OWNER: ALL_PERMS,
    # CFO: full books + payroll + all reports incl CFO/CEO + approvals; reads
    # settings; NO user management, NO settings write.
    Role.CFO: frozenset({
        Perm.SETTINGS_READ,
        Perm.BOOKS_READ, Perm.BOOKS_WRITE,
        Perm.PAYROLL_READ, Perm.PAYROLL_WRITE,
        Perm.BANK_READ,
        Perm.REPORTS_READ, Perm.REPORTS_LIMITED,
        Perm.CFO_READ,
        Perm.APPROVALS_WRITE,
    }),
    # Accountant: full books + payroll (read/write) + standard reports; NO CFO
    # mode, NO user management, NO settings write, NO approvals.
    Role.ACCOUNTANT: frozenset({
        Perm.BOOKS_READ, Perm.BOOKS_WRITE,
        Perm.PAYROLL_READ, Perm.PAYROLL_WRITE,
        Perm.BANK_READ,
        Perm.REPORTS_READ, Perm.REPORTS_LIMITED,
    }),
    # Manager/Approver: act on over-threshold items + a limited report slice.
    Role.MANAGER: frozenset({
        Perm.APPROVALS_WRITE,
        Perm.REPORTS_LIMITED,
    }),
    # Employee: self-service only — own time, own expenses, own payslip.
    Role.EMPLOYEE: frozenset({
        Perm.TIME_OWN,
        Perm.EXPENSES_OWN,
        Perm.PAYROLL_OWN,
    }),
    # Viewer: read-only reports/dashboard (sensitive fields stripped downstream).
    Role.VIEWER: frozenset({
        Perm.REPORTS_READ, Perm.REPORTS_LIMITED,
    }),
}


def role_permissions(role: str | None) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role or DEFAULT_ROLE, frozenset())


def role_can(role: str | None, required) -> bool:
    """True if `role` satisfies `required` (a single perm, an iterable of perms
    meaning any-of, or the ANY_ROLE sentinel meaning any known role)."""
    if required == ANY_ROLE:
        return (role or DEFAULT_ROLE) in ROLE_PERMISSIONS
    held = role_permissions(role)
    if isinstance(required, str):
        return required in held
    return any(p in held for p in required)  # any-of


# ---------------------------------------------------------------------------
# Route -> permission(s)
# ---------------------------------------------------------------------------
# Value is a single Perm, a frozenset of Perms (any-of), or ANY_ROLE.
# Keys are (METHOD, exact route path template as FastAPI sees it).
_READ_LOOKUPS = ANY_ROLE  # tiny read-only lookups every form needs

ROUTE_PERMISSIONS: dict[tuple[str, str], object] = {}


def _add(method: str, path: str, perm) -> None:
    ROUTE_PERMISSIONS[(method.upper(), path)] = perm


def _reads(paths, perm):
    for p in paths:
        _add("GET", p, perm)


# --- Company settings & branding -------------------------------------------
_add("GET", "/admin/company-profile", Perm.SETTINGS_READ)
_add("GET", "/admin/company-profile/logo", ANY_ROLE)  # logo is shown in every header
_add("PUT", "/admin/company-profile", Perm.SETTINGS_WRITE)
_add("POST", "/admin/company-profile/logo", Perm.SETTINGS_WRITE)
_add("POST", "/admin/company-profile/signature", Perm.SETTINGS_WRITE)
for _p in ("/admin/reporting-locale", "/admin/display-calendar",
           "/admin/iran-shares-outstanding", "/admin/closed-period",
           "/admin/chat-provider-shape"):
    _add("GET", _p, Perm.SETTINGS_READ)
    _add("PUT", _p, Perm.SETTINGS_WRITE)
# AI provider config is an owner-level setting.
_add("GET", "/admin/ai-config", Perm.SETTINGS_READ)
_add("PATCH", "/admin/ai-config", Perm.SETTINGS_WRITE)
_add("GET", "/admin/anthropic-config", Perm.SETTINGS_READ)
_add("PATCH", "/admin/anthropic-config", Perm.SETTINGS_WRITE)
_add("POST", "/admin/reset-db", Perm.SETTINGS_WRITE)  # destructive, owner-only

# --- User management (Owner only) ------------------------------------------
_add("GET", "/admin/users", Perm.USERS_MANAGE)
_add("POST", "/admin/users", Perm.USERS_MANAGE)
_add("PATCH", "/admin/users/{user_id}", Perm.USERS_MANAGE)
_add("DELETE", "/admin/users/{user_id}", Perm.USERS_MANAGE)

# --- Books: chart of accounts (read-only lookups) --------------------------
_reads(["/accounts", "/accounts/by-code/{code}", "/accounts/{account_id}"],
       frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))

# --- Books: transactions ----------------------------------------------------
_reads(["/transactions", "/transactions/{transaction_id}",
        "/transactions/fees/methods", "/transactions/fees"],
       frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))
_add("POST", "/transactions/fees/calculate", frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))
for _m, _p in [
    ("POST", "/transactions"), ("PATCH", "/transactions/{transaction_id}"),
    ("DELETE", "/transactions/{transaction_id}"),
    ("POST", "/transactions/attachments"),
    ("DELETE", "/transactions/attachments/{attachment_id}"),
    ("POST", "/transactions/attachments/{attachment_id}/ocr"),
    ("POST", "/transactions/chat"), ("POST", "/transactions/suggest"),
    ("PUT", "/transactions/fees"),
    ("POST", "/transactions/import"),
    ("POST", "/transactions/excel-import/preview"),
    ("POST", "/transactions/excel-import/confirm"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: invoices --------------------------------------------------------
_reads(["/invoices", "/invoices/{invoice_id}/payments",
        "/invoices/{invoice_id}/credit-notes",
        "/invoices/{invoice_id}/payments/{payment_id}/receipt",
        "/invoices/{invoice_id}/timeline", "/invoices/{invoice_id}/pdf"],
       frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))
for _m, _p in [
    ("POST", "/invoices/ocr-import"), ("POST", "/invoices/preview-pdf"),
    ("POST", "/invoices"), ("PATCH", "/invoices/{invoice_id}"),
    ("DELETE", "/invoices/{invoice_id}"),
    ("POST", "/invoices/{invoice_id}/payments"),
    ("POST", "/invoices/{invoice_id}/credit-notes"),
    ("POST", "/invoices/{invoice_id}/void"),
    ("POST", "/invoices/{invoice_id}/payments/{payment_id}/reverse"),
    ("POST", "/invoices/{invoice_id}/mark-paid"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: entities --------------------------------------------------------
_reads(["/entities", "/entities/{entity_id}", "/entities/{entity_id}/statement.pdf"],
       frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))
for _m, _p in [
    ("POST", "/entities/resolve"), ("POST", "/entities"),
    ("PATCH", "/entities/{entity_id}"), ("DELETE", "/entities/{entity_id}"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: recurring rules -------------------------------------------------
_reads(["/recurring"], Perm.BOOKS_READ)
for _m, _p in [("POST", "/recurring"), ("POST", "/recurring/from-text"),
               ("PATCH", "/recurring/{rule_id}"), ("DELETE", "/recurring/{rule_id}")]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: adjustments (accrual / prepayment / depreciation) --------------
_reads(["/adjustments", "/adjustments/{adjustment_id}"], Perm.BOOKS_READ)
for _m, _p in [
    ("POST", "/adjustments/accrual"), ("POST", "/adjustments/prepayment"),
    ("POST", "/adjustments/depreciation"),
    ("POST", "/adjustments/{adjustment_id}/release"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: FX --------------------------------------------------------------
_reads(["/fx/metadata", "/fx/reporting-currency", "/fx/rates"], _READ_LOOKUPS)
_add("POST", "/fx/convert", _READ_LOOKUPS)
_add("PUT", "/fx/reporting-currency", Perm.SETTINGS_WRITE)
for _m, _p in [("POST", "/fx/rates"), ("DELETE", "/fx/rates/{rate_id}"),
               ("POST", "/fx/revalue")]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: products / catalog (read-only analytics) -----------------------
_reads(["/products/catalog", "/products/detail/{product_name}",
        "/products/entity-matrix", "/products/profitability"],
       frozenset({Perm.BOOKS_READ, Perm.REPORTS_READ}))

# --- Books: purchase orders -------------------------------------------------
_reads(["/purchase-orders", "/purchase-orders/{po_id}", "/purchase-orders/{po_id}/pdf"],
       Perm.BOOKS_READ)
for _m, _p in [
    ("POST", "/purchase-orders"), ("PATCH", "/purchase-orders/{po_id}"),
    ("POST", "/purchase-orders/{po_id}/receipts"),
    ("POST", "/purchase-orders/{po_id}/match"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: AI accountant ---------------------------------------------------
_reads(["/ai-accountant/sessions", "/ai-accountant/sessions/{session_id}/messages",
        "/ai-accountant/proposals/{token}"], Perm.BOOKS_READ)
for _m, _p in [
    ("POST", "/ai-accountant/chat"), ("POST", "/ai-accountant/execute"),
    ("POST", "/ai-accountant/undo"), ("POST", "/ai-accountant/reverse"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Books: bank statements & reconcile (brain) ----------------------------
# Reads expose bank account numbers -> BANK_READ.
_reads(["/brain/bank-statements", "/brain/bank-statements/{statement_id}",
        "/brain/ocr-health"], Perm.BANK_READ)
for _m, _p in [
    ("POST", "/brain/bank-statements/upload"),
    ("POST", "/brain/bank-statements/{statement_id}/reconcile"),
    ("POST", "/brain/bank-statements/{statement_id}/approve"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)
# Audit views (who did what) — books-level read.
_reads(["/brain/audit/report", "/brain/audit/logs", "/brain/audit/integrity-history",
        "/brain/audit/versions/{transaction_id}"], Perm.BOOKS_READ)
_add("GET", "/brain/settings/{key}", Perm.SETTINGS_READ)
_add("POST", "/brain/settings", Perm.SETTINGS_WRITE)
_add("POST", "/brain/cfo/seed-sample-data", Perm.SETTINGS_WRITE)

# --- CFO / CEO mode ---------------------------------------------------------
_add("GET", "/brain/cfo/report", Perm.CFO_READ)
_add("GET", "/brain/ceo/report", Perm.CFO_READ)
_add("POST", "/brain/cfo/ask", Perm.CFO_READ)

# --- Payroll / salaries -----------------------------------------------------
_reads(["/payroll/profiles", "/payroll/runs", "/payroll/runs/{run_id}",
        "/payroll/year-summary", "/payroll/hours-summary"], Perm.PAYROLL_READ)
# Payslip: books payroll people OR the employee's own (object-checked downstream).
_reads(["/payroll/runs/{run_id}/payslip/{entity_id}",
        "/payroll/runs/{run_id}/payslip/{entity_id}/pdf"],
       frozenset({Perm.PAYROLL_READ, Perm.PAYROLL_OWN}))
for _m, _p in [
    ("POST", "/payroll/profiles"), ("POST", "/payroll/runs"),
    ("POST", "/payroll/runs/{run_id}/post"), ("POST", "/payroll/runs/{run_id}/pay"),
    ("POST", "/payroll/runs/{run_id}/void"),
    ("POST", "/payroll/prorate-raise"),
]:
    _add(_m, _p, Perm.PAYROLL_WRITE)
# Self-service timesheet summary (own hours; payroll people may pass entity_id).
_add("GET", "/time/my-summary", frozenset({Perm.TIME_OWN, Perm.PAYROLL_READ}))

# --- Reports (dashboard, ledger, manager) ----------------------------------
_reads([
    "/reports/ledger-summary", "/reports/accounts/{account_code}/detail",
    "/reports/entities/{entity_id}/transactions", "/reports/owner-dashboard",
    "/reports/tax-summary", "/reports/tax-rates", "/reports/tax-rates/effective",
    "/reports/missing-references", "/reports/transactions/search",
], Perm.REPORTS_READ)
_add("POST", "/reports/tax-rates", Perm.BOOKS_WRITE)
# Budgets — reporting/planning reads + books writes.
_reads(["/budgets", "/budgets/actual-vs-budget"], Perm.REPORTS_READ)
_add("POST", "/budgets", Perm.BOOKS_WRITE)
_add("DELETE", "/budgets/{budget_id}", Perm.BOOKS_WRITE)
# Exports of the books/reports.
_reads(["/exports/transactions.csv", "/exports/transactions.xlsx"], Perm.REPORTS_READ)
_add("POST", "/exports/monthly-snapshot", Perm.BOOKS_WRITE)

# Manager-reports: financial statements + books + operational + inventory + sales.
_MR_READ = [
    "/manager-reports/financial/balance-sheet", "/manager-reports/financial/income-statement",
    "/manager-reports/financial/cash-flow", "/manager-reports/financial/cash-flow-periods",
    "/manager-reports/financial/balance-sheet-periods",
    "/manager-reports/financial/iran/income-statement", "/manager-reports/financial/iran/balance-sheet",
    "/manager-reports/financial/iran/changes-in-equity", "/manager-reports/financial/iran/comprehensive-income",
    "/manager-reports/financial/iran/cash-flow",
    "/manager-reports/financial/uk/balance-sheet", "/manager-reports/financial/uk/profit-and-loss",
    "/manager-reports/financial/uk/comprehensive-income", "/manager-reports/financial/uk/changes-in-equity",
    "/manager-reports/financial/uk/cash-flow",
    "/manager-reports/accounts/list",
    "/manager-reports/books/general-journal", "/manager-reports/books/general-ledger",
    "/manager-reports/books/account-ledger/{account_code}", "/manager-reports/books/trial-balance",
    "/manager-reports/books/trial-balance-by-currency",
    "/manager-reports/operational/debtor-creditor", "/manager-reports/operational/accounts-payable",
    "/manager-reports/operational/accounts-receivable", "/manager-reports/operational/person-running-balance",
    "/manager-reports/operational/cash-bank-statement",
    "/manager-reports/inventory/items", "/manager-reports/inventory/movements",
    "/manager-reports/inventory/balance",
    "/manager-reports/sales/trend", "/manager-reports/sales/by-product", "/manager-reports/sales/by-invoice",
    "/manager-reports/purchases/by-product", "/manager-reports/purchases/by-invoice",
    "/manager-reports/journal/entries",
    "/manager-reports/entities/search", "/manager-reports/products/names",
]
_reads(_MR_READ, Perm.REPORTS_READ)
for _m, _p in [
    ("POST", "/manager-reports/inventory/items"),
    ("POST", "/manager-reports/inventory/movements"),
    ("PATCH", "/manager-reports/inventory/items/{item_id}/price"),
    ("POST", "/manager-reports/journal/register"),
    ("PATCH", "/manager-reports/journal/{transaction_id}"),
    ("POST", "/manager-reports/journal/{transaction_id}/reverse"),
]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Approvals + expenses ---------------------------------------------------
# The approval inbox / expense list: approvers, books people, or the owner-employee.
_reads(["/expenses", "/expenses/{claim_id}"],
       frozenset({Perm.EXPENSES_OWN, Perm.APPROVALS_WRITE, Perm.BOOKS_READ}))
_add("GET", "/expenses/settings", frozenset({Perm.BOOKS_READ, Perm.APPROVALS_WRITE}))
_add("POST", "/expenses/settings", Perm.SETTINGS_WRITE)
_add("POST", "/expenses/mileage", Perm.EXPENSES_OWN)
_add("POST", "/expenses/{claim_id}/approve", Perm.APPROVALS_WRITE)
_add("POST", "/expenses/{claim_id}/reject", Perm.APPROVALS_WRITE)
_add("POST", "/expenses/{claim_id}/reimburse", Perm.BOOKS_WRITE)

# --- Time tracking / time-billing ------------------------------------------
# Reads: the employee (own) or books people (all, to bill).
_reads(["/time/projects", "/time/rates", "/time/entries", "/time/unbilled"],
       frozenset({Perm.TIME_OWN, Perm.BOOKS_READ}))
_add("GET", "/time/invoice/{invoice_id}/pdf", frozenset({Perm.TIME_OWN, Perm.BOOKS_READ}))
# Logging own time.
for _m, _p in [("POST", "/time/entries"), ("PATCH", "/time/entries/{entry_id}"),
               ("DELETE", "/time/entries/{entry_id}")]:
    _add(_m, _p, frozenset({Perm.TIME_OWN, Perm.BOOKS_WRITE}))
# Billing/setup is a books activity.
for _m, _p in [("POST", "/time/projects"), ("POST", "/time/rates"),
               ("POST", "/time/entries/{entry_id}/write-off"),
               ("POST", "/time/invoice-preview"), ("POST", "/time/invoice")]:
    _add(_m, _p, Perm.BOOKS_WRITE)

# --- Notifications trigger + low-cash digest (Owner + CFO) ------------------
_add("POST", "/notifications/check", Perm.REPORTS_READ)
_add("GET", "/notifications/digest-settings", frozenset({Perm.SETTINGS_READ, Perm.CFO_READ}))
_add("PUT", "/notifications/digest-settings", Perm.SETTINGS_WRITE)
_add("POST", "/notifications/daily-digest", Perm.CFO_READ)


# ---------------------------------------------------------------------------
# Resolve a concrete request path (e.g. /transactions/abc-123) back to its
# templated table key (/transactions/{transaction_id}).
# ---------------------------------------------------------------------------
# `scope["route"]` is not populated by Starlette and this FastAPI version wraps
# include_router so leaf routes can't be introspected at startup — so we match
# the concrete path against our own templates, most-specific first.
import re as _re

_COMPILED: list[tuple] = []


def _compile_templates() -> None:
    _COMPILED.clear()
    seen = set()
    for (method, template) in ROUTE_PERMISSIONS:
        if (method, template) in seen:
            continue
        seen.add((method, template))
        segs = [s for s in template.split("/") if s]
        n_params = sum(1 for s in segs if s.startswith("{"))
        n_static = len(segs) - n_params
        regex = _re.compile("^" + _re.sub(r"\{[^}]+\}", r"[^/]+", template) + "$")
        # Sort key: most static segments, then fewest params, then longest.
        _COMPILED.append(((n_static, -n_params, len(template)), method, template, regex))
    _COMPILED.sort(key=lambda t: t[0], reverse=True)


def resolve_template(method: str, path: str) -> str | None:
    """Map a concrete path to the matching table template, or None."""
    method = method.upper()
    if (method, path) in ROUTE_PERMISSIONS:  # exact/static hit
        return path
    for _key, m, template, regex in _COMPILED:
        if m == method and regex.match(path):
            return template
    return None


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
def user_can_access(user: SessionUser, method: str, path: str) -> bool:
    """Pure check (no request) — used by the guard and by tests. `path` may be a
    concrete path or an exact template key."""
    if getattr(user, "is_superadmin", False):
        return True
    role = getattr(user, "role", None) or DEFAULT_ROLE
    template = resolve_template(method, path)
    required = ROUTE_PERMISSIONS.get((method.upper(), template)) if template else None
    if required is None:
        return role == Role.OWNER  # default-deny: only the Owner
    return role_can(role, required)


# ---------------------------------------------------------------------------
# Object-level ownership
# ---------------------------------------------------------------------------
def own_scope(user, full_perm) -> tuple[bool, str | None]:
    """Decide whether a caller is restricted to their OWN records for a domain.

    Returns ``(restricted, own_entity_id)``:
      * ``restricted=False`` — the caller holds the domain's full read
        permission (or is superadmin): they see everything in the company.
      * ``restricted=True`` — a self-service caller (e.g. an Employee with only
        the ``*_OWN`` permission): they may see only rows whose owner entity ==
        ``own_entity_id`` (their linked ``User.entity_id``; ``None`` if unlinked,
        which then matches nothing).
    """
    if user is None or getattr(user, "is_superadmin", False):
        return (False, None)
    role = getattr(user, "role", None) or DEFAULT_ROLE
    if role_can(role, full_perm):
        return (False, None)
    return (True, getattr(user, "entity_id", None))


def enforce_route_permission(request: Request, user: SessionUser = Depends(get_current_user)) -> SessionUser:
    """Router-level dependency: default-deny RBAC for the matched route."""
    if not user_can_access(user, request.method, request.url.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to perform this action",
        )
    return user


_compile_templates()
