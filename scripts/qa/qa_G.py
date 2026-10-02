"""Group G — the UK company (Thames Studio Ltd), in English; and H — personal mode (Sara)."""
import json
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import BASE, OUT, Check, alert_text, api, clear_alert, go, new_session, shot, ux, wait_until

FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))


def g0_reset_keeps_locale(browser):
    c = Check("G0")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        s, before = api(page, "GET", "/accounts", None)
        uk_before = any((a.get("code") or "").startswith("1200") for a in (before or [])) or not any(FA(a.get("name")) for a in (before or []))
        c.note(f"chart before: {len(before or [])} accounts, Persian names: {sum(FA(a.get('name')) for a in (before or []))}")
        c.ok(page.locator("#reset-db-btn").count() == 0, "no reset button on the parties page (#259)")
        go(page, "settings")
        page.evaluate("() => document.querySelectorAll('.card[data-page=\"settings\"] details').forEach(d => { d.open = true; })")
        page.click("#reset-empty-btn")
        c.ok(page.locator("#ui-prompt-modal").is_visible(), "the wipe asks for the company's name")
        c.shots.append(shot(page, "G0", "reset-confirm", full=False))
        name = page.evaluate("() => (document.getElementById('company-badge') || {}).textContent || ''").strip()
        page.fill("#ui-prompt-input", name)
        with page.expect_response(lambda r: "/admin/reset-db" in r.url) as res:
            page.click("#ui-prompt-ok")
        c.note(f"reset → {res.value.status}")
        page.wait_for_timeout(1500)
        s, after = api(page, "GET", "/accounts", None)
        persian = sum(FA(a.get("name")) for a in (after or []))
        c.ok(persian == 0, f"after a reset from the parties page the UK company keeps a UK chart ({persian} Persian-named accounts)")
        if persian:   # put the UK chart back for the rest of the group
            s2, _ = api(page, "POST", "/admin/reset-db?locale=uk", None)
            c.note(f"restored the UK chart ({s2})")
    finally:
        ctx.close()
    c.done()


def g1_uk_books(browser):
    c = Check("G1")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        s, ent = api(page, "POST", "/entities", {"type": "client", "name": "Brightside Ltd", "payment_terms": "Net 14"})
        c.ok(s in (200, 201), f"client ({s})")
        go(page, "invoices")
        page.select_option("#inv-kind", "sales")
        page.fill("#inv-number", "TS-0001")
        page.fill("#inv-issue", "2026-10-01")
        with page.expect_response(lambda r: f"/entities/{ent['id']}" in r.url):
            page.select_option("#inv-entity", ent["id"])
        page.click("#inv-mode-itemized")
        row = page.locator("#inv-items-body .inv-line").first
        row.locator(".il-desc").fill("Brand workshop")
        row.locator(".il-qty").fill("2"); row.locator(".il-price").fill("1500")
        opts = row.locator(".il-code option").all_text_contents()
        pick = next((o for o in opts if "UK_VAT_STANDARD" in o), None)
        c.ok(pick is not None, f"UK standard VAT offered ({opts[:5]})")
        if pick:
            row.locator(".il-code").select_option(label=pick)
        page.wait_for_timeout(300)
        grand = page.inner_text("#inv-grand")
        c.ok("3,600" in grand and "£" in grand, f"total £3,600 incl. 20% VAT «{grand}»")
        with page.expect_response(lambda r: r.url.endswith("/invoices") and r.request.method == "POST") as res:
            page.click("#inv-add")
        c.ok(res.value.status in (200, 201), f"invoice created ({res.value.status} {res.value.text()[:150]})")
        inv = res.value.json() if res.value.status in (200, 201) else {}
        c.ok(inv.get("due_date") == "2026-10-15", f"due Net 14 → 15 Oct ({inv.get('due_date')})")
        page.wait_for_timeout(600)
        page.evaluate("() => document.querySelectorAll('.card[data-page=\"invoices\"] details').forEach(d => { d.open = true; })")
        c.ok(not page.locator("#mo-export").is_visible(), "no Moadian (Iran) section for a UK company")
        c.ok(not page.locator("#ttms-panel").is_visible(), "no TTMS (Iran) section for a UK company")
        c.shots.append(shot(page, "G1", "invoices"))
        ux(page, "G1", "invoices (UK)", lang="en", shot_name="G1-invoices.png")
        r = page.request.get(f"{BASE}/invoices/{inv.get('id')}/pdf")
        c.ok(r.status == 200, f"English PDF ({r.status})")
        if r.status == 200:
            open(f"{OUT}/G1-invoice.pdf", "wb").write(r.body())
        s, vr = api(page, "GET", "/tax/uk/vat/return?start=2026-10-01&end=2026-12-31", None)
        if s != 200:
            s, periods = api(page, "GET", "/tax/uk/vat/periods", None)
            c.note(f"vat periods → {s} {str(periods)[:200]}")
        else:
            boxes = vr.get("boxes") or vr
            c.note(f"VAT return: {str(boxes)[:200]}")
        s, bs = api(page, "GET", "/manager-reports/financial/uk/balance-sheet?to_date=2026-12-31", None)
        c.ok(s == 200, f"FRS 102 balance sheet → {s}")
    finally:
        ctx.close()
    c.done()


def h1_personal(browser):
    c = Check("H1")
    ctx, page, watch = new_session(browser, "sara", lang="fa")
    try:
        home = page.evaluate("() => activePage()")
        c.note(f"personal home: {home}")
        pages = sorted(page.evaluate("() => [...document.querySelectorAll('.nav-btn[data-page]')].filter(b => b.offsetParent !== null).map(b => b.dataset.page)"))
        want = sorted(["personal-dashboard", "commitments", "ai-accountant", "transactions", "recurring", "bank-statements"])
        c.ok(pages == want, f"personal nav: extra {sorted(set(pages) - set(want))} missing {sorted(set(want) - set(pages))}")
        go(page, "personal-dashboard")
        page.wait_for_timeout(1200)
        c.shots.append(shot(page, "H1", "dashboard"))
        ux(page, "H1", "personal dashboard", lang="fa", shot_name="H1-dashboard.png")
        txt = page.inner_text('.card[data-page="personal-dashboard"]')
        c.ok("NaN" not in txt and "undefined" not in txt, "no broken numbers")
        go(page, "commitments")
        page.fill("#cm-p-title", "قسط خودرو")
        page.locator("#cm-p-total").press_sequentially("۲۴۰۰۰۰۰۰۰")
        page.fill("#cm-p-count", "24")
        page.fill("#cm-p-first", "2026-10-25")
        with page.expect_response(lambda r: "/commitments" in r.url and r.request.method == "POST") as res:
            page.click("#cm-p-save")
        c.ok(res.value.status in (200, 201), f"car loan plan ({res.value.status} {res.value.text()[:150]})")
        c.shots.append(shot(page, "H1", "loan"))
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (g0_reset_keeps_locale, g1_uk_books, h1_personal):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:400])
        b.close()
