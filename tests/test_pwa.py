"""Installable app (roadmap 2026-09 §4.10, part 1): the manifest and its
icons, the service worker (versioned, static-only caching, offline page,
share target), the offline page, the share-target fallback, and the page
hooks — camera capture, install prompt, shared-file pickup."""
from __future__ import annotations

import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, app_build_version

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def _anon():
    return TestClient(app)          # no session: the browser fetches these before sign-in


def test_the_manifest():
    r = _anon().get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert (m["name"], m["short_name"], m["display"], m["scope"]) == ("Accounting Assistant", "Accounting", "standalone", "/")
    assert m["start_url"].startswith("/") and m["theme_color"] == "#006d77"
    purposes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= purposes
    share = m["share_target"]
    assert (share["action"], share["method"], share["enctype"]) == ("/share-target", "POST", "multipart/form-data")
    assert share["params"]["files"][0]["name"] == "file"


def test_every_icon_is_there_at_its_size():
    c = _anon()
    m = c.get("/manifest.webmanifest").json()
    for icon in m["icons"] + [{"src": "/static/pwa/apple-touch-icon.png", "sizes": "180x180"}]:
        r = c.get(icon["src"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", icon
        w, h = _png_size(r.content)
        assert f"{w}x{h}" == icon["sizes"], icon


def test_the_service_worker_is_served_from_the_root_and_versioned():
    r = _anon().get("/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert r.headers["cache-control"] == "no-cache" and r.headers["service-worker-allowed"] == "/"
    version = app_build_version()
    assert re.fullmatch(r"[0-9a-f]{12}", version)
    assert f"const VERSION = '{version}';" in r.text and "__VERSION__" not in r.text
    assert app_build_version() == version                             # stable between requests


def test_the_build_version_follows_the_assets(monkeypatch):
    import app.main as m
    before = app_build_version()
    real = m._asset_version
    monkeypatch.setattr(m, "_asset_version", lambda p: "changed" if p.endswith("01-core.js") else real(p))
    assert app_build_version() != before


def test_the_service_worker_caches_nothing_but_static_files():
    sw = (STATIC / "pwa" / "sw.js").read_text(encoding="utf-8")
    body = sw.split("self.addEventListener('fetch'", 1)[1]
    navigate = body.split("if (req.mode === 'navigate')", 1)[1].split("return;", 1)[0]
    assert "cache.put" not in navigate and "OFFLINE_URL" in navigate      # pages: network, offline fallback
    static = body.split("url.pathname.startsWith('/static/') && url.searchParams.has('v')", 1)[1]
    assert "cache.put(req" in static                                      # only versioned static files
    assert body.count("cache.put") == 1
    assert "/api" not in sw.split("*/", 1)[1] and "/transactions" not in sw


def test_the_offline_page():
    r = _anon().get("/offline")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "<script" not in html and 'src="data:image/png;base64,' in html   # works with nothing cached
    for text in ("You're offline", "اتصال اینترنت برقرار نیست", "Sin conexión", "لا يوجد اتصال"):
        assert text in html


def test_a_share_before_the_worker_is_installed_opens_the_chat():
    r = _anon().post("/share-target", files={"file": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")},
                     follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#ai-accountant"


def test_the_pages_link_the_manifest_and_icons():
    for name in ("index.html", "login.html"):
        html = (STATIC / name).read_text(encoding="utf-8")
        assert '<link rel="manifest" href="/manifest.webmanifest">' in html, name
        assert 'rel="apple-touch-icon" href="/static/pwa/apple-touch-icon.png"' in html, name


def test_the_page_hooks():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ui = (STATIC / "js" / "03-ui.js").read_text(encoding="utf-8")
    chat = (STATIC / "js" / "15-ai-chat.js").read_text(encoding="utf-8")
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert 'id="ai-acct-camera" accept="image/*" capture="environment"' in html
    assert 'id="ai-acct-camera-btn"' in html and 'id="install-app-btn"' in html
    assert "@media (pointer: coarse) { .ai-camera-btn { display: inline-flex; } }" in css
    pwa = ui.split("// ═══════ Installable app", 1)[1]
    assert "window.isSecureContext" in pwa and "register('/sw.js', { scope: '/' })" in pwa
    assert "beforeinstallprompt" in pwa and "onclick" not in pwa
    assert "async function shrinkImage(file)" in chat and "file = await shrinkImage(file);" in chat
    assert "caches.open('shared-files')" in chat and "caches.open(SHARE_CACHE)" in \
        (STATIC / "pwa" / "sw.js").read_text(encoding="utf-8")


def test_the_shell_routes_are_exactly_the_public_ones():
    """They skip the session guard on purpose — so they must stay data-free."""
    from app.main import APP_SHELL_PATHS
    assert APP_SHELL_PATHS == ("/sw.js", "/manifest.webmanifest", "/offline", "/share-target")
    c = _anon()
    for path in ("/sw.js", "/manifest.webmanifest", "/offline"):
        r = c.get(path)
        assert r.status_code == 200 and "set-cookie" not in r.headers, path
