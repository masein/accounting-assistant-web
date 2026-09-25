"""Strict Content-Security-Policy (roadmap 2026-09 §1.13): no inline script
of any kind, so an injected <script> or on…= attribute never runs.

The policy is only safe if the front end has no inline code left — these
tests scan every served page and script so a new inline handler fails CI
instead of silently breaking a button in production."""
from __future__ import annotations

import re

import pytest

from app.main import _CSP_POLICY, STATIC_DIR, render_versioned_html

HTML_PAGES = ("index.html", "login.html")
SCRIPT_FILES = sorted([*STATIC_DIR.glob("*.js"), *(STATIC_DIR / "js").glob("*.js")])
OWN_SCRIPTS = [p for p in SCRIPT_FILES if not p.name.endswith(".min.js")]
# on + event name + =, as an HTML attribute (inside markup or a template string)
INLINE_HANDLER = re.compile(r"""<[^>]*\son[a-z]+\s*=\s*["'{]""", re.I | re.S)
ATTR_HANDLER = re.compile(r"""\son(?:click|change|input|submit|error|load|keydown|keyup|keypress|blur|focus|mouse\w+|pointer\w+|touch\w+|drag\w*|drop|wheel|scroll|contextmenu|dblclick)\s*=\s*["']""", re.I)


def _directives() -> dict[str, list[str]]:
    out = {}
    for part in _CSP_POLICY.split(";"):
        bits = part.split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


# --- the policy ----------------------------------------------------------------------

def test_scripts_only_from_this_origin():
    d = _directives()
    assert d["script-src"] == ["'self'"]
    assert d["script-src-attr"] == ["'none'"]
    assert "'unsafe-eval'" not in _CSP_POLICY


@pytest.mark.parametrize("directive, value", [
    ("object-src", "'none'"), ("base-uri", "'self'"), ("frame-ancestors", "'none'"),
    ("form-action", "'self'"), ("default-src", "'self'"), ("connect-src", "'self'"),
])
def test_locked_down_directives(directive, value):
    assert _directives()[directive] == [value]


@pytest.mark.parametrize("path", ["/login"])
def test_pages_are_served_with_the_policy(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == _CSP_POLICY


def test_app_shell_is_served_with_the_policy(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    assert "script-src 'self';" in r.headers["content-security-policy"]


# --- nothing inline left ---------------------------------------------------------------

@pytest.mark.parametrize("page", HTML_PAGES)
def test_pages_have_no_inline_scripts(page):
    html = render_versioned_html(page)
    for tag in re.findall(r"<script\b[^>]*>", html, re.I):
        assert "src=" in tag, f"{page}: inline <script> {tag}"
    bodies = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.I | re.S)
    assert all(not b.strip() for b in bodies), f"{page}: code inside a <script src> tag"


@pytest.mark.parametrize("page", HTML_PAGES)
def test_pages_have_no_inline_event_handlers(page):
    html = (STATIC_DIR / page).read_text(encoding="utf-8")
    found = INLINE_HANDLER.findall(html) + ATTR_HANDLER.findall(html)
    assert not found, f"{page}: inline handlers {found[:3]}"
    assert "javascript:" not in html.lower()


@pytest.mark.parametrize("path", OWN_SCRIPTS, ids=lambda p: p.name)
def test_scripts_build_no_inline_handlers(path):
    src = path.read_text(encoding="utf-8")
    found = ATTR_HANDLER.findall(src)
    assert not found, f"{path.name}: markup with inline handlers {found[:3]}"
    assert "javascript:" not in src.lower()
    assert not re.search(r"\bnew Function\s*\(|\beval\s*\(", src), f"{path.name}: eval"
    assert not re.search(r"set(?:Timeout|Interval)\(\s*['\"`]", src), f"{path.name}: string timer"


def test_every_data_action_is_registered():
    """A data-action with no registerAction() behind it is a dead button."""
    registered = set()
    used = set()
    for p in OWN_SCRIPTS:
        src = p.read_text(encoding="utf-8")
        registered |= set(re.findall(r"registerAction\(\s*'([a-z0-9-]+)'", src))
        used |= set(re.findall(r'data-(?:change-)?action="([a-z0-9-]+)"', src))
    for page in HTML_PAGES:
        used |= set(re.findall(r'data-(?:change-)?action="([a-z0-9-]+)"', (STATIC_DIR / page).read_text(encoding="utf-8")))
    assert used, "expected data-action markup"
    assert used <= registered, f"unregistered actions: {sorted(used - registered)}"


def test_login_script_is_a_versioned_file():
    html = render_versioned_html("login.html")
    assert re.search(r'<script src="/static/login\.js\?v=[0-9a-f]{8}"></script>', html)
    assert (STATIC_DIR / "login.js").is_file()
