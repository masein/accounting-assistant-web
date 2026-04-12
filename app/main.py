from contextlib import asynccontextmanager
import logging
from pathlib import Path
import time
import uuid

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.brain import router as brain_router
from app.api.budgets import router as budgets_router
from app.api.entities import router as entities_router
from app.api.exports import router as exports_router
from app.api.invoices import router as invoices_router
from app.api.manager_reports import router as manager_reports_router
from app.api.notifications import router as notifications_router
from app.api.products import router as products_router
from app.api.recurring import router as recurring_router
from app.api.reports import router as reports_router
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
from app.db.base import Base
from app.db.seed import seed_admin_user_if_missing, seed_chart_if_empty, seed_payment_methods_if_empty
from app.db.session import engine, SessionLocal
import app.models  # noqa: F401 - register models with Base.metadata

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


def _run_alembic_migrations() -> None:
    """Run Alembic migrations programmatically (equivalent to 'alembic upgrade head')."""
    from alembic.config import Config
    from alembic import command
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    try:
        command.upgrade(alembic_cfg, "head")
        _migration_logger.info("Alembic migrations applied successfully")
    except Exception:
        _migration_logger.warning(
            "Alembic migration failed — falling back to idempotent startup SQL",
            exc_info=True,
        )
        _apply_numeric_migrations()
        _apply_entity_cleanup_migrations()
        _apply_transaction_fee_migrations()
        _apply_user_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables + run migrations
    Base.metadata.create_all(bind=engine)
    _run_alembic_migrations()
    # Seed minimal chart of accounts if empty
    db = SessionLocal()
    try:
        seed_chart_if_empty(db)
        seed_payment_methods_if_empty(db)
        seed_admin_user_if_missing(db)
    finally:
        db.close()
    # Restore AI config from database (survives restarts)
    from app.core.ai_runtime import load_ai_config_from_db
    load_ai_config_from_db()
    yield
    # Shutdown: nothing to do
    pass


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

PROTECTED_API_PREFIXES = (
    "/accounts",
    "/admin",
    "/budgets",
    "/entities",
    "/exports",
    "/invoices",
    "/manager-reports",
    "/notifications",
    "/recurring",
    "/reports",
    "/transactions",
    "/auth/me",
    "/auth/change-password",
    "/auth/preferences",
    "/auth/admin-check",
)


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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # allow app/static resources and framework internals
    if (
        path in PUBLIC_PATHS
        or path.startswith("/uploads/")
        or path.startswith("/static/")
        or path.startswith("/docs")
        or path.startswith("/redoc")
        or path == "/openapi.json"
        or path.startswith("/favicon")
        or path == "/health"
    ):
        return await call_next(request)

    token = request.cookies.get(settings.auth_cookie_name)
    user = parse_session_token(token)
    request.state.user = user

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
        if user:
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


@app.get("/health", include_in_schema=True, tags=["system"])
def health_check():
    """Health check endpoint for monitoring and container orchestration."""
    status = "ok"
    db_ok = False
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        status = "degraded"
    return {"status": status, "database": "connected" if db_ok else "unavailable"}


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


app.include_router(accounts_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(brain_router)
app.include_router(budgets_router)
app.include_router(entities_router)
app.include_router(exports_router)
app.include_router(invoices_router)
app.include_router(manager_reports_router)
app.include_router(notifications_router)
app.include_router(products_router)
app.include_router(recurring_router)
app.include_router(reports_router)
app.include_router(transactions_router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/login", include_in_schema=False)
def login():
    return FileResponse(
        STATIC_DIR / "login.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
