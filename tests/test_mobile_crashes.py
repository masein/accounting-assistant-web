"""The app's crash reports (scenario N30; roadmap ROADMAP_ANDROID_CHAT P1.8):
where it crashed, never what it said."""
from __future__ import annotations

import logging

from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _report(**over):
    r = {"at": "2026-10-10T16:00:00Z", "app_version": "0.1.0", "android": 36, "device": "Google Pixel 8",
         "thread": "main", "exception": "java.lang.IllegalArgumentException",
         "causes": ["java.lang.NumberFormatException"],
         "frames": ["app.accountingassistant.android.ui.chat.EditSheetKt.editChange(EditSheet.kt:52)",
                    "androidx.compose.runtime.Recomposer.runRecomposeAndApplyChanges(Recomposer.kt:640)"]}
    return {**r, **over}


def test_n30_a_crash_is_logged_with_where_it_happened(client, co, phone, caplog):
    with caplog.at_level(logging.WARNING, logger="app.mobile.crash"):
        r = client.post(f"{API}/crashes", headers=phone, json={"reports": [_report(), _report(android=26)]})
    assert r.status_code == 200 and r.json() == {"received": 2}
    crashes = [rec.crash for rec in caplog.records if rec.getMessage() == "mobile_crash"]
    assert len(crashes) == 2
    first = crashes[0]
    assert first["exception"] == "java.lang.IllegalArgumentException"
    assert first["where"] == "app.accountingassistant.android.ui.chat.EditSheetKt.editChange(EditSheet.kt:52)"
    assert first["user"] == co["owner"].username and first["device_id"]


def test_n30_no_words_get_in(client, co, phone, caplog):
    # a "message" isn't part of a report, and a frame that isn't a frame (typed words) is dropped
    sneaky = _report(message="اجاره ۸۰ میلیون", frames=["For input string: '85000000'", "اجاره.هزینه(سند.kt:1)",
                                                       "app.accountingassistant.android.MainActivity.onCreate(MainActivity.kt:9)"])
    with caplog.at_level(logging.WARNING, logger="app.mobile.crash"):
        assert client.post(f"{API}/crashes", headers=phone, json={"reports": [sneaky]}).status_code == 200
    crash = next(rec.crash for rec in caplog.records if rec.getMessage() == "mobile_crash")
    assert "message" not in crash and "اجاره" not in str(crash) and "85000000" not in str(crash)
    assert crash["frames"] == ["app.accountingassistant.android.MainActivity.onCreate(MainActivity.kt:9)"]


def test_n30_limits_and_a_session(client, co, phone):
    assert client.post(f"{API}/crashes", headers=phone, json={"reports": [_report()] * 6}).status_code == 422
    assert client.post(f"{API}/crashes", headers=phone, json={"reports": []}).status_code == 422
    assert client.post(f"{API}/crashes", headers=phone, json={"reports": [_report(exception="x" * 300)]}).status_code == 422
    client.cookies.clear()
    assert client.post(f"{API}/crashes", json={"reports": [_report()]}).status_code == 401
