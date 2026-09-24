"""AI provider config is platform-wide and super-admin only (decided after
the 2026-09-24 QA run)."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.models.app_setting import AppSetting


def _client_as(client, role: str, superadmin: bool):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    token = create_session_token(user_id=str(uuid.uuid4()), username=f"{role}-u", is_admin=(role == "owner"),
                                 role=role, is_superadmin=superadmin, company_id=str(uuid.uuid4()))
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_only_the_superadmin_reaches_ai_config(client):
    owner = _client_as(client, "owner", superadmin=False)
    for path in ("/admin/ai-config", "/admin/anthropic-config", "/admin/chat-provider-shape"):
        assert owner.get(path).status_code == 403, path
    assert owner.patch("/admin/ai-config", json={"model": "gpt-4.1-mini"}).status_code == 403

    client.cookies.clear()
    sa = _client_as(client, "owner", superadmin=True)
    for path in ("/admin/ai-config", "/admin/anthropic-config", "/admin/chat-provider-shape"):
        assert sa.get(path).status_code == 200, path


def test_config_is_persisted_and_loaded_as_one_platform_row(db, monkeypatch):
    from app.core import ai_runtime
    from app.db import session as db_session
    from app.db.tenant import tenant_bypass, use_company
    from tests.conftest import _TestSession

    # ai_runtime opens its own sessions — point them at the test DB, never at
    # the developer's Postgres.
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)

    # Save from inside a tenant context → the row must still be platform-wide.
    with use_company(str(uuid.uuid4())):
        ai_runtime.update_ai_config(provider="metis", model="gpt-4.1-mini")
    with tenant_bypass():
        rows = db.execute(select(AppSetting).where(AppSetting.key == ai_runtime._DB_KEY)).scalars().all()
    platform = [r for r in rows if r.company_id is None]
    assert len(platform) == 1
    assert json.loads(platform[0].value)["metis"]["model"] == "gpt-4.1-mini"

    # Loading from another tenant context sees the same platform row.
    ai_runtime._state["metis"]["model"] = "scrambled"
    with use_company(str(uuid.uuid4())):
        ai_runtime.load_ai_config_from_db()
    assert ai_runtime.resolve_active_ai_backend()["model"] == "gpt-4.1-mini"
