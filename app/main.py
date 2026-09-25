from contextlib import asynccontextmanager
import hashlib
import logging
from pathlib import Path
import re
import time
import uuid

from fastapi import Depends, FastAPI
from starlette.concurrency import run_in_threadpool
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.accounts import router as accounts_router
from app.api.adjustments import router as adjustments_router
from app.api.admin import router as admin_router
from app.api.commitments import router as commitments_router
from app.api.companies import router as companies_router
from app.api.company_profile import router as company_profile_router
from app.api.ai_accountant import router as ai_accountant_router
from app.api.auth import router as auth_router
from app.api.brain import router as brain_router
from app.api.budgets import router as budgets_router
from app.api.entities import router as entities_router
from app.api.equity import router as equity_router
from app.api.expenses import router as expenses_router
from app.api.exports import router as exports_router
from app.api.fx import router as fx_router
from app.api.integration import router as integration_router
from app.api.invoices import router as invoices_router
from app.api.invoice_mail import router as invoice_mail_router
from app.api.quotes import router as quotes_router
from app.api.moadian import router as moadian_router
from app.api.recurring_invoices import router as recurring_invoices_router
from app.api.manager_reports import router as manager_reports_router
from app.api.migration import router as migration_router
from app.api.notifications import router as notifications_router
from app.api.payroll import router as payroll_router
from app.api.personal import router as personal_router
from app.api.insights import router as insights_router
from app.api.petty_cash import router as petty_cash_router
from app.api.products import router as products_router
from app.api.purchase_orders import router as purchase_orders_router
from app.api.recurring import router as recurring_router
from app.api.reports import router as reports_router
from app.api.time_tracking import router as time_tracking_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.core.auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    generate_csrf_token,
    parse_session_token,
    validate_csrf,
)
from app.core.rate_limit import RateLimiter
from app.core.permissions import enforce_route_permission
from app.core.request_context import set_current_user, clear_current_user
from app.db.base import Base
from app.db.tenant import set_current_company, clear_current_company, tenant_bypass
from app.db.seed import ensure_default_company, seed_admin_user_if_missing, seed_chart_if_empty, seed_payment_methods_if_empty
from app.db.session import engine, SessionLocal, get_db
import app.models  # noqa: F401 - register models with Base.metadata
import app.core.shared_state  # noqa: F401,E402 - registers the books-version flush hook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
request_logger = logging.getLogger("app.request")


_migration_logger = logging.getLogger("app.migrations")


def _col_type(conn, table: str, column: str) -> str | None:
    """Return the data_type of a column, or None if it doesn't exist (PostgreSQL only)."""
    row = conn.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def _col_is_nullable(conn, table: str, column: str) -> bool | None:
    """Return whether a column is nullable, or None if not found."""
    row = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] == "YES" if row else None


def _apply_numeric_migrations() -> None:
    """
    Idempotent startup migrations: promote INT columns to BIGINT for IRR values.
    Only alters columns that are not already BIGINT.
    """
    if engine.dialect.name != "postgresql":
        return
    targets = [
        ("invoices", "amount"),
        ("recurring_rules", "amount"),
        ("budget_limits", "limit_amount"),
        ("transaction_lines", "debit"),
        ("transaction_lines", "credit"),
    ]
    with engine.begin() as conn:
        for table, col in targets:
            current = _col_type(conn, table, col)
            if current and current != "bigint":
                _migration_logger.info("Upgrading %s.%s from %s to BIGINT", table, col, current)
                conn.execute(text(
                    f"ALTER TABLE {table} ALTER COLUMN {col} TYPE BIGINT USING {col}::BIGINT"
                ))


def _apply_entity_cleanup_migrations() -> None:
    """
    Cleanup malformed entity names produced by old chat parsing paths.
    These UPDATEs/DELETEs use WHERE clauses so they are naturally idempotent.
    """
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        "UPDATE entities SET name = btrim(regexp_replace(name, '\\s+', ' ', 'g')) WHERE name ~ '\\s{2,}'",
        (
            "UPDATE entities "
            "SET name = initcap(btrim(regexp_replace(name, E'\\s+with\\s+of\\s+\\d+\\s*$', '', 'i'))) "
            "WHERE type = 'bank' AND name ~* E'\\s+with\\s+of\\s+\\d+\\s*$'"
        ),
        (
            "UPDATE transactions t SET description = regexp_replace(t.description, E'\\s+[Ww]ith\\s+[Oo]f\\s+\\d+\\s+bank\\s+account', ' bank account', 'g') "
            "WHERE t.description ~* E'\\s+with\\s+of\\s+\\d+\\s+bank\\s+account'"
        ),
        (
            "UPDATE transaction_lines tl SET line_description = regexp_replace(tl.line_description, E'\\s+[Ww]ith\\s+[Oo]f\\s+\\d+', '', 'g') "
            "WHERE tl.line_description ~* E'\\s+with\\s+of\\s+\\d+'"
        ),
        (
            "DELETE FROM transaction_entities te USING entities e "
            "WHERE te.entity_id = e.id "
            "AND e.type = 'employee' "
            "AND e.name ~* '^(us|our|me|we|you|your)[[:space:]]+via[[:space:]].*(bank|account|about)'"
        ),
        (
            "DELETE FROM entities e "
            "WHERE e.type = 'employee' "
            "AND e.name ~* '^(us|our|me|we|you|your)[[:space:]]+via[[:space:]].*(bank|account|about)'"
        ),
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _apply_transaction_fee_migrations() -> None:
    """
    Idempotent: only drop NOT NULL if the column is currently NOT NULL.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        nullable = _col_is_nullable(conn, "transaction_fee_applications", "transaction_id")
        if nullable is False:
            _migration_logger.info("Dropping NOT NULL on transaction_fee_applications.transaction_id")
            conn.execute(text(
                "ALTER TABLE transaction_fee_applications ALTER COLUMN transaction_id DROP NOT NULL"
            ))


def _apply_user_migrations() -> None:
    """
    Startup-safe user schema adjustments.
    """
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(8) DEFAULT 'en'",
        "UPDATE users SET preferred_language = 'en' WHERE preferred_language IS NULL OR btrim(preferred_language) = ''",
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _run_alembic_migrations(strict: bool = False) -> None:
    """Bring the schema to head.

    On a FRESH database `create_all` (run just before this) already builds the
    current/head schema, so the historical *transform* migrations must NOT run —
    they collide with it (e.g. migration 015 does `CREATE INDEX
    ix_accounts_company_id` on an index create_all already made → DuplicateTable,
    and the whole upgrade rolls back). Detect fresh by the absence of Alembic's
    version table and `stamp head` instead; migration 015's DATA backfill is done
    separately and idempotently by `ensure_default_company`. On an existing DB
    (version table present) run the pending migrations normally.

    ``strict`` controls what happens when Alembic itself fails. In BOTH modes we
    first attempt the idempotent startup SQL as a best-effort recovery of the
    known historical column tweaks. In strict mode (the container pre-start) we
    then RE-RAISE, so a genuinely broken migration aborts the boot loudly with
    the traceback in the logs instead of leaving the app serving a possibly-wrong
    schema. In tolerant mode (a dev running `uvicorn` directly, via the lifespan
    self-heal) we swallow and carry on, as before.
    """
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    fresh_db = not inspect(engine).has_table("alembic_version")
    try:
        if fresh_db:
            command.stamp(alembic_cfg, "head")
            _migration_logger.info(
                "Fresh DB — stamped Alembic at head (schema built by create_all)"
            )
        else:
            command.upgrade(alembic_cfg, "head")
            _migration_logger.info("Alembic migrations applied successfully")
    except Exception:
        _migration_logger.error(
            "Alembic step failed — attempting idempotent startup SQL recovery",
            exc_info=True,
        )
        _apply_numeric_migrations()
        _apply_entity_cleanup_migrations()
        _apply_transaction_fee_migrations()
        _apply_user_migrations()
        if strict:
            # Don't mask a broken migration behind the fallback — fail the boot
            # loudly so the operator sees exactly what broke (`docker compose
            # logs api`) rather than an app quietly serving on a bad schema.
            raise


def _bootstrap_schema_and_seed(strict: bool = False) -> None:
    """Build the schema, migrate, and seed — the heavy, potentially-slow work.

    Startup order matters on a FRESH database:
      1. create_all — build the base tables (the migration chain only patches
         an existing schema; it never creates them).
      2. migrate — stamp Alembic at head on a fresh DB (create_all already
         built the head schema) or upgrade an existing one.
      3. seed — create the admin user + chart + defaults (company_id NULL).
      4. ensure_default_company — idempotently create the Default company,
         fold every seeded row + the admin user into it, and promote admin to
         super-admin (the DATA half of migration 015, which never runs on a
         fresh DB because we stamp rather than upgrade).
    On an existing DB every step is idempotent (create_all/upgrade/seed no-op).

    This is extracted from the lifespan so the container entrypoint
    (``app/prestart.py``) can run it ONCE in a one-shot pre-start step BEFORE
    uvicorn boots. That keeps the web server from blocking on — or crash-looping
    from — migrations: /health answers as soon as uvicorn is up, and a migration
    failure becomes a loud non-zero exit rather than a half-started ASGI app.
    Because it stays fully idempotent, the lifespan can still call it as a
    self-heal fallback when the app is launched without the entrypoint.
    """
    Base.metadata.create_all(bind=engine)
    _run_alembic_migrations(strict=strict)
    # Guards a stamped (never migrated) fresh database would otherwise lack.
    from app.db.guards import install_db_guards
    for item in install_db_guards(engine):
        logging.getLogger("app.migrations").info("installed database guard: %s", item)
    db = SessionLocal()
    try:
        seed_chart_if_empty(db)
        seed_payment_methods_if_empty(db)
        seed_admin_user_if_missing(db)
        _warn_if_default_admin_password(db)
        from app.services.fx_service import seed_default_rates_if_empty
        seed_default_rates_if_empty(db)
        from app.services.tax_rate_service import seed_tax_rates
        seed_tax_rates(db)
        from app.services.payroll_rules import seed_payroll_rules
        seed_payroll_rules(db)
    finally:
        db.close()
    ensure_default_company(engine)


def _warn_if_default_admin_password(db) -> None:
    """Loud boot-time warning while the seeded admin still has 'admin'."""
    try:
        from sqlalchemy import func, select
        from app.core.auth import verify_password
        from app.models.user import User

        row = db.execute(select(User).where(func.lower(User.username) == "admin")).scalars().first()
        if row is not None and verify_password("admin", row.password_hash, row.password_salt):
            logging.getLogger("app.security").warning(
                "SECURITY: the 'admin' account still uses the default password. "
                "Sign in and set a new one — until then that session is locked to the change-password screen."
            )
    except Exception:  # pragma: no cover - never block boot on this
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    # The container entrypoint runs _bootstrap_schema_and_seed() in a one-shot
    # pre-start (app/prestart.py) BEFORE uvicorn, so the web server answers
    # /health immediately and a migration failure is a loud, isolated non-zero
    # exit instead of a crash-looping/half-started app. When that already
    # happened we skip the (idempotent) redo here; otherwise — e.g. `uvicorn`
    # run directly, or a test harness — we still bootstrap so the app self-heals.
    if os.getenv("ENTRYPOINT_BOOTSTRAP") == "1":
        _migration_logger.info(
            "Schema bootstrap handled by entrypoint pre-start; skipping in lifespan."
        )
    else:
        _bootstrap_schema_and_seed()
    # Restore AI config from database (survives restarts). Always runs in-process
    # because it sets per-process runtime globals the pre-start process can't.
    from app.core.ai_runtime import load_ai_config_from_db
    load_ai_config_from_db()
    # Background jobs: recurring postings, notification feed, daily digest —
    # they used to run only while a browser had the app open.
    import asyncio as _asyncio
    from app.jobs.scheduler import scheduler_loop
    _stop = _asyncio.Event()
    _task = _asyncio.create_task(scheduler_loop(_stop)) if settings.scheduler_enabled else None
    yield
    if _task is not None:
        _stop.set()
        try:
            await _asyncio.wait_for(_task, timeout=5)
        except Exception:
            _task.cancel()


app = FastAPI(
    title="Accounting Assistant API",
    description="API for accounting data, accounts, and transactions",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.app_cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global API rate limiter: 120 requests per minute per user/IP
_api_limiter = RateLimiter(max_requests=120, window_seconds=60)

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


PUBLIC_PATHS = {
    "/auth/login",
    "/auth/logout",
}

# Self-service auth endpoints that need a session (+ CSRF on writes). The
# router prefixes are added below, once the routers are registered, so the
# list is derived from the app instead of maintained by hand.
_AUTH_SELF_SERVICE_PATHS = (
    "/auth/me",
    "/auth/change-password",
    "/auth/preferences",
    "/auth/admin-check",
    "/auth/whats-new",
)
PROTECTED_API_PREFIXES: tuple[str, ...] = _AUTH_SELF_SERVICE_PATHS


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add CSP and other security headers, enforce global API rate limit."""
    path = request.url.path
    # Rate limit protected API endpoints
    if path.startswith(PROTECTED_API_PREFIXES):
        identity = getattr(getattr(request.state, "user", None), "user_id", None) or (
            request.client.host if request.client else "unknown"
        )
        if not _api_limiter.is_allowed(identity):
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded. Try again shortly."})

    response = await call_next(request)
    # CSP header on HTML pages and API responses
    if "text/html" in response.headers.get("content-type", "") or path.startswith(PROTECTED_API_PREFIXES):
        response.headers["Content-Security-Policy"] = _CSP_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.app_env == "prod":
        # Production is HTTPS-only behind the proxy: browsers must never fall
        # back to plain HTTP for this host.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        request_logger.exception(
            "request_failed id=%s method=%s path=%s ms=%s",
            req_id,
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = req_id
    request_logger.info(
        "request_done id=%s method=%s path=%s status=%s ms=%s",
        req_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_origin(request: Request) -> bool:
    """Browsers send Origin (or at least Referer) on cross-site POSTs. When
    present it must name this host; absent (curl, tests, same-origin GET) is
    fine. Protects /auth/login and /auth/logout, which have no CSRF token
    yet (security review 2026-09-24, L9)."""
    from urllib.parse import urlsplit

    src = request.headers.get("origin") or request.headers.get("referer")
    if not src:
        return True
    host = (request.headers.get("host") or request.url.netloc or "").lower()
    return urlsplit(src).netloc.lower() == host


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS and request.method == "POST" and not _same_origin(request):
        return JSONResponse(status_code=403, content={"detail": "Cross-site request refused"})
    if settings.app_env == "prod" and (path.startswith(("/docs", "/redoc")) or path == "/openapi.json"):
        # The interactive API docs stay a development aid (review L1).
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    # allow app/static resources and framework internals
    if (
        path in PUBLIC_PATHS
        or path.startswith("/static/")
        or path.startswith("/docs")
        or path.startswith("/redoc")
        or path == "/openapi.json"
        or path.startswith("/favicon")
        or path == "/health"
    ):
        return await call_next(request)

    # /api/v1 is the key-authenticated integration surface: no session cookie,
    # no CSRF — the API key alone resolves the company and scopes everything.
    if path.startswith("/api/v1/"):
        resolved = await run_in_threadpool(_resolve_api_key_actor, request)
        if resolved is None:
            return JSONResponse(status_code=401, content={"detail": "A valid API key is required."})
        company_id, actor = resolved
        if not _api_key_limiter.is_allowed(actor.user_id):
            return JSONResponse(status_code=429, content={"detail": "API rate limit exceeded."})
        request.state.user = actor
        request.state.api_key = True
        set_current_company(company_id)
        set_current_user(actor)
        try:
            return await call_next(request)
        finally:
            clear_current_company()
            clear_current_user()

    token = request.cookies.get(settings.auth_cookie_name)
    user = parse_session_token(token)
    # Session validation hits the database: keep it off the event loop so one
    # slow query cannot stall every other request (review M5).
    if user is not None and not await run_in_threadpool(_session_is_valid, user):
        # Token invalidated (password reset or company suspended) → drop it.
        user = None
    request.state.user = user

    # Scope every tenant query to this user's company, and expose the acting
    # user (for audit attribution / object-ownership / field-stripping) for the
    # whole request.
    set_current_company(user.company_id if user else None)
    set_current_user(user)
    try:
        return await _dispatch(request, call_next, path, user)
    finally:
        clear_current_company()
        clear_current_user()


def _session_is_valid(user) -> bool:
    """Reject a session whose company was suspended or whose password was reset
    (token_version bumped). Looks the user up directly; if the user can't be
    found (e.g. stateless test tokens) the token is treated as still valid."""
    try:
        sess, gen = _resolve_validation_session()
        try:
            from uuid import UUID
            from app.models.user import User
            from app.models.company import Company
            try:
                uid = UUID(str(user.user_id))
            except (ValueError, TypeError):
                uid = user.user_id
            with tenant_bypass():
                row = sess.get(User, uid)
                if row is None:
                    # A deleted user must not keep a working session (security
                    # review 2026-09-24, H4). Stateless tokens with no user row
                    # exist only in dev/test fixtures.
                    return settings.app_env in ("dev", "test")
                if not row.is_active or int(row.token_version) != int(user.token_version):
                    return False
                if row.company_id is not None:
                    company = sess.get(Company, row.company_id)
                    if company is not None and company.status != "active":
                        return False
                # Refresh RBAC + privilege fields from the DB so a role change,
                # entity link or revoked super-admin flag takes effect on the
                # next request without a re-login (never trust the token's copy).
                user.role = getattr(row, "role", None) or "owner"
                user.entity_id = str(row.entity_id) if getattr(row, "entity_id", None) else None
                user.is_superadmin = bool(getattr(row, "is_superadmin", False))
                user.is_admin = bool(getattr(row, "is_admin", False)) or user.is_superadmin
            return True
        finally:
            if gen is not None:
                gen.close()
            else:
                sess.close()
    except Exception:
        # Fail closed: a validation error means we cannot prove the session is
        # still good. The client simply retries / re-logs in.
        logging.getLogger("app.auth").warning("session_validation_failed", exc_info=True)
        return False


def _resolve_validation_session():
    """Get a DB session that honours the test get_db override."""
    override = app.dependency_overrides.get(get_db)
    if override is not None:
        gen = override()
        return next(gen), gen
    return SessionLocal(), None


# Per-key rate limit for the /api/v1 integration surface.
_api_key_limiter = RateLimiter(max_requests=240, window_seconds=60)


def _resolve_api_key_actor(request: Request):
    """Resolve an /api/v1 request's API key to (company_id, service actor).
    Returns None when the key is missing, unknown, revoked, or its company is
    not active. The actor is a SessionUser-shaped service identity so audit
    attribution and object-ownership helpers work unchanged."""
    from app.core.api_key_auth import extract_api_key, hash_api_key
    from app.core.auth import SessionUser
    from app.models.api_key import ApiKey
    from app.models.company import Company
    from datetime import datetime, timezone

    raw = extract_api_key(request.headers)
    if not raw:
        return None
    digest = hash_api_key(raw)
    try:
        sess, gen = _resolve_validation_session()
        try:
            with tenant_bypass():
                from sqlalchemy import select as _select
                key = sess.execute(
                    _select(ApiKey).where(ApiKey.key_hash == digest)
                ).scalars().first()
                if key is None or key.revoked:
                    return None
                company = sess.get(Company, key.company_id)
                if company is None or company.status != "active":
                    return None
                key.last_used_at = datetime.now(timezone.utc)
                sess.commit()
                actor = SessionUser(
                    user_id=str(key.id),
                    username=f"api:{key.label or 'integration'}",
                    is_admin=False,
                    company_id=str(key.company_id),
                    role="integration",
                )
                return str(key.company_id), actor
        finally:
            if gen is not None:
                gen.close()
            else:
                sess.close()
    except Exception:
        logging.getLogger("app.api_key").warning("API key resolution failed", exc_info=True)
        return None


# What a session opened with the default password may still reach.
_PASSWORD_CHANGE_ALLOWED = {"/auth/change-password", "/auth/logout", "/auth/me", "/login"}


async def _dispatch(request: Request, call_next, path: str, user):
    if user is not None and getattr(user, "must_change_password", False) and path not in _PASSWORD_CHANGE_ALLOWED:
        # Locked session: the seeded default password was used. Send the app
        # shell back to the login page (which shows the change form) and
        # refuse every API call with a machine-readable reason.
        if path == "/":
            return RedirectResponse(url="/login?change=1", status_code=302)
        if path.startswith(PROTECTED_API_PREFIXES) or path.startswith("/auth/"):
            return JSONResponse(
                status_code=403,
                content={"detail": "You signed in with the default password. Set a new password to continue.",
                         "code": "password_change_required"},
            )
    if path == "/":
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        response = await call_next(request)
        # Set CSRF cookie on every HTML page load so the frontend can read it
        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(
                CSRF_COOKIE, generate_csrf_token(),
                httponly=False, samesite="strict", secure=request.url.scheme == "https",
            )
        return response

    if path == "/login":
        if user and not getattr(user, "must_change_password", False):
            return RedirectResponse(url="/", status_code=302)
        return await call_next(request)

    if path.startswith(PROTECTED_API_PREFIXES):
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        # CSRF check: state-changing methods must include a matching token
        if request.method not in _CSRF_SAFE_METHODS:
            if not validate_csrf(request):
                return JSONResponse(status_code=403, content={"detail": "CSRF token missing or invalid"})
        return await call_next(request)

    return await call_next(request)


_CODE_SCHEMA_HEAD: str | None = None


def _code_schema_head() -> str | None:
    """The Alembic head revision shipped IN THIS BUILD (cached). Comparing it to
    the DB's applied version in /health makes a stale deployed image obvious:
    if the head here lags what's on main, the server never pulled the new image."""
    global _CODE_SCHEMA_HEAD
    if _CODE_SCHEMA_HEAD is None:
        try:
            from alembic.config import Config as _AlembicConfig
            from alembic.script import ScriptDirectory
            script = ScriptDirectory.from_config(
                _AlembicConfig(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
            )
            heads = script.get_heads()
            _CODE_SCHEMA_HEAD = heads[0] if heads else "unknown"
        except Exception:
            _CODE_SCHEMA_HEAD = "unknown"
    return _CODE_SCHEMA_HEAD


@app.get("/health", include_in_schema=True, tags=["system"])
def health_check():
    """Health check endpoint for monitoring and container orchestration."""
    status = "ok"
    db_ok = False
    db_schema = None
    try:
        # Same session source as the auth middleware: the real engine in
        # production, the overridden one under test.
        db, gen = _resolve_validation_session()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
            try:
                row = db.execute(text("SELECT version_num FROM alembic_version")).first()
                db_schema = row[0] if row else None
            except Exception:
                db_schema = None
        finally:
            if gen is not None:
                gen.close()
            else:
                db.close()
    except Exception:
        status = "degraded"
    try:
        from app.services.ocr_extract import ocr_engine_available

        ocr_ok = ocr_engine_available()
    except Exception:
        ocr_ok = False
    body = {
        "status": status,
        "database": "connected" if db_ok else "unavailable",
        "ocr_available": ocr_ok,
        # Deploy diagnostics: image_schema = migrations shipped in this build;
        # db_schema = what's applied. image_schema lagging main ⇒ stale image.
        "image_schema": _code_schema_head(),
        "db_schema": db_schema,
    }
    if status != "ok":
        # A 200 "degraded" kept the container healthy while the database was
        # down (security review 2026-09-24, M6). Monitors and the compose
        # healthcheck need the status code to say it.
        return JSONResponse(status_code=503, content=body)
    return body


from fastapi.exceptions import RequestValidationError


def _sanitize_errors(errors: list[dict]) -> list[dict]:
    """Make Pydantic validation errors JSON-serializable (ctx may contain Exception objects)."""
    safe = []
    for err in errors:
        e = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err and isinstance(err["ctx"], dict):
            e["ctx"] = {k: str(v) if isinstance(v, Exception) else v for k, v in err["ctx"].items()}
        safe.append(e)
    return safe


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return structured error responses for validation failures."""
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "detail": "Request validation failed",
            "errors": _sanitize_errors(exc.errors()),
            "request_id": request.headers.get("x-request-id"),
        },
    )


# Every business router carries the central default-deny RBAC guard
# (app/core/permissions.py). auth_router is exempt — it holds the public
# login/logout and self-service endpoints that use get_current_user directly.
_rbac = [Depends(enforce_route_permission)]
app.include_router(accounts_router, dependencies=_rbac)
app.include_router(adjustments_router, dependencies=_rbac)
app.include_router(admin_router, dependencies=_rbac)
app.include_router(companies_router, dependencies=_rbac)
app.include_router(company_profile_router, dependencies=_rbac)
app.include_router(ai_accountant_router, dependencies=_rbac)
app.include_router(auth_router)
# /api/v1: API-key auth (middleware) + its own require_api_key guard — NOT the
# session RBAC guard. The key resolves the company; tenant scoping does the rest.
app.include_router(integration_router)
app.include_router(brain_router, dependencies=_rbac)
app.include_router(budgets_router, dependencies=_rbac)
app.include_router(entities_router, dependencies=_rbac)
app.include_router(equity_router, dependencies=_rbac)
app.include_router(expenses_router, dependencies=_rbac)
app.include_router(exports_router, dependencies=_rbac)
app.include_router(fx_router, dependencies=_rbac)
app.include_router(invoice_mail_router, dependencies=_rbac)
app.include_router(invoices_router, dependencies=_rbac)
app.include_router(quotes_router, dependencies=_rbac)
app.include_router(moadian_router, dependencies=_rbac)
app.include_router(recurring_invoices_router, dependencies=_rbac)
app.include_router(manager_reports_router, dependencies=_rbac)
app.include_router(migration_router, dependencies=_rbac)
app.include_router(notifications_router, dependencies=_rbac)
app.include_router(payroll_router, dependencies=_rbac)
app.include_router(personal_router, dependencies=_rbac)
app.include_router(insights_router, dependencies=_rbac)
app.include_router(commitments_router, dependencies=_rbac)
app.include_router(petty_cash_router, dependencies=_rbac)
app.include_router(products_router, dependencies=_rbac)
app.include_router(purchase_orders_router, dependencies=_rbac)
app.include_router(recurring_router, dependencies=_rbac)
app.include_router(reports_router, dependencies=_rbac)
app.include_router(time_tracking_router, dependencies=_rbac)
app.include_router(transactions_router, dependencies=_rbac)

# Every router mounted behind the RBAC guard is protected API surface: session
# required, CSRF on writes, global rate limit, default-password lock. Derived
# from the registrations so a new router can never be forgotten — the hand-kept
# list used to miss /commitments, /insights, /personal, /migration and
# /petty-cash (security review 2026-09-24, M1).
_GUARDED_PREFIXES = tuple(sorted({
    r.prefix for r in (
        accounts_router, adjustments_router, admin_router, companies_router, company_profile_router,
        ai_accountant_router, brain_router, budgets_router, entities_router, equity_router,
        expenses_router, exports_router, fx_router, invoices_router, invoice_mail_router, manager_reports_router,
        migration_router, moadian_router, notifications_router, payroll_router, personal_router, insights_router,
        commitments_router, petty_cash_router, products_router, purchase_orders_router,
        quotes_router, recurring_router, recurring_invoices_router, reports_router, time_tracking_router, transactions_router,
    ) if r.prefix
}))
PROTECTED_API_PREFIXES = _GUARDED_PREFIXES + _AUTH_SELF_SERVICE_PATHS

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Uploaded files (receipts, statements, logos, signatures) are NOT mounted as
# public static files any more (security review 2026-09-24, H1): they are
# served by authenticated, tenant-scoped routes — GET /transactions/attachments/
# {id}/file and GET /admin/company-profile/logo — and never from /uploads.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Static asset cache-busting ---------------------------------------------
# index.html is served no-store, but the js/ and css/ files it pulls are cached
# by the browser. Without a version marker a deploy can leave a client running
# a mix of old and new files — the load order across js/01..16 is load-bearing,
# so a stale file is a broken app, not a cosmetic glitch. Each reference gets
# ?v=<content hash>, so a changed file gets a new URL and an unchanged one stays
# cached. Hashes are memoised per mtime, which keeps the dev bind-mount honest.
_ASSET_VERSIONS: dict[str, tuple[float, str]] = {}
_ASSET_REF_RE = re.compile(
    r'(?P<attr>src|href)="/static/(?P<path>[A-Za-z0-9][A-Za-z0-9._/-]*\.(?:js|css))"'
)


def _asset_version(rel_path: str) -> str | None:
    """Short content hash for a static asset, or None if it isn't a readable
    file under STATIC_DIR."""
    path = (STATIC_DIR / rel_path).resolve()
    try:
        path.relative_to(STATIC_DIR.resolve())  # never step outside static/
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    cached = _ASSET_VERSIONS.get(rel_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return None
    _ASSET_VERSIONS[rel_path] = (mtime, digest)
    return digest


def render_versioned_html(name: str) -> str:
    """Read a static HTML page and stamp ?v=<hash> onto its local js/css refs."""
    html = (STATIC_DIR / name).read_text(encoding="utf-8")

    def _stamp(m: re.Match) -> str:
        version = _asset_version(m.group("path"))
        if version is None:
            return m.group(0)  # unknown file: leave the reference untouched
        return f'{m.group("attr")}="/static/{m.group("path")}?v={version}"'

    return _ASSET_REF_RE.sub(_stamp, html)


_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/", include_in_schema=False)
def index():
    return HTMLResponse(render_versioned_html("index.html"), headers=_NO_STORE)


@app.get("/login", include_in_schema=False)
def login():
    return HTMLResponse(render_versioned_html("login.html"), headers=_NO_STORE)
