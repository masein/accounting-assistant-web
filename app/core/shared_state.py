"""Cross-worker primitives on Postgres (roadmap §2.2).

* ``DbRateLimiter``  sliding-window limiter whose attempts are rows, so every
  worker counts the same attempts. Each call commits its own row: callers use
  it before any other write in the request (login, signup, chat).
* ``books_version``  per-company counter bumped by an ORM flush hook whenever
  ledger or invoice rows change; caches include it in their key.
* upload tokens      ``store_upload`` / ``find_upload`` / ``drop_upload``,
  tenant-scoped and expiring.

The per-request API limiter stays in process on purpose: a database write on
every request costs more than it protects, and the effective limit with N
workers is N × the configured rate (documented in DEPLOY.md).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

from app.models.shared_state import RateLimitEvent, UploadToken


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DbRateLimiter:
    PRUNE_PROBABILITY = 0.02

    def __init__(self, bucket: str, max_requests: int, window_seconds: int):
        self.bucket = bucket[:32]
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def _count(self, db: Session, identity: str, now: datetime) -> int:
        cutoff = now - timedelta(seconds=self.window_seconds)
        return int(db.execute(
            select(func.count(RateLimitEvent.id)).where(
                RateLimitEvent.bucket == self.bucket, RateLimitEvent.identity == identity[:256],
                RateLimitEvent.at > cutoff,
            )
        ).scalar() or 0)

    def would_allow(self, db: Session, identity: str) -> bool:
        return self._count(db, identity, _utcnow()) < self.max_requests

    def remaining(self, db: Session, identity: str) -> int:
        return max(0, self.max_requests - self._count(db, identity, _utcnow()))

    def hit(self, db: Session, identity: str) -> None:
        now = _utcnow()
        db.add(RateLimitEvent(bucket=self.bucket, identity=identity[:256], at=now))
        if random.random() < self.PRUNE_PROBABILITY:
            self.prune(db, now)
        db.commit()

    def is_allowed(self, db: Session, identity: str) -> bool:
        """Check and record in one step."""
        if not self.would_allow(db, identity):
            return False
        self.hit(db, identity)
        return True

    def reset(self, db: Session, identity: str) -> None:
        db.execute(delete(RateLimitEvent).where(RateLimitEvent.bucket == self.bucket,
                                                RateLimitEvent.identity == identity[:256]))
        db.commit()

    def prune(self, db: Session, now: datetime | None = None) -> None:
        cutoff = (now or _utcnow()) - timedelta(seconds=self.window_seconds * 2)
        db.execute(delete(RateLimitEvent).where(RateLimitEvent.bucket == self.bucket, RateLimitEvent.at < cutoff))

    def clear(self, db: Session) -> None:
        db.execute(delete(RateLimitEvent).where(RateLimitEvent.bucket == self.bucket))
        db.commit()


# ---------------------------------------------------------------------------
# Books version (cache invalidation across workers)
# ---------------------------------------------------------------------------

_TRACKED = ("transactions", "transaction_lines", "invoices", "invoice_items", "payments", "credit_notes",
            "pay_runs", "commitments", "exchange_rates", "budget_limits", "accounts", "entities")

_BUMP_SQL = text(
    "INSERT INTO books_versions (scope, version) VALUES (:scope, 1) "
    "ON CONFLICT (scope) DO UPDATE SET version = books_versions.version + 1"
)


def _scope_of(obj) -> str:
    from app.db.tenant import get_current_company
    cid = getattr(obj, "company_id", None) or get_current_company()
    return str(cid) if cid else "platform"


def current_scope() -> str:
    from app.db.tenant import get_current_company
    cid = get_current_company()
    return str(cid) if cid else "platform"


def books_version(db: Session, scope: str | None = None) -> int:
    from app.models.shared_state import BooksVersion
    row = db.get(BooksVersion, scope or current_scope())
    return int(row.version) if row else 0


@event.listens_for(Session, "after_flush")
def _bump_books_version(session: Session, _ctx) -> None:
    scopes: set[str] = set()
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        table = getattr(getattr(obj, "__table__", None), "name", None)
        if table in _TRACKED and (obj in session.new or obj in session.deleted or session.is_modified(obj)):
            scopes.add(_scope_of(obj))
    if not scopes:
        return
    conn = session.connection()
    for scope in sorted(scopes):
        conn.execute(_BUMP_SQL, {"scope": scope})


# ---------------------------------------------------------------------------
# Upload tokens
# ---------------------------------------------------------------------------

def _remove_expired_file(path: str) -> None:
    """Delete an expired upload's temp file — only inside the import
    directory, never anything a stray row might point at elsewhere."""
    import tempfile
    from pathlib import Path
    root = (Path(tempfile.gettempdir()) / "excel_imports").resolve()
    try:
        p = Path(path).resolve()
        if p.parent == root and p.is_file():
            p.unlink()
    except OSError:
        pass


def store_upload(db: Session, kind: str, token: str, file_path: str, *, ttl_hours: int = 6) -> None:
    now = _utcnow()
    for old in db.execute(select(UploadToken).where(UploadToken.expires_at < now)).scalars().all():
        _remove_expired_file(old.file_path)
    db.execute(delete(UploadToken).where(UploadToken.expires_at < now))
    row = db.execute(select(UploadToken).where(UploadToken.kind == kind, UploadToken.token == token)).scalars().first()
    if row is None:
        db.add(UploadToken(kind=kind, token=token, file_path=file_path, expires_at=now + timedelta(hours=ttl_hours)))
    else:
        row.file_path = file_path
        row.expires_at = now + timedelta(hours=ttl_hours)
    db.commit()


def find_upload(db: Session, kind: str, token: str | None) -> str | None:
    if not token:
        return None
    row = db.execute(select(UploadToken).where(
        UploadToken.kind == kind, UploadToken.token == token, UploadToken.expires_at > _utcnow(),
    )).scalars().first()
    return row.file_path if row else None


def drop_upload(db: Session, kind: str, token: str) -> None:
    db.execute(delete(UploadToken).where(UploadToken.kind == kind, UploadToken.token == token))
