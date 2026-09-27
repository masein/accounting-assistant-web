"""Installable app: the manifest is linked, and where the browser allows a
service worker (a secure context — CI's 127.0.0.1) it registers for the
whole app and caches static files only, never an API response."""
from __future__ import annotations

import pytest

_CHECK = """async () => {
  if (!window.isSecureContext || !navigator.serviceWorker) return {skip: true};
  const reg = await Promise.race([navigator.serviceWorker.ready,
                                  new Promise((ok) => setTimeout(() => ok(null), 10000))]);
  if (!reg) return {scope: null};
  const urls = [];
  for (const name of await caches.keys()) {
    const cache = await caches.open(name);
    for (const req of await cache.keys()) urls.push(new URL(req.url).pathname);
  }
  return {scope: new URL(reg.scope).pathname, urls, caches: await caches.keys()};
}"""


def test_manifest_and_service_worker(app_page):
    page, watch = app_page
    assert page.locator('link[rel="manifest"]').get_attribute("href") == "/manifest.webmanifest"
    page.locator('.nav-btn[data-page="invoices"]').first.click()
    page.wait_for_load_state("networkidle")
    got = page.evaluate(_CHECK)
    if got.get("skip"):
        pytest.skip("service workers need a secure context (https or localhost)")
    assert got["scope"] == "/"
    assert all(u.startswith(("/static/", "/offline")) for u in got["urls"]), got["urls"]
    assert all(c.startswith(("static-", "shared-files")) for c in got["caches"]), got["caches"]
    assert watch.problems() == [], watch.problems()
    # The offline page is precached for a failed page load. (Playwright's
    # offline mode doesn't reach the worker's own fetches, so a real offline
    # load can't be simulated here; the fallback itself is unit-tested.)
    assert "/offline" in got["urls"], got["urls"]
    cached = page.evaluate("""async () => {
      const res = await caches.match('/offline');
      return res ? await res.text() : null;
    }""")
    assert cached and "You're offline" in cached
