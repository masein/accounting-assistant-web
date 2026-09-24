"""Security review 2026-09-24 M10: AI provider keys were persisted in
app_settings as plain text and the database copy silently overrode a key
rotated in .env. Keys are now Fernet-encrypted at rest (keyed from
AUTH_SECRET) and the environment's key wins on load."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.core import ai_runtime
from app.core.secrets import decrypt_secret, encrypt_secret, is_encrypted
from app.db.tenant import tenant_bypass
from app.models.app_setting import AppSetting


@pytest.fixture()
def runtime_db(db, monkeypatch):
    from app.db import session as db_session
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)
    saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ai_runtime._state.items()}
    saved_env = dict(ai_runtime._ENV_KEYS)
    yield db
    with ai_runtime._lock:
        ai_runtime._state.clear(); ai_runtime._state.update(saved)
        ai_runtime._ENV_KEYS.clear(); ai_runtime._ENV_KEYS.update(saved_env)


def _row(db):
    with tenant_bypass():
        return db.execute(select(AppSetting).where(AppSetting.key == ai_runtime._DB_KEY, AppSetting.company_id.is_(None))).scalars().first()


def test_secret_helper_round_trips_and_tolerates_legacy_and_garbage():
    token = encrypt_secret("tpsg-abc123")
    assert is_encrypted(token) and "tpsg-abc123" not in token
    assert decrypt_secret(token) == "tpsg-abc123"
    assert decrypt_secret("legacy-plain") == "legacy-plain"  # pass-through, re-saved encrypted on load
    assert decrypt_secret("enc:v1:not-a-token") == ""
    assert encrypt_secret("") == "" and decrypt_secret("") == ""


def test_persisted_config_never_holds_a_plain_key(runtime_db):
    secret = f"tpsg-{uuid.uuid4().hex}"
    ai_runtime.update_ai_config(provider="metis", model="gpt-4.1-mini", api_key=secret)
    row = _row(runtime_db)
    runtime_db.refresh(row)
    assert secret not in row.value
    stored = json.loads(row.value)
    assert is_encrypted(stored["metis"]["api_key"])
    assert decrypt_secret(stored["metis"]["api_key"]) == secret
    assert stored["metis"]["model"] == "gpt-4.1-mini"  # non-secret fields stay readable


def test_load_decrypts_and_the_env_key_wins(runtime_db, monkeypatch):
    secret = f"tpsg-{uuid.uuid4().hex}"
    ai_runtime.update_ai_config(provider="metis", api_key=secret)
    # no env key → the stored (decrypted) key is used
    ai_runtime._ENV_KEYS["metis"] = ""
    ai_runtime._state["metis"]["api_key"] = "scrambled"
    ai_runtime.load_ai_config_from_db()
    assert ai_runtime._state["metis"]["api_key"] == secret
    # env key present → it wins over the database copy
    ai_runtime._ENV_KEYS["metis"] = "env-rotated-key"
    ai_runtime._state["metis"]["api_key"] = "env-rotated-key"
    ai_runtime.load_ai_config_from_db()
    assert ai_runtime._state["metis"]["api_key"] == "env-rotated-key"


def test_legacy_plaintext_row_is_loaded_once_and_rewritten_encrypted(runtime_db):
    ai_runtime._ENV_KEYS["custom"] = ""
    legacy = f"legacy-{uuid.uuid4().hex[:8]}"
    row = _row(runtime_db)
    data = json.loads(row.value) if row else {"provider": "metis"}
    data["custom"] = {"base_url": "https://x.example/v1", "model": "m", "api_key": legacy, "api_key_header": "Authorization", "api_key_prefix": "Bearer"}
    with tenant_bypass():
        if row is None:
            runtime_db.add(AppSetting(key=ai_runtime._DB_KEY, value=json.dumps(data), company_id=None))
        else:
            row.value = json.dumps(data)
        runtime_db.commit()
    ai_runtime.load_ai_config_from_db()
    assert ai_runtime._state["custom"]["api_key"] == legacy
    runtime_db.expire_all()
    rewritten = json.loads(_row(runtime_db).value)
    assert is_encrypted(rewritten["custom"]["api_key"]) and legacy not in _row(runtime_db).value
