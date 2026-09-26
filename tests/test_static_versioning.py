"""Static asset cache-busting.

index.html pulls js/01-core.js … js/16-boot.js as plain <script> tags whose
load order is load-bearing, so a browser holding a stale copy of one file is a
broken app. Every local js/css reference is stamped with ?v=<content hash> at
serve time: a changed file gets a new URL, an unchanged one stays cached.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app.main import STATIC_DIR, _asset_version, render_versioned_html

REF_RE = re.compile(r'(?:src|href)="/static/([A-Za-z0-9][A-Za-z0-9._/-]*\.(?:js|css))(\?v=([0-9a-f]+))?"')


def _content_hash(rel_path: str) -> str:
    return hashlib.sha256((STATIC_DIR / rel_path).read_bytes()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# The rendered page
# ---------------------------------------------------------------------------
def test_every_local_js_css_reference_is_versioned():
    html = render_versioned_html("index.html")
    refs = REF_RE.findall(html)
    assert refs, "index.html should reference at least one local js/css asset"
    unversioned = [path for path, qs, _ in refs if not qs]
    assert not unversioned, f"unversioned static references: {unversioned}"


def test_version_matches_the_file_content_hash():
    html = render_versioned_html("index.html")
    checked = 0
    for path, _qs, version in REF_RE.findall(html):
        assert version == _content_hash(path), f"{path}: stale/incorrect version"
        checked += 1
    assert checked >= 17  # 16 js chunks + app.css, plus vendored libs


def test_all_split_js_chunks_are_referenced_in_order():
    """The split is only safe if every chunk is loaded, in numeric order, with
    16-boot.js last (hoisting does not cross <script> boundaries)."""
    html = render_versioned_html("index.html")
    chunks = [p for p, _q, _v in REF_RE.findall(html) if p.startswith("js/")]
    on_disk = sorted(p.name for p in (STATIC_DIR / "js").glob("*.js"))
    assert [c.split("/", 1)[1] for c in chunks] == on_disk
    assert chunks[-1].endswith("16-boot.js")


def test_served_index_is_no_store_and_versioned(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    assert "/static/js/01-core.js?v=" in resp.text
    assert '/static/js/01-core.js"' not in resp.text  # no bare reference left


def test_login_page_still_renders(client):
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    assert "<form" in resp.text or "login" in resp.text.lower()


# ---------------------------------------------------------------------------
# The hashing helper
# ---------------------------------------------------------------------------
def test_version_changes_when_content_changes(tmp_path: Path):
    """A rewritten asset must produce a new version even inside the same
    second — the memo key is (mtime, hash) and is refreshed on mtime change."""
    asset = STATIC_DIR / "css" / "__probe__.css"
    try:
        asset.write_text("a{}", encoding="utf-8")
        first = _asset_version("css/__probe__.css")
        assert first == hashlib.sha256(b"a{}").hexdigest()[:8]

        import os
        asset.write_text("b{color:red}", encoding="utf-8")
        os.utime(asset, (0, 0))  # force a distinct mtime, defeating the memo
        second = _asset_version("css/__probe__.css")
        assert second != first
        assert second == hashlib.sha256(b"b{color:red}").hexdigest()[:8]
    finally:
        asset.unlink(missing_ok=True)


def test_unknown_asset_is_left_untouched():
    assert _asset_version("js/does-not-exist.js") is None
    html = render_versioned_html("index.html")
    # a reference the resolver can't hash must survive verbatim, not be dropped
    assert "/static/js/01-core.js?v=" in html


@pytest.mark.parametrize("evil", ["../main.py", "js/../../main.py", "../../etc/passwd"])
def test_asset_version_refuses_paths_outside_static(evil):
    assert _asset_version(evil) is None


# ---------------------------------------------------------------------------
# Transfer size and caching (roadmap 2026-09 §2.6)
# ---------------------------------------------------------------------------
def test_scripts_are_gzipped_when_the_browser_accepts_it(client):
    raw = (STATIC_DIR / "js" / "02-i18n.js").read_bytes()
    r = client.get("/static/js/02-i18n.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert r.num_bytes_downloaded < len(raw) / 3               # compressed on the wire
    assert r.content == raw                                    # and identical once decoded


def test_no_gzip_without_accept_encoding(client):
    r = client.get("/static/js/02-i18n.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers


def test_versioned_assets_are_immutable_bare_ones_revalidate(client):
    v = _content_hash("js/01-core.js")
    r = client.get(f"/static/js/01-core.js?v={v}")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    r = client.get("/static/js/01-core.js")
    assert r.headers["cache-control"] == "no-cache"
    etag = r.headers.get("etag")
    assert etag
    r = client.get("/static/js/01-core.js", headers={"If-None-Match": etag})
    assert r.status_code == 304


def test_every_reference_on_the_page_gets_the_long_cache(client):
    html = render_versioned_html("index.html")
    for path, qs, _v in REF_RE.findall(html)[:5]:
        r = client.get(f"/static/{path}{qs}")
        assert r.status_code == 200 and "immutable" in r.headers["cache-control"], path


def test_the_pages_themselves_are_never_cached(auth_client, client):
    assert "no-store" in auth_client.get("/").headers["cache-control"]
    assert "no-store" in client.get("/login").headers["cache-control"]


def test_large_api_responses_are_compressed_small_ones_are_not(auth_client):
    big = auth_client.get("/accounts", headers={"Accept-Encoding": "gzip"})
    assert big.status_code == 200
    if len(big.content) > 1024:
        assert big.headers.get("content-encoding") == "gzip"
    small = auth_client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert len(small.content) < 1024 and "content-encoding" not in small.headers
