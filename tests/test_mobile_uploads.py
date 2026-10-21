"""A file sent in pieces over a poor line (scenario N37; roadmap
ROADMAP_ANDROID_CHAT P0.7): it goes on after a drop from what the server
kept, a stray piece writes nothing, the same file isn't sent twice, a
damaged one starts over, and each upload is its user's."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.db.tenant import use_company
from app.models.mobile_upload import MobileUpload
from app.models.transaction import TransactionAttachment
from app.services import mobile_uploads as uploads
from tests.test_ai_guardrails import _calls, _text, _txn, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone, scripted  # noqa: F401  (fixtures)
from tests.test_mobile_edit_approvals import _phone_for

API = "/api/mobile/v1"
PIECE = 256 * 1024


def _pdf(size: int) -> bytes:
    head = b"%PDF-1.4\n"
    return head + os.urandom(size - len(head))          # random, so each test's file is its own


def _photo(size: int) -> bytes:
    head = b"\x89PNG\r\n\x1a\n"
    return head + os.urandom(size - len(head))


def _begin(client, headers, data: bytes, name="statement.pdf", kind="application/pdf", sha=None):
    return client.post(f"{API}/uploads/begin", headers=headers, json={
        "file_name": name, "content_type": kind, "size_bytes": len(data),
        "sha256": sha or hashlib.sha256(data).hexdigest()})


def _put(client, headers, upload_id: str, offset: int, piece: bytes):
    return client.put(f"{API}/uploads/{upload_id}?offset={offset}", headers=headers, content=piece)


def _send(client, headers, data: bytes, state: dict) -> dict:
    """What the phone does: each piece from where the server says it stands."""
    while not state["done"]:
        at = state["received"]
        r = _put(client, headers, state["id"], at, data[at:at + PIECE])
        assert r.status_code == 200, r.text
        state = r.json()
    return state


def _row(db, co, upload_id) -> MobileUpload | None:
    with use_company(co["cid"]):
        db.expire_all()
        return db.get(MobileUpload, uuid.UUID(upload_id))


def test_n37_pieces_make_one_attachment_and_a_drop_goes_on_where_it_stopped(client, db, co, phone):
    data = _pdf(2 * PIECE + 75_000)
    first = _begin(client, phone, data).json()
    assert (first["received"], first["max_chunk"], first["done"], first["attachment"]) == (0, uploads.MAX_CHUNK, False, None)
    r = _put(client, phone, first["id"], 0, data[:PIECE])
    assert r.status_code == 200 and r.json()["received"] == PIECE
    # the line drops; the phone begins again with the same hash and goes on from what arrived
    again = _begin(client, phone, data).json()
    assert (again["id"], again["received"]) == (first["id"], PIECE)
    done = _send(client, phone, data, again)
    att = done["attachment"]
    assert done["received"] == len(data)
    assert (att["file_name"], att["content_type"], att["size_bytes"]) == ("statement.pdf", "application/pdf", len(data))
    with use_company(co["cid"]):
        stored = db.get(TransactionAttachment, uuid.UUID(att["id"]))
        assert Path(stored.file_path).read_bytes() == data and stored.transaction_id is None
    assert not uploads._part(_row(db, co, first["id"])).exists()


def test_n37_a_piece_anywhere_else_writes_nothing_and_says_where_to_go_on(client, db, co, phone):
    data = _pdf(2 * PIECE + 10)
    state = _begin(client, phone, data).json()
    assert _put(client, phone, state["id"], 0, data[:PIECE]).json()["received"] == PIECE
    # the same piece again (its reply was lost), then one after a gap: neither is written
    assert _put(client, phone, state["id"], 0, data[:PIECE]).json()["received"] == PIECE
    assert _put(client, phone, state["id"], 2 * PIECE, data[2 * PIECE:]).json()["received"] == PIECE
    done = _send(client, phone, data, _begin(client, phone, data).json())
    with use_company(co["cid"]):
        assert Path(db.get(TransactionAttachment, uuid.UUID(done["attachment"]["id"])).file_path).read_bytes() == data
    # a repeat of the last piece after it finished changes nothing either
    r = _put(client, phone, state["id"], 2 * PIECE, data[2 * PIECE:])
    assert r.status_code == 200 and r.json()["attachment"]["id"] == done["attachment"]["id"]


def test_n37_the_same_file_again_sends_nothing_until_it_belongs_to_a_voucher(client, db, co, phone, scripted, monkeypatch):
    from app.services import ocr_extract

    async def unread(path, content_type):
        return {}
    monkeypatch.setattr(ocr_extract, "extract_from_attachment", unread)
    data = _photo(PIECE + 5)
    done = _send(client, phone, data, _begin(client, phone, data, name="receipt.png", kind="image/png").json())
    att = done["attachment"]["id"]
    again = _begin(client, phone, data, name="receipt.png", kind="image/png").json()
    assert (again["done"], again["id"], again["attachment"]["id"]) == (True, done["id"], att)
    # the file goes with a message and its voucher is confirmed: it belongs to that voucher now
    scripted(_calls(_txn(80_000_000, desc="Office rent")), _text("Drafted it."))
    r = client.post(f"{API}/chat", headers=phone, json={"message": "record this receipt", "attachment_ids": [att]})
    assert r.status_code == 200, r.text
    token = next(b["token"] for b in r.json()["blocks"] if b["type"] == "proposal")
    assert client.post(f"{API}/proposals/{token}/confirm", headers=phone).status_code == 200
    with use_company(co["cid"]):
        db.expire_all()
        assert db.get(TransactionAttachment, uuid.UUID(att)).transaction_id is not None
    fresh = _begin(client, phone, data, name="receipt.png", kind="image/png").json()
    assert (fresh["done"], fresh["received"]) == (False, 0) and fresh["id"] != done["id"]


def test_n37_a_file_that_does_not_match_its_hash_starts_over(client, db, co, phone):
    data = _pdf(PIECE + 100)
    state = _begin(client, phone, data).json()
    _put(client, phone, state["id"], 0, data[:PIECE])
    r = _put(client, phone, state["id"], PIECE, b"x" * 100)         # damaged on the way
    assert r.status_code == 422 and r.headers.get("X-Error-Code") == "upload_corrupt"
    again = _begin(client, phone, data).json()
    assert (again["id"], again["received"]) == (state["id"], 0)
    assert not uploads._part(_row(db, co, state["id"])).exists()
    assert _send(client, phone, data, again)["done"]


def test_n37_what_is_refused_before_and_after_the_bytes(client, db, co, phone):
    assert _begin(client, phone, b"", sha="0" * 64).status_code == 422          # nothing to send
    r = client.post(f"{API}/uploads/begin", headers=phone, json={
        "file_name": "huge.pdf", "content_type": "application/pdf", "size_bytes": 21 * 1024 * 1024, "sha256": "a" * 64})
    assert r.status_code == 400 and "20 MB" in r.json()["detail"]
    r = client.post(f"{API}/uploads/begin", headers=phone, json={                 # AJ10: a 10.4 MB bank scan is taken
        "file_name": "20261015_133322_receipt.pdf", "content_type": "application/pdf", "size_bytes": 10_400_000,
        "sha256": "b" * 64})
    assert r.status_code in (200, 201), r.text
    assert _begin(client, phone, b"MZ..", name="x.exe", kind="application/x-msdownload").status_code == 400
    assert _begin(client, phone, b"abcd", sha="not-a-hash").status_code == 422
    data = _pdf(PIECE + 10)
    state = _begin(client, phone, data).json()
    assert _put(client, phone, state["id"], 0, b"\0" * (uploads.MAX_CHUNK + 1)).status_code == 413
    assert _put(client, phone, state["id"], 0, data + b"extra").status_code == 400     # more than the file
    assert _put(client, phone, "not-an-id", 0, b"x").status_code == 404
    # the hash matches but the bytes aren't a PDF: refused at the end, and nothing is kept
    fake = b"<html>" + os.urandom(100)
    fs = _begin(client, phone, fake).json()
    r = _put(client, phone, fs["id"], 0, fake)
    assert r.status_code == 400
    assert _row(db, co, fs["id"]) is None


def test_n37_nobody_can_resume_or_add_to_someone_elses_upload(client, db, co, phone):
    data = _pdf(PIECE + 10)
    mine = _begin(client, phone, data).json()
    _put(client, phone, mine["id"], 0, data[:PIECE])
    cfo = _phone_for(client, co["cfo"])
    assert _put(client, cfo, mine["id"], PIECE, data[PIECE:]).status_code == 404
    theirs = _begin(client, cfo, data).json()
    assert theirs["id"] != mine["id"] and theirs["received"] == 0
    assert _row(db, co, mine["id"]).received == PIECE


def test_n37_nothing_unfinished_is_kept_after_a_day(client, db, co, phone):
    data = _pdf(PIECE + 10)
    old = _begin(client, phone, data).json()
    _put(client, phone, old["id"], 0, data[:PIECE])
    part = uploads._part(_row(db, co, old["id"]))
    assert part.exists()
    with use_company(co["cid"]):
        db.execute(update(MobileUpload).where(MobileUpload.id == uuid.UUID(old["id"]))
                   .values(updated_at=uploads._now() - timedelta(days=2)))
        db.commit()
    _begin(client, phone, _pdf(100))                                     # any begin sweeps
    assert _row(db, co, old["id"]) is None and not part.exists()
    fresh = _begin(client, phone, data).json()
    assert fresh["id"] != old["id"] and fresh["received"] == 0
    with use_company(co["cid"]):
        assert db.execute(select(MobileUpload).where(MobileUpload.sha256 == hashlib.sha256(data).hexdigest())).scalars().all()


def test_n37_a_csv_under_androids_own_name_is_a_csv(client, db, co, phone):
    # Android's files in Downloads call a CSV text/comma-separated-values; the name may have no extension
    data = "Date,Description,Debit,Credit,Balance\n2026-09-01,کارمزد بانکی,90000,0,9910000\n".encode()
    state = _begin(client, phone, data, name="statement", kind="text/comma-separated-values").json()
    att = _send(client, phone, data, state)["attachment"]
    assert (att["content_type"], att["file_name"]) == ("text/csv", "statement")
    r = client.post(f"{API}/uploads", headers=phone,
                    files={"file": ("mellat.csv", data, "text/comma-separated-values; charset=utf-8")})
    assert r.status_code == 201 and r.json()["content_type"] == "text/csv"


def test_n37_a_file_the_server_lost_is_sent_again_not_pointed_at(client, db, co, phone):
    # the 2026-10-11 pass: the attachment's file was gone, the same file "sent" again pointed at it, and the
    # statement never reached the intake
    data = _pdf(PIECE + 10)
    done = _send(client, phone, data, _begin(client, phone, data).json())
    with use_company(co["cid"]):
        Path(db.get(TransactionAttachment, uuid.UUID(done["attachment"]["id"])).file_path).unlink()
    again = _begin(client, phone, data).json()
    assert (again["done"], again["received"]) == (False, 0) and again["id"] != done["id"]
    fresh = _send(client, phone, data, again)
    with use_company(co["cid"]):
        assert Path(db.get(TransactionAttachment, uuid.UUID(fresh["attachment"]["id"])).file_path).read_bytes() == data
