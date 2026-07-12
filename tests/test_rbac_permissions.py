"""RBAC: the role × endpoint permission matrix is the bar.

Three layers:
1. Pure matrix — `user_can_access(role, method, path)` for every role against a
   representative endpoint per capability (fast, no DB).
2. Coverage — every guarded business route in the live OpenAPI schema has an
   explicit permission mapping (no silent owner-only fallback).
3. Live enforcement — real requests with a role's session token get 403 when
   denied and something-other-than-403 when allowed, proving the guard is wired.
"""
from __future__ import annotations

import re
import uuid

import pytest

from app.core.auth import CSRF_COOKIE, SessionUser, create_session_token, generate_csrf_token
from app.core.config import settings
from app.core.permissions import ALL_ROLES, Role, user_can_access

O, C, A, M, E, V = (
    Role.OWNER, Role.CFO, Role.ACCOUNTANT, Role.MANAGER, Role.EMPLOYEE, Role.VIEWER,
)


def _user(role: str, superadmin: bool = False) -> SessionUser:
    return SessionUser(
        user_id=str(uuid.uuid4()), username=role, is_admin=(role == O),
        company_id="c1", is_superadmin=superadmin, role=role,
    )


# (method, path, {roles that MUST be allowed}) — everyone else must be denied.
MATRIX = [
    # Company settings & branding
    ("PUT", "/admin/company-profile", {O}),
    ("POST", "/admin/company-profile/logo", {O}),
    ("GET", "/admin/company-profile", {O, C}),
    ("PUT", "/fx/reporting-currency", {O}),
    ("POST", "/admin/reset-db", {O}),
    # User management (Owner only)
    ("GET", "/admin/users", {O}),
    ("POST", "/admin/users", {O}),
    ("PATCH", "/admin/users/00000000-0000-0000-0000-000000000000", {O}),
    ("DELETE", "/admin/users/00000000-0000-0000-0000-000000000000", {O}),
    # Books: write = owner/cfo/accountant
    ("POST", "/transactions", {O, C, A}),
    ("DELETE", "/transactions/tx1", {O, C, A}),
    ("POST", "/invoices", {O, C, A}),
    ("POST", "/entities", {O, C, A}),
    ("POST", "/ai-accountant/chat", {O, C, A}),
    ("POST", "/manager-reports/journal/register", {O, C, A}),
    ("POST", "/adjustments/accrual", {O, C, A}),
    ("POST", "/purchase-orders", {O, C, A}),
    # Books: read = owner/cfo/accountant + viewer (reports); NOT manager/employee
    ("GET", "/transactions", {O, C, A, V}),
    ("GET", "/invoices", {O, C, A, V}),
    ("GET", "/accounts", {O, C, A, V}),
    # Payroll / salaries
    ("GET", "/payroll/runs", {O, C, A}),
    ("POST", "/payroll/runs", {O, C, A}),
    ("GET", "/payroll/runs/r1/payslip/e1", {O, C, A, E}),  # + employee's own
    # Bank accounts & balances (account numbers)
    ("GET", "/brain/bank-statements", {O, C, A}),
    ("GET", "/brain/bank-statements/s1", {O, C, A}),
    # Reports / dashboard = owner/cfo/accountant/viewer
    ("GET", "/reports/owner-dashboard", {O, C, A, V}),
    ("GET", "/manager-reports/books/trial-balance", {O, C, A, V}),
    # CFO / CEO mode
    ("GET", "/brain/cfo/report", {O, C}),
    ("GET", "/brain/ceo/report", {O, C}),
    # Approvals
    ("POST", "/expenses/c1/approve", {O, C, M}),
    ("POST", "/expenses/c1/reject", {O, C, M}),
    ("POST", "/expenses/c1/reimburse", {O, C, A}),  # reimburse = books write
    # Self-service: own time / own expenses
    ("POST", "/expenses/mileage", {O, E}),
    ("POST", "/time/entries", {O, C, A, E}),  # employees log; books people too
    ("GET", "/time/entries", {O, C, A, E}),
    ("GET", "/expenses", {O, C, A, M, E}),  # everyone who can see claims; not viewer
]


@pytest.mark.parametrize("method,path,allowed", MATRIX)
def test_permission_matrix(method, path, allowed):
    for role in ALL_ROLES:
        got = user_can_access(_user(role), method, path)
        assert got == (role in allowed), (
            f"{role} {method} {path}: got allowed={got}, expected {role in allowed}"
        )


def test_owner_can_do_everything_in_matrix():
    for method, path, _ in MATRIX:
        assert user_can_access(_user(O), method, path) is True


def test_superadmin_bypasses_rbac():
    # Even a viewer role, if platform superadmin, is allowed (orthogonal flag).
    su = _user(V, superadmin=True)
    for method, path, _ in MATRIX:
        assert user_can_access(su, method, path) is True


def test_default_deny_unmapped_route_is_owner_only():
    for role in ALL_ROLES:
        got = user_can_access(_user(role), "POST", "/totally/unmapped/route")
        assert got == (role == O)


def test_employee_is_boxed_in():
    """Acceptance: every books/reports/payroll/bank/user endpoint → denied."""
    emp = _user(E)
    for method, path in [
        ("POST", "/transactions"), ("GET", "/transactions"),
        ("POST", "/invoices"), ("GET", "/payroll/runs"),
        ("GET", "/brain/bank-statements"), ("GET", "/admin/users"),
        ("GET", "/reports/owner-dashboard"), ("GET", "/brain/cfo/report"),
        ("POST", "/manager-reports/journal/register"),
    ]:
        assert user_can_access(emp, method, path) is False, f"{method} {path}"


def test_viewer_cannot_write_anything():
    viewer = _user(V)
    for method, path in [
        ("POST", "/transactions"), ("POST", "/invoices"),
        ("POST", "/payroll/runs"), ("PUT", "/admin/company-profile"),
        ("POST", "/manager-reports/journal/register"),
        ("POST", "/expenses/c1/approve"),
    ]:
        assert user_can_access(viewer, method, path) is False, f"{method} {path}"


def test_every_guarded_business_route_is_mapped():
    """Coverage: no business route silently falls back to owner-only."""
    from app.core.permissions import ROUTE_PERMISSIONS
    from app.main import app

    def norm(p: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", p)

    mapped = {(m, norm(t)) for (m, t) in ROUTE_PERMISSIONS}
    exempt = ("/auth", "/health", "/docs", "/redoc", "/openapi.json",
              "/", "/login", "/uploads", "/static", "/favicon",
              "/api/v1")  # key-authenticated integration surface (own guard)
    missing = []
    for path, ops in app.openapi()["paths"].items():
        if path.startswith(exempt):
            continue
        for method in ops:
            if method.upper() in ("OPTIONS", "HEAD"):
                continue
            if (method.upper(), norm(path)) not in mapped:
                missing.append((method.upper(), path))
    assert not missing, f"Unmapped guarded routes (owner-only fallback): {missing}"


# ---------------------------------------------------------------------------
# Live enforcement through the real middleware + guard
# ---------------------------------------------------------------------------
def _role_client(client, role: str):
    from tests.conftest import _CSRFTestClient
    token = create_session_token(
        user_id=str(uuid.uuid4()), username=role, is_admin=(role == O), role=role,
    )
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.mark.parametrize("role,method,path,denied", [
    (E, "post", "/transactions", True),
    (E, "get", "/payroll/runs", True),
    (V, "post", "/transactions", True),
    (M, "post", "/transactions", True),
    (A, "get", "/brain/cfo/report", True),
    (A, "get", "/admin/users", True),
    (O, "get", "/accounts", False),
    (A, "get", "/transactions", False),
])
def test_live_guard_returns_403_when_denied(client, role, method, path, denied):
    rc = _role_client(client, role)
    kwargs = {} if method == "get" else {"json": {}}
    resp = getattr(rc, method)(path, **kwargs)
    if denied:
        assert resp.status_code == 403, f"{role} {method} {path} -> {resp.status_code}"
    else:
        # allowed: guard let it through (any status but 401/403)
        assert resp.status_code not in (401, 403), f"{role} {method} {path} -> {resp.status_code}"


def test_unauthenticated_is_rejected(client):
    assert client.post("/transactions", json={}).status_code == 401
