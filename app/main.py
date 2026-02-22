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
from app.api.budgets import router as budgets_router
from app.api.entities import router as entities_router
from app.api.exports import router as exports_router
from app.api.invoices import router as invoices_router
from app.api.manager_reports import router as manager_reports_router
from app.api.notifications import router as notifications_router
from app.api.recurring import router as recurring_router
from app.api.reports import router as reports_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.core.auth import parse_session_token
from app.db.base import Base
from app.db.seed import seed_admin_user_if_missing, seed_chart_if_empty, seed_payment_methods_if_empty
from app.db.session import engine, SessionLocal
import app.models  # noqa: F401 - register models with Base.metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
request_logger = logging.getLogger("app.request")


def _apply_numeric_migrations() -> None:
    """
    Lightweight startup migrations for existing DBs without Alembic.
    Keeps high-amount IRR values from overflowing INT columns.
    """
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        "ALTER TABLE invoices ALTER COLUMN amount TYPE BIGINT USING amount::BIGINT",
        "ALTER TABLE recurring_rules ALTER COLUMN amount TYPE BIGINT USING amount::BIGINT",
        "ALTER TABLE budget_limits ALTER COLUMN limit_amount TYPE BIGINT USING limit_amount::BIGINT",
        "ALTER TABLE transaction_lines ALTER COLUMN debit TYPE BIGINT USING debit::BIGINT",
        "ALTER TABLE transaction_lines ALTER COLUMN credit TYPE BIGINT USING credit::BIGINT",
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _apply_entity_cleanup_migrations() -> None:
    """
    Cleanup malformed entity names produced by old chat parsing paths.
    Idempotent and safe to run at startup.
    """
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        # Normalize extra whitespace globally.
        "UPDATE entities SET name = btrim(regexp_replace(name, '\\s+', ' ', 'g'))",
        # Fix malformed bank names like: 'Mellat With Of 1161743370' -> 'Mellat'
        (
            "UPDATE entities "
            "SET name = initcap(btrim(regexp_replace(name, E'\\s+with\\s+of\\s+\\d+\\s*$', '', 'i'))) "
            "WHERE type = 'bank' AND name ~* E'\\s+with\\s+of\\s+\\d+\\s*$'"
        ),
        # Keep linked transaction descriptions clean as well.
        (
            "UPDATE transactions t SET description = regexp_replace(t.description, E'\\s+[Ww]ith\\s+[Oo]f\\s+\\d+\\s+bank\\s+account', ' bank account', 'g') "
            "WHERE t.description ~* E'\\s+with\\s+of\\s+\\d+\\s+bank\\s+account'"
        ),
        (
            "UPDATE transaction_lines tl SET line_description = regexp_replace(tl.line_description, E'\\s+[Ww]ith\\s+[Oo]f\\s+\\d+', '', 'g') "
            "WHERE tl.line_description ~* E'\\s+with\\s+of\\s+\\d+'"
        ),
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _apply_transaction_fee_migrations() -> None:
    """
    Startup-safe adjustments for transaction fee feature tables.
    """
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        "ALTER TABLE transaction_fee_applications ALTER COLUMN transaction_id DROP NOT NULL",
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables
    Base.metadata.create_all(bind=engine)
    _apply_numeric_migrations()
    _apply_entity_cleanup_migrations()
    _apply_transaction_fee_migrations()
    # Seed minimal chart of accounts if empty
    db = SessionLocal()
    try:
        seed_chart_if_empty(db)
        seed_payment_methods_if_empty(db)
        seed_admin_user_if_missing(db)
    finally:
        db.close()
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
    allow_origins=settings.app_cors_origins.split(",") if settings.app_cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    "/auth/admin-check",
)


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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # allow app/static resources and framework internals
    if (
        path in PUBLIC_PATHS
        or path.startswith("/uploads/")
        or path.startswith("/docs")
        or path.startswith("/redoc")
        or path == "/openapi.json"
        or path.startswith("/favicon")
    ):
        return await call_next(request)

    token = request.cookies.get(settings.auth_cookie_name)
    user = parse_session_token(token)
    request.state.user = user

    if path == "/":
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        return await call_next(request)

    if path == "/login":
        if user:
            return RedirectResponse(url="/", status_code=302)
        return await call_next(request)

    if path.startswith(PROTECTED_API_PREFIXES):
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        return await call_next(request)

    return await call_next(request)


app.include_router(accounts_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(budgets_router)
app.include_router(entities_router)
app.include_router(exports_router)
app.include_router(invoices_router)
app.include_router(manager_reports_router)
app.include_router(notifications_router)
app.include_router(recurring_router)
app.include_router(reports_router)
app.include_router(transactions_router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login():
    return FileResponse(STATIC_DIR / "login.html")
