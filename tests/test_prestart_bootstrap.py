"""Container pre-start contract (startup/deploy hardening).

The web server must never boot before the schema is migrated, and a failed
migration must abort LOUDLY (non-zero exit) rather than leave a half-started
app. These tests pin that contract without needing a real Postgres.
"""
from __future__ import annotations

import app.prestart as prestart


def test_prestart_returns_zero_on_success(monkeypatch):
    calls = []
    monkeypatch.setattr(prestart, "wait_for_db", lambda *a, **k: calls.append("wait"))
    # _bootstrap_schema_and_seed is imported inside main(); patch it at source.
    import app.main as main
    monkeypatch.setattr(main, "_bootstrap_schema_and_seed", lambda *a, **k: calls.append("boot"))
    assert prestart.main() == 0
    assert calls == ["wait", "boot"]  # DB wait happens BEFORE migrations


def test_prestart_returns_nonzero_when_migration_fails(monkeypatch, caplog):
    monkeypatch.setattr(prestart, "wait_for_db", lambda *a, **k: None)
    import app.main as main

    def _boom(*_a, **_k):
        raise RuntimeError("migration 099 exploded")

    monkeypatch.setattr(main, "_bootstrap_schema_and_seed", _boom)
    with caplog.at_level("ERROR"):
        rc = prestart.main()
    assert rc == 1  # non-zero → entrypoint `set -e` aborts the container
    assert "PRE-START FAILED" in caplog.text
    assert "migration 099 exploded" in caplog.text  # the real cause is surfaced


def test_prestart_returns_nonzero_when_db_never_ready(monkeypatch):
    def _never(*a, **k):
        raise TimeoutError("db unreachable")

    monkeypatch.setattr(prestart, "wait_for_db", _never)
    assert prestart.main() == 1


def test_wait_for_db_retries_then_succeeds(monkeypatch):
    """wait_for_db keeps trying until the DB accepts a connection."""
    import app.db.session as session

    attempts = {"n": 0}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise OSError("connection refused")
            return None

    monkeypatch.setattr(session.engine, "connect", lambda: _Conn())
    monkeypatch.setattr(prestart.time, "sleep", lambda *_a: None)  # don't actually wait
    prestart.wait_for_db(max_wait=30, interval=0)
    assert attempts["n"] == 3


def test_lifespan_skips_bootstrap_when_entrypoint_flag_set(monkeypatch):
    """With ENTRYPOINT_BOOTSTRAP=1 the lifespan must NOT redo the heavy bootstrap
    (the entrypoint pre-start already did it) — it only loads runtime config."""
    import asyncio

    import app.main as main

    monkeypatch.setenv("ENTRYPOINT_BOOTSTRAP", "1")
    called = {"boot": 0, "ai": 0}
    monkeypatch.setattr(main, "_bootstrap_schema_and_seed", lambda: called.__setitem__("boot", called["boot"] + 1))
    monkeypatch.setattr("app.core.ai_runtime.load_ai_config_from_db", lambda: called.__setitem__("ai", called["ai"] + 1))

    async def _drive():
        async with main.lifespan(main.app):
            pass

    asyncio.run(_drive())
    assert called["boot"] == 0  # skipped
    assert called["ai"] == 1    # runtime config still loaded in-process


def test_alembic_strict_reraises_but_tolerant_swallows(monkeypatch):
    """A broken migration must abort in strict mode (pre-start) and be tolerated
    in non-strict mode (dev self-heal). The idempotent fallback runs in both."""
    import alembic.command
    import pytest
    import sqlalchemy

    import app.main as main

    fallback = {"n": 0}
    for fn in (
        "_apply_numeric_migrations",
        "_apply_entity_cleanup_migrations",
        "_apply_transaction_fee_migrations",
        "_apply_user_migrations",
    ):
        monkeypatch.setattr(main, fn, lambda: fallback.__setitem__("n", fallback["n"] + 1))

    class _Insp:
        def has_table(self, _name):
            return True  # existing DB → takes the `upgrade` path

    monkeypatch.setattr(sqlalchemy, "inspect", lambda _e: _Insp())

    def _boom(*_a, **_k):
        raise RuntimeError("bad migration 099")

    monkeypatch.setattr(alembic.command, "upgrade", _boom)

    fallback["n"] = 0
    main._run_alembic_migrations(strict=False)  # tolerant: no raise
    assert fallback["n"] == 4  # fallback recovery still attempted

    fallback["n"] = 0
    with pytest.raises(RuntimeError, match="bad migration 099"):
        main._run_alembic_migrations(strict=True)  # strict: re-raises loudly
    assert fallback["n"] == 4  # fallback attempted BEFORE re-raising


def test_lifespan_runs_bootstrap_without_flag(monkeypatch):
    """Without the flag (e.g. `uvicorn` run directly) the lifespan self-heals by
    running the bootstrap itself."""
    import asyncio

    import app.main as main

    monkeypatch.delenv("ENTRYPOINT_BOOTSTRAP", raising=False)
    called = {"boot": 0}
    monkeypatch.setattr(main, "_bootstrap_schema_and_seed", lambda: called.__setitem__("boot", called["boot"] + 1))
    monkeypatch.setattr("app.core.ai_runtime.load_ai_config_from_db", lambda: None)

    async def _drive():
        async with main.lifespan(main.app):
            pass

    asyncio.run(_drive())
    assert called["boot"] == 1
