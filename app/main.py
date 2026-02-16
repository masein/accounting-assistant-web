from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.budgets import router as budgets_router
from app.api.entities import router as entities_router
from app.api.exports import router as exports_router
from app.api.invoices import router as invoices_router
from app.api.notifications import router as notifications_router
from app.api.recurring import router as recurring_router
from app.api.reports import router as reports_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.db.base import Base
from app.db.seed import seed_chart_if_empty
from app.db.session import engine, SessionLocal
import app.models  # noqa: F401 - register models with Base.metadata


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables
    Base.metadata.create_all(bind=engine)
    _apply_numeric_migrations()
    # Seed minimal chart of accounts if empty
    db = SessionLocal()
    try:
        seed_chart_if_empty(db)
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

app.include_router(accounts_router)
app.include_router(admin_router)
app.include_router(budgets_router)
app.include_router(entities_router)
app.include_router(exports_router)
app.include_router(invoices_router)
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
