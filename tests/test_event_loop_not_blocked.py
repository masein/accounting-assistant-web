"""Security review 2026-09-24 M5: synchronous database and file-parsing work
ran inside async handlers and the async auth middleware, stalling the single
event loop for every user. Handlers that only awaited the upload read are now
plain functions (FastAPI runs them in a thread), and the middleware's DB
lookups go through run_in_threadpool."""
from __future__ import annotations

import inspect
from pathlib import Path

from app.api import migration, transactions
from app.main import app


def test_upload_and_parse_handlers_are_synchronous():
    for fn in (transactions.upload_attachment, transactions.excel_import_preview, migration.migration_import_preview):
        assert not inspect.iscoroutinefunction(fn), fn.__name__


def test_middleware_db_lookups_leave_the_event_loop():
    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "await run_in_threadpool(_session_is_valid, user)" in src
    assert "await run_in_threadpool(_resolve_api_key_actor, request)" in src


def test_uploads_still_work_as_sync_handlers(auth_client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    r = auth_client.post("/transactions/attachments", files={"file": ("r.png", png, "image/png")})
    assert r.status_code == 201, r.text
    r2 = auth_client.post("/transactions/excel-import/preview", files={"file": ("x.txt", b"nope", "text/plain")})
    assert r2.status_code == 400  # wrong type is still refused by the handler
