"""One-shot container pre-start: wait for the DB, build/upgrade the schema, seed.

Run by ``entrypoint.sh`` BEFORE uvicorn so the web server never blocks on — or
crash-loops from — migrations. On success it exits 0 and the entrypoint execs
uvicorn (which then answers /health straight away). On failure it logs the full
traceback and exits non-zero, so ``docker compose logs api`` shows exactly what
broke instead of a silent hang or a half-started app serving errors.

Run manually to reproduce a boot:  ``python -m app.prestart``
"""
from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
log = logging.getLogger("app.prestart")


def wait_for_db(max_wait: float = 60.0, interval: float = 2.0) -> None:
    """Block until the database accepts a connection, or raise after max_wait.

    Belt-and-braces with compose's ``depends_on: condition: service_healthy``:
    Postgres can briefly bounce during first-boot initdb even after pg_isready
    flips healthy, and this also covers running the app against an external DB
    that has no compose healthgate at all.
    """
    from sqlalchemy import text

    from app.db.session import engine

    deadline = time.monotonic() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Database is accepting connections (after %d attempt(s)).", attempt)
            return
        except Exception as exc:  # noqa: BLE001 — any driver error means "not ready yet"
            if time.monotonic() >= deadline:
                log.error("Database not reachable after %.0fs — giving up.", max_wait)
                raise
            log.info(
                "Waiting for database (attempt %d, %s) — retrying in %.0fs ...",
                attempt,
                exc.__class__.__name__,
                interval,
            )
            time.sleep(interval)


def main() -> int:
    try:
        wait_for_db()
        # Imported lazily so a DB-wait failure is reported before we pull in the
        # full app graph.
        from app.main import _bootstrap_schema_and_seed

        log.info("Running schema bootstrap (create_all → migrate → seed) ...")
        started = time.monotonic()
        # strict=True: a broken migration must abort the boot loudly, not be
        # masked by the idempotent fallback and leave the app on a bad schema.
        _bootstrap_schema_and_seed(strict=True)
        log.info("Schema bootstrap complete in %.1fs.", time.monotonic() - started)
        return 0
    except Exception:  # noqa: BLE001 — surface EVERYTHING, loudly, then fail hard
        log.exception("PRE-START FAILED — schema bootstrap did not complete")
        return 1


if __name__ == "__main__":
    sys.exit(main())
