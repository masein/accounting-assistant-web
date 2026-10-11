"""Files, voice notes and the opening briefing on the phone (roadmap
ROADMAP_ANDROID_CHAT P0.7; scenarios N15, N16)."""
from __future__ import annotations

import base64

from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"
# a 1×1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_n15_a_photo_is_uploaded_and_a_wrong_file_refused(client, co, phone):
    r = client.post(f"{API}/uploads", headers=phone, files={"file": ("receipt.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] and body["content_type"] == "image/png" and body["size_bytes"] == len(PNG)
    # bytes that aren't the type they claim
    r = client.post(f"{API}/uploads", headers=phone, files={"file": ("receipt.png", b"not a picture", "image/png")})
    assert r.status_code in (400, 415, 422)
    r = client.post(f"{API}/uploads", headers=phone, files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert r.status_code == 400


def test_n15_a_voice_note_reaches_the_transcriber(client, co, phone):
    # an empty recording is refused by the transcriber itself, before any AI call
    r = client.post(f"{API}/transcribe", headers=phone, files={"file": ("note.m4a", b"", "audio/mp4")})
    assert r.status_code == 422 and "empty" in r.json()["detail"].lower()


def test_n16_the_briefing_speaks_first_and_is_kept_in_the_thread(client, co, phone, monkeypatch):
    from app.services import insight_service
    monkeypatch.setattr(insight_service, "compute_insights", lambda db, **kw: ["one"])
    monkeypatch.setattr(insight_service, "briefing_text", lambda insights, lang: "Two invoices are overdue: 360,000,000 IRR.")
    r = client.post(f"{API}/briefing", headers=phone, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(b["type"], b["kind"], b["text"]) for b in body["blocks"]] == [
        ("text", "briefing", "Two invoices are overdue: 360,000,000 IRR.")]
    msgs = client.get(f"{API}/threads/{body['thread_id']}/messages", headers=phone).json()
    assert msgs[-1]["blocks"][0]["kind"] == "briefing"


def test_n16_nothing_to_say_is_said_with_nothing(client, co, phone, monkeypatch):
    from app.services import insight_service
    monkeypatch.setattr(insight_service, "compute_insights", lambda db, **kw: [])
    monkeypatch.setattr(insight_service, "briefing_text", lambda insights, lang: None)
    r = client.post(f"{API}/briefing", headers=phone, json={})
    assert r.status_code == 200 and r.json() == {"thread_id": None, "blocks": []}


def test_n39_a_file_only_message_comes_back_as_its_files_not_the_models_prompt(client, db, co, phone, monkeypatch):
    # opened again, a receipt's message used to read "Attached document OCR (attachment_id=…" — the
    # prompt written for the model — and a statement's an empty bubble
    from functools import partial

    from app.api import ai_accountant as api_mod
    from app.services import ocr_extract
    from tests.test_ai_guardrails import _FakeClient, _text

    async def unread(path, content_type):
        return {}
    monkeypatch.setattr(ocr_extract, "extract_from_attachment", unread)
    monkeypatch.setattr(api_mod, "run_chat_turn", partial(api_mod.run_chat_turn, client=_FakeClient([_text("I couldn't read it.")])))
    att = client.post(f"{API}/uploads", headers=phone, files={"file": ("receipt.png", PNG, "image/png")}).json()["id"]
    r = client.post(f"{API}/chat", headers=phone, json={"message": "", "attachment_ids": [att], "client_message_id": "m-77"})
    assert r.status_code == 200, r.text
    thread = r.json()["thread_id"]
    mine = [m for m in client.get(f"{API}/threads/{thread}/messages", headers=phone).json() if m["role"] == "user"]
    assert (mine[-1]["text"], mine[-1]["client_message_id"]) == ("", "m-77")
    assert mine[-1]["files"] == [{"name": "receipt.png", "content_type": "image/png"}]
    # the model still sees the document when the conversation goes on
    import uuid as _uuid

    from app.db.tenant import use_company
    from app.services.ai_accountant.orchestrator import _replay_history
    with use_company(co["cid"]):
        replay = _replay_history(db, _uuid.UUID(thread))
    assert any("Attached document OCR" in (m.text or "") for m in replay if m.role == "user")


def test_n39_a_statement_sent_alone_keeps_its_name(client, co, phone):
    from tests.test_chat_statement_intake import STATEMENT_CSV
    att = client.post(f"{API}/uploads", headers=phone, files={"file": ("mellat-mehr.csv", STATEMENT_CSV, "text/csv")}).json()["id"]
    r = client.post(f"{API}/chat", headers=phone, json={"message": "", "attachment_ids": [att]})
    assert r.status_code == 200, r.text
    mine = [m for m in client.get(f"{API}/threads/{r.json()['thread_id']}/messages", headers=phone).json() if m["role"] == "user"]
    assert mine[-1]["files"] == [{"name": "mellat-mehr.csv", "content_type": "text/csv"}]
