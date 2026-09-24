from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

# --- SQLite: store UUID columns as text -------------------------------------
# The models use the PostgreSQL UUID type. On SQLite (the test database, or a
# dev box on sqlite://) SQLAlchemy renders it with the type name "UUID", which
# SQLite does not know and therefore gives NUMERIC affinity. A UUID whose hex
# happens to look like a number in scientific notation (about one in a million,
# e.g. 1e999999999999999999999999999999) is then stored as the float inf and
# blows up on read ("'float' object has no attribute 'replace'") — the flaky CI
# failure seen on 2026-09-24. CHAR(32) has TEXT affinity and keeps the hex.
from sqlalchemy.dialects.postgresql import UUID as _PG_UUID  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(_PG_UUID, "sqlite")
def _compile_uuid_for_sqlite(type_, compiler, **kw):  # pragma: no cover - trivial
    return "CHAR(32)"
