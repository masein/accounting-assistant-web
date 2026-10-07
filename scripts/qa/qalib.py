"""Helpers for the deep browser test (SCENARIOS.md). Runs inside aa-playwright
against the throwaway aa-qa server. Passwords come from the environment and
are never printed."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, timedelta

BASE = os.environ.get("BASE", "http://localhost:8000")
PW = os.environ["QA_PASSWORD"]
OUT = "/qa/out"
os.makedirs(OUT, exist_ok=True)


# ── results ────────────────────────────────────────────────────────────────
def record(sid: str, status: str, notes: str = "", shots: list[str] | None = None) -> None:
    with open(f"{OUT}/results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": sid, "status": status, "notes": notes, "shots": shots or [],
                            "at": time.strftime("%H:%M:%S")}, ensure_ascii=False) + "\n")
    print(f"[{status}] {sid} {notes[:300]}")


def finding(sid: str, severity: str, page_name: str, text: str, shot: str = "") -> None:
    with open(f"{OUT}/findings.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"scenario": sid, "severity": severity, "page": page_name, "text": text, "shot": shot},
                           ensure_ascii=False) + "\n")
    print(f"  ! {severity} {page_name}: {text[:300]}")


class Check:
    """Collects failed expectations for one scenario."""

    def __init__(self, sid: str):
        self.sid, self.fails, self.notes, self.shots = sid, [], [], []

    def ok(self, cond, what: str) -> bool:
        if not cond:
            self.fails.append(what)
            print(f"  ✗ {what}")
        return bool(cond)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def done(self) -> None:
        status = "PASS" if not self.fails else "FAIL"
        record(self.sid, status, "; ".join(self.fails + self.notes), self.shots)


# ── browser ────────────────────────────────────────────────────────────────
class Watch:
    def __init__(self, page):
        self.js, self.console, self.http = [], [], []
        page.on("pageerror", lambda e: self.js.append(str(e)[:200]))
        page.on("console", lambda m: self.console.append(m.text[:200]) if m.type == "error" else None)
        page.on("response", lambda r: self.http.append(f"{r.status} {r.request.method} {r.url.replace(BASE, '')[:120]}")
                if r.status >= 400 and "/static/" not in r.url else None)
        page.on("dialog", lambda d: (self.js.append("NATIVE DIALOG: " + d.message[:120]), d.dismiss()))

    def problems(self, allow=(r"^403 ", r"^404 GET /favicon")) -> list[str]:
        http = [h for h in self.http if not any(re.search(a, h) for a in allow)]
        return self.js + http

    def reset(self):
        self.js.clear(); self.console.clear(); self.http.clear()


def new_session(browser, username: str, *, lang: str | None = None, mobile: bool = False, tablet: bool = False,
                timezone: str = "Asia/Tehran", locale: str | None = None, password: str | None = None):
    """Sign in through the real form. Returns (ctx, page, watch)."""
    vp = {"width": 375, "height": 812} if mobile else ({"width": 768, "height": 1024} if tablet else {"width": 1280, "height": 900})
    ctx = browser.new_context(viewport=vp, timezone_id=timezone, locale=locale or ("fa-IR" if lang == "fa" else "en-GB"),
                              is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
    # the page's requests in flight, to wait for what a section fetches when it opens
    # (networkidle returns at once once the page has been idle)
    ctx.add_init_script("""(() => { window.__inflight = 0; const f = window.fetch;
        window.fetch = (...a) => { window.__inflight++; return f(...a).finally(() => { window.__inflight--; }); }; })()""")
    page = ctx.new_page()
    watch = Watch(page)
    page.goto(f"{BASE}/login")
    page.fill("#username", username)
    page.fill("#password", password or PW)
    page.click("#submit-btn")
    page.wait_for_url(lambda u: "/login" not in u, timeout=20_000)
    page.wait_for_load_state("networkidle")
    if lang:
        set_language(page, lang)
    return ctx, page, watch


def set_language(page, lang: str) -> None:
    if page.evaluate("() => document.documentElement.lang") == lang:
        return
    with page.expect_response(lambda r: "/auth/preferences" in r.url and r.request.method == "PATCH"):
        page.evaluate("""(l) => { const s = document.getElementById('topbar-language'); s.value = l;
            s.dispatchEvent(new Event('change', { bubbles: true })); }""", lang)
    wait_until(page, "(l) => document.documentElement.lang === l", lang)
    page.wait_for_load_state("networkidle")


def settle(page, min_ms: int = 300, timeout_ms: int = 8_000) -> None:
    """Wait until the page's fetches are done (and what they render has had a moment)."""
    page.wait_for_timeout(min_ms)
    wait_until(page, "() => (window.__inflight || 0) === 0", timeout_ms=timeout_ms)
    page.wait_for_timeout(150)


def wait_until(page, expression: str, arg=None, timeout_ms: int = 8_000) -> bool:
    end = time.monotonic() + timeout_ms / 1000
    while True:
        if page.evaluate(f"(a) => !!(({expression})(a))", arg):
            return True
        if time.monotonic() > end:
            return False
        page.wait_for_timeout(120)


def go(page, name: str) -> None:
    btn = page.locator(f'.nav-btn[data-page="{name}"]').first
    if btn.count() and btn.is_visible():
        btn.click()
    else:
        page.evaluate("(p) => { location.hash = p; }", name)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(350)


def shot(page, sid: str, name: str, *, full: bool = True, selector: str | None = None) -> str:
    path = f"{OUT}/{sid}-{name}.png"
    try:
        if selector:
            page.locator(selector).first.screenshot(path=path)
        elif full and page.locator("canvas:visible").count():
            # A full-page capture resizes the window, which clears every chart's
            # canvas; the shot then caught them blank or half redrawn (CEO Mode
            # looked chartless in retest 3). Grow the window to the page, let the
            # charts redraw, take it as it stands, and put the window back.
            vp = page.viewport_size
            height = page.evaluate("() => document.documentElement.scrollHeight")
            page.set_viewport_size({"width": vp["width"], "height": max(vp["height"], height)})
            page.wait_for_timeout(1200)
            page.screenshot(path=path)
            page.set_viewport_size(vp)
            page.wait_for_timeout(300)
        else:
            page.screenshot(path=path, full_page=full)
    except Exception as e:   # a screenshot must not end the scenario
        print("  (shot failed)", str(e)[:120])
    return path.replace(OUT + "/", "")


API_JS = r"""async ([method, path, body]) => { const r = await fetch(path, { method,
  headers: body !== null && body !== undefined ? { 'Content-Type': 'application/json' } : {},
  body: body !== null && body !== undefined ? JSON.stringify(body) : undefined });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (_) { j = t; } return [r.status, j]; }"""


def api(page, method: str, path: str, body=None):
    return page.evaluate(API_JS, [method, path, body])


def alert_text(page, timeout_ms: int = 6000) -> str:
    if not wait_until(page, "() => { const a = document.querySelector('#alert'); return a && a.style.display !== 'none' && a.innerText.trim(); }",
                      timeout_ms=timeout_ms):
        return ""
    return page.inner_text("#alert").replace("×", "").strip()


def clear_alert(page) -> None:
    page.evaluate("() => { const a = document.getElementById('alert'); if (a) { a.style.display = 'none'; a.textContent = ''; } }")


def iso(days: int = 0) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ── automated UI/UX checks on the screen as it is ───────────────────────────
UX_JS = r"""(opts) => {
  const out = [];
  // checkVisibility: an input inside a closed <details> keeps a box in Chromium but isn't shown
  // (counting it made finding #10, its label's text being "" while hidden); aria-hidden isn't the user's
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && e.offsetParent !== null
      && (!e.checkVisibility || e.checkVisibility({ checkVisibilityCSS: true, contentVisibilityAuto: true }))
      && !e.closest('[aria-hidden="true"]'); };
  const card = document.querySelector(opts.root) || document.body;
  // I1: page overflow
  if (document.documentElement.scrollWidth > window.innerWidth + 1)
    out.push(['I1', 'page scrolls sideways: ' + document.documentElement.scrollWidth + ' > ' + window.innerWidth]);
  // I1: a phone zooms out when the layout is wider than its screen; innerWidth grows with it, so the
  // check above can't see it (Settings at 640 px on a 375 px phone, 2026-10-06)
  if (opts.mobile && window.innerWidth > screen.width + 1) {
    const wide = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
        return r.width > 0 && r.right > screen.width + 2 && !e.closest('.sidebar') && getComputedStyle(e).position !== 'fixed'; })
      .filter((e, _, all) => !all.some(o => o !== e && o.contains(e)))
      .slice(0, 4).map(e => '<' + e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.split(' ')[0] : '') + '> ' + Math.round(e.getBoundingClientRect().right));
    out.push(['I1', 'phone zooms out: a ' + window.innerWidth + ' px layout on a ' + screen.width + ' px screen ' + wide.join(', ')]);
  }
  // I1: elements wider than the viewport that are not inside a scroller
  const scroller = (e) => { for (let n = e.parentElement; n; n = n.parentElement) { const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
  [...card.querySelectorAll('*')].filter(vis).forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.right > window.innerWidth + 2 && !scroller(e) && getComputedStyle(e).position !== 'fixed' && !e.closest('#jdate-pop'))
      out.push(['I1', 'past the screen edge: <' + e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '') + '> right=' + Math.round(r.right)]);
  });
  // I1: text cut off in a button / label / th (clipped, no ellipsis)
  [...card.querySelectorAll('button, label, th, .btn, h1, h2, h3, h4')].filter(vis).forEach(e => {
    const s = getComputedStyle(e);
    if (e.scrollWidth > e.clientWidth + 2 && (s.overflow === 'hidden' || s.overflowX === 'hidden') && s.textOverflow !== 'ellipsis')
      out.push(['I1', 'text clipped: ' + e.tagName.toLowerCase() + ' «' + e.innerText.trim().slice(0, 40) + '»']);
  });
  // I3: raw keys, placeholders, undefined/NaN on screen
  const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
  let n; const seen = new Set();
  while ((n = walker.nextNode())) {
    const t = n.textContent.trim(); const p = n.parentElement;
    if (!t || !p || !vis(p) || p.closest('script, style, textarea, pre, code')) continue;
    if (/^[a-z][a-z0-9]+(_[a-z0-9]+)*[A-Z][A-Za-z0-9_]*$/.test(t) && !/^(iPhone|eBay)/.test(t)) seen.add('raw key «' + t + '»');
    if (/\{[a-z][A-Za-z_]*\}/.test(t)) seen.add('placeholder left: «' + t.slice(0, 60) + '»');
    if (/\b(undefined|NaN|null|\[object Object\])\b/.test(t)) seen.add('broken value: «' + t.slice(0, 60) + '»');
    if (opts.lang !== 'en' && /[A-Za-z]{3,}/.test(t) && !/[؀-ۿ]/.test(t)) {
      if (!p.closest('td, bdi, [dir="ltr"], select, option, .ccy-badge, input') && !opts.asWritten.some(w => t.includes(w))
          && (t.match(/[A-Za-z]{3,}/g) || []).length >= 1 && t.length < 160) seen.add('English in ' + opts.lang + ': «' + t.slice(0, 60) + '»');
    }
  }
  // I3: an English sentence among Persian words (a server message: «Enter the company's tax memory id
  // (شناسه یکتای حافظه مالیاتی) in the مودیان settings.» got past the check above for its Persian).
  // Persian runs only: their tenants' data is Persian, while in es/ar the UK tenant's English is data.
  if (opts.lang === 'fa') {
    const w2 = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    let m;
    while ((m = w2.nextNode())) {
      const t = m.textContent.trim(); const p = m.parentElement;
      if (!t || !p || !vis(p) || p.closest('script, style, textarea, pre, code, input, option, [dir="ltr"]')) continue;
      const words = t.match(/[A-Za-z][A-Za-z']*/g) || [];
      if (words.length >= 5 && /\b(the|to|of|is|are|no|not|and|for|on|with|be|can't|must|has|have|this|was|could|should|from)\b/i.test(t))
        seen.add('English sentence in fa: «' + t.slice(0, 90) + '»');
    }
  }
  seen.forEach(s => out.push(['I3', s]));
  // I4: inputs / buttons without a name
  [...card.querySelectorAll('input, select, textarea, button')].filter(vis).forEach(e => {
    if (e.type === 'hidden') return;
    const name = (e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].textContent) || e.getAttribute('title')
      || e.getAttribute('placeholder') || (e.tagName === 'BUTTON' ? e.innerText : '') || e.getAttribute('aria-labelledby') || '').trim();
    if (!name) out.push(['I4', 'no accessible name: <' + e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '') + ' type=' + (e.type || '') + '>']);
  });
  // I5: tap targets and tiny type (phones)
  if (opts.mobile) {
    [...card.querySelectorAll('button, a.btn, input[type=checkbox], select')].filter(vis).forEach(e => {
      const r = e.getBoundingClientRect();
      // a checkbox's tap target is its label row (20 px box in a 32 px row, #270)
      const lab = e.type === 'checkbox' ? (e.closest('label') || (e.labels && e.labels[0])) : null;
      if (lab && lab.getBoundingClientRect().height >= 28) return;
      if ((r.height < 28 || r.width < 28) && !e.closest('.jdate-grid')) out.push(['I5', 'small tap target ' + Math.round(r.width) + '×' + Math.round(r.height) + ': «' + (e.innerText || e.getAttribute('aria-label') || e.id || e.className).toString().trim().slice(0, 30) + '»']);
    });
  }
  [...card.querySelectorAll('*')].filter(vis).forEach(e => {
    if (e.id === 'notify-badge') return;            // the bell's count: small by design
    if ([...e.childNodes].some(c => c.nodeType === 3 && c.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 11)
      out.push(['I5', 'tiny text ' + getComputedStyle(e).fontSize + ': «' + e.innerText.trim().slice(0, 30) + '»']);
  });
  // dedupe
  const u = new Map(); out.forEach(([k, v]) => u.set(k + v, [k, v])); return [...u.values()].slice(0, 60);
}"""
AS_WRITTEN = ["CSV", "PDF", "Excel", "JSON", "XLSX", "IBAN", "API", "SMS", "VAT", "MTD", "HMRC", "TTMS", "IMAP", "INBOX",
              "GBP", "IRR", "USD", "EUR", "IRT", "http", "@", "Telegram", "Bale", "Google", "Apple", "OCR", "AI", "SKU", "PAYE",
              "FRS", "Xero", "QuickBooks", "Sepidar", "Hesabfa", "Holoo", "English", "Español", "PAP-", "INV-", "Default", "Navasan",
              "GOLDG", "PNG", "JPG", "WEBP", "SMTP", "TOTP", "URL", "PWA", "ID", "CEO", "CFO", "KPI"]


def ux(page, sid: str, page_name: str, *, lang: str, mobile: bool = False, root: str = ".card[data-page]:not([style*='display: none'])",
       shot_name: str = "", scope: str | None = None) -> list:
    """The checks on the whole screen, or, with ``scope``, on one open popup
    (the page's own findings aren't counted again)."""
    hits = page.evaluate(UX_JS, {"root": scope or "body", "lang": lang, "mobile": mobile, "asWritten": AS_WRITTEN})
    for check, text in hits:
        finding(sid, check, page_name, text, shot_name)
    return hits


def confirm_shown(page, timeout_ms: int = 3000) -> bool:
    """The app's confirm dialog, given a moment to open (the voucher's fetches account names first)."""
    try:
        page.locator("#ui-confirm-modal").wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False
