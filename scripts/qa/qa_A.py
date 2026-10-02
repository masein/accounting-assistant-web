"""Group A — platform and onboarding (SCENARIOS.md A1–A6)."""
import struct
import sys
import zlib

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import (BASE, OUT, PW, Check, alert_text, api, clear_alert, go, new_session, shot, ux, wait_until)

PAGE_ROLES = {
    "dashboard": ["owner", "cfo", "accountant", "viewer"], "personal-dashboard": ["personal"],
    "commitments": ["owner", "cfo", "accountant", "personal"], "ai-accountant": ["owner", "cfo", "accountant", "personal"],
    "transactions": ["owner", "cfo", "accountant", "personal"], "invoices": ["owner", "cfo", "accountant"],
    "time": ["owner", "cfo", "accountant", "employee"], "expenses": ["owner", "cfo", "accountant", "manager", "employee"],
    "purchase-orders": ["owner", "cfo", "accountant"], "recurring": ["owner", "cfo", "accountant", "personal"],
    "entities": ["owner", "cfo", "accountant"], "products": ["owner", "cfo", "accountant"], "inventory": ["owner", "cfo", "accountant"],
    "payroll": ["owner", "cfo", "accountant"], "equity": ["owner", "cfo", "accountant"],
    "bank-statements": ["owner", "cfo", "accountant", "personal"], "ledger": ["owner", "cfo", "accountant", "viewer"],
    "manager": ["owner", "cfo", "accountant", "viewer"], "cfo": ["owner", "cfo"], "ceo": ["owner", "cfo"],
    "audit": ["owner", "cfo", "accountant"], "settings": ["owner"], "migration": ["owner", "accountant"],
    "petty-cash": ["owner", "cfo", "accountant", "manager", "employee"], "fixed-assets": ["owner", "cfo", "accountant"],
    "accounts": ["owner", "cfo", "accountant"],
}
ROLE_HOME = {"owner": "dashboard", "cfo": "dashboard", "accountant": "dashboard", "manager": "expenses",
             "employee": "time", "viewer": "dashboard"}


def png(path, rgb=(15, 118, 110), size=(120, 60)):
    w, h = size
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def visible_pages(page):
    return sorted(page.evaluate("() => [...document.querySelectorAll('.nav-btn[data-page]')].filter(b => b.offsetParent !== null).map(b => b.dataset.page)"))


def a1_super_creates_tenants(browser):
    c = Check("A1")
    ctx, page, watch = new_session(browser, "qa_super", lang="fa")
    try:
        c.ok(page.locator("#nav-companies").is_visible(), "super-admin sees Companies")
        go(page, "companies")
        c.shots.append(shot(page, "A1", "companies-empty"))
        # validation: missing fields
        page.fill("#co-name", "")
        page.click("#co-create")
        msg = alert_text(page)
        c.ok(msg and not any(ch.isascii() and ch.isalpha() for ch in msg), f"missing-fields message is Persian: «{msg}»")
        clear_alert(page)
        made = {}
        for name, locale, kind, user in (("بازرگانی آرمان", "ir", "business", "arman_owner"),
                                         ("Thames Studio Ltd", "uk", "business", "thames_owner"),
                                         ("خانواده سارا", "ir", "personal", "sara")):
            page.fill("#co-name", name)
            page.select_option("#co-locale", locale)
            page.select_option("#co-kind", kind)
            cur = page.input_value("#co-currency")
            c.ok(cur == ("GBP" if locale == "uk" else "IRR"), f"{name}: currency follows locale ({cur})")
            page.fill("#co-username", user)
            page.fill("#co-password", PW)
            with page.expect_response(lambda r: r.url.endswith("/admin/companies") and r.request.method == "POST") as res:
                page.click("#co-create")
            c.ok(res.value.status in (200, 201), f"{name}: created ({res.value.status} {res.value.text()[:120]})")
            msg = alert_text(page)
            c.ok(msg and "Created" not in msg, f"{name}: success message Persian «{msg}»")
            made[user] = res.value.json() if res.value.status in (200, 201) else {}
            clear_alert(page)
        # duplicate username
        page.fill("#co-name", "تکراری")
        page.fill("#co-username", "arman_owner")
        page.fill("#co-password", PW)
        with page.expect_response(lambda r: r.url.endswith("/admin/companies")) as res:
            page.click("#co-create")
        msg = alert_text(page)
        c.ok(res.value.status >= 400, f"duplicate owner username refused ({res.value.status})")
        c.ok(msg and not any(ch.isascii() and ch.isalpha() for ch in msg.replace("arman_owner", "")), f"duplicate message Persian «{msg}»")
        clear_alert(page)
        page.wait_for_timeout(500)
        rows = page.inner_text("#companies-tbody")
        for name in ("بازرگانی آرمان", "Thames Studio Ltd", "خانواده سارا"):
            c.ok(name in rows, f"{name} listed")
        c.shots.append(shot(page, "A1", "companies-listed"))
        ux(page, "A1", "companies", lang="fa", shot_name="A1-companies-listed.png")
        c.ok(watch.problems() == [] or all(p.startswith(("409", "400", "422")) for p in watch.problems()), f"problems: {watch.problems()}")
    finally:
        ctx.close()
    c.done()


def a2_first_sign_in(browser):
    c = Check("A2")
    ctx, page, watch = new_session(browser, "arman_owner", locale="fa-IR")   # no language set by us
    try:
        lang = page.evaluate("() => document.documentElement.lang")
        c.ok(lang == "fa", f"a Persian browser gets Persian (lang={lang})")
        c.ok(page.evaluate("() => document.documentElement.dir") == "rtl", "right to left")
        wait_until(page, "() => window.__DISPLAY_CALENDAR !== undefined")
        page.wait_for_timeout(800)
        c.ok(page.evaluate("() => window.__DISPLAY_CALENDAR") == "jalali", f"an Iranian company is on Jalali ({page.evaluate('() => window.__DISPLAY_CALENDAR')})")
        c.ok(page.evaluate("() => activePage()") == "dashboard", "lands on the dashboard")
        tour = page.locator("#whats-new-modal, .whats-new, #wn-modal")
        if tour.count() and tour.first.is_visible():
            c.note("what's new shown on first sign-in")
            c.shots.append(shot(page, "A2", "whats-new", full=False))
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            c.ok(not tour.first.is_visible(), "what's new closes on Escape")
        c.shots.append(shot(page, "A2", "dashboard-first", full=True))
        ux(page, "A2", "dashboard (empty)", lang="fa", shot_name="A2-dashboard-first.png")
        c.ok(watch.problems() == [], f"problems: {watch.problems()}")
    finally:
        ctx.close()
    c.done()


def a3_company_profile(browser):
    c = Check("A3")
    png(f"{OUT}/logo.png"); png(f"{OUT}/signature.png", rgb=(30, 30, 30), size=(160, 50))
    open(f"{OUT}/not-an-image.png", "w").write("this is text, not a png")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        go(page, "settings")
        page.wait_for_timeout(600)
        fields = {"cp-legal_name": "شرکت بازرگانی آرمان (سهامی خاص)", "cp-national_id": "۱۰۱۰۳۴۵۶۷۸۹", "cp-economic_code": "۴۱۱۱۲۲۳۳۴۴۵۵",
                  "cp-registration_number": "۵۴۳۲۱", "cp-province": "تهران", "cp-city": "تهران", "cp-postal_code": "۱۴۳۵۶۷۸۹۰۱",
                  "cp-phone": "۰۲۱-۸۸۷۷۶۶۵۵", "cp-email": "info@arman.example", "cp-address": "تهران، خیابان ولیعصر، پلاک ۱۲",
                  "cp-iban": "IR120170000000123456789012", "cp-invoice_number_prefix": "ARM-"}
        for fid, val in fields.items():
            loc = page.locator(f"#{fid}")
            if loc.count():
                loc.fill(val)
            else:
                c.ok(False, f"field #{fid} missing")
        page.set_input_files("#cp-logo-file", f"{OUT}/logo.png")
        page.set_input_files("#cp-signature-file", f"{OUT}/signature.png")
        with page.expect_response(lambda r: "company-profile" in r.url and r.request.method in ("PUT", "PATCH", "POST")) as res:
            page.click("#cp-save")
        page.wait_for_timeout(1200)
        c.ok(res.value.status in (200, 201), f"profile saved ({res.value.status})")
        st = page.inner_text("#cp-status") if page.locator("#cp-status").count() else ""
        c.note(f"status «{st}»")
        status, prof = api(page, "GET", "/admin/company-profile", None)
        if status == 200 and isinstance(prof, dict):
            c.ok(prof.get("national_id") == "10103456789", f"national id stored in 0–9 ({prof.get('national_id')})")
            c.ok(prof.get("postal_code") == "1435678901", f"postal code in 0–9 ({prof.get('postal_code')})")
            c.ok(bool(prof.get("logo_url") or prof.get("has_logo")), f"logo stored ({ {k: prof.get(k) for k in ('logo_url', 'has_logo')} })")
        else:
            c.ok(False, f"GET /admin/company-profile → {status}")
        c.shots.append(shot(page, "A3", "profile-saved"))
        # a text file named .png must be refused
        page.set_input_files("#cp-logo-file", f"{OUT}/not-an-image.png")
        with page.expect_response(lambda r: "company-profile" in r.url and r.request.method in ("PUT", "PATCH", "POST")) as res:
            page.click("#cp-save")
        page.wait_for_timeout(800)
        msg = alert_text(page, 3000) or (page.inner_text("#cp-status") if page.locator("#cp-status").count() else "")
        c.note(f"bad logo → {res.value.status} «{msg}»")
        c.ok(res.value.status >= 400 or "image" not in msg.lower(), "a fake image is refused")
        c.ok(not msg or any("؀" <= ch <= "ۿ" for ch in msg), f"the refusal is Persian «{msg}»")
        c.shots.append(shot(page, "A3", "profile-bad-logo", full=False))
        ux(page, "A3", "settings", lang="fa", shot_name="A3-profile-saved.png")
    finally:
        ctx.close()
    c.done()


def a4_team(browser):
    c = Check("A4")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    team = {"arman_acc": "accountant", "arman_cfo": "cfo", "arman_mgr": "manager", "arman_emp": "employee", "arman_view": "viewer"}
    try:
        go(page, "settings")
        page.wait_for_timeout(500)
        # weak password
        page.fill("#new-user-username", "arman_weak")
        page.fill("#new-user-password", "123")
        page.click("#create-user-btn")
        msg = alert_text(page)
        c.ok(msg and any("؀" <= ch <= "ۿ" for ch in msg), f"weak password refused in Persian «{msg}»")
        clear_alert(page)
        for user, role in team.items():
            page.fill("#new-user-username", user)
            page.fill("#new-user-password", PW)
            page.select_option("#new-user-role", role)
            with page.expect_response(lambda r: r.url.endswith("/admin/users") and r.request.method == "POST") as res:
                page.click("#create-user-btn")
            c.ok(res.value.status in (200, 201), f"{user} ({role}) created ({res.value.status} {res.value.text()[:100]})")
            clear_alert(page)
        page.wait_for_timeout(600)
        c.shots.append(shot(page, "A4", "users"))
    finally:
        ctx.close()
    for user, role in {"arman_owner": "owner", **team}.items():
        ctx, page, watch = new_session(browser, user, lang="fa")
        try:
            page.wait_for_timeout(500)
            home = page.evaluate("() => activePage()")
            c.ok(home == ROLE_HOME[role], f"{user} lands on {ROLE_HOME[role]} (got {home})")
            want = sorted(p for p, roles in PAGE_ROLES.items() if role in roles)
            got = visible_pages(page)
            c.ok(got == want, f"{user} nav: extra {sorted(set(got) - set(want))} missing {sorted(set(want) - set(got))}")
            c.shots.append(shot(page, "A4", f"home-{role}", full=False))
            ux(page, "A4", f"home ({role})", lang="fa", shot_name=f"A4-home-{role}.png")
            probs = watch.problems()
            c.ok(probs == [], f"{user} problems on sign-in: {probs}")
        finally:
            ctx.close()
    c.done()


def a5_isolation(browser):
    c = Check("A5")
    ctx, page, _ = new_session(browser, "arman_owner", lang="fa")
    status, ent = api(page, "POST", "/entities", {"type": "client", "name": "مشتری محرمانه آرمان"})
    ctx.close()
    c.ok(status in (200, 201), f"Arman client made ({status})")
    ctx, page, _ = new_session(browser, "thames_owner", lang="en")
    try:
        s1, body = api(page, "GET", f"/entities/{ent.get('id')}", None)
        c.ok(s1 in (403, 404), f"Thames reads Arman's client by id → {s1}")
        s2, lst = api(page, "GET", "/entities", None)
        c.ok(s2 == 200 and not any("آرمان" in (e.get("name") or "") for e in (lst or [])), "not in Thames' list")
        s3, _ = api(page, "PATCH", f"/entities/{ent.get('id')}", {"name": "hijack"})
        c.ok(s3 in (403, 404, 405), f"Thames edits it → {s3}")
        s4, _ = api(page, "DELETE", f"/entities/{ent.get('id')}", None)
        c.ok(s4 in (403, 404, 405), f"Thames deletes it → {s4}")
    finally:
        ctx.close()
    c.done()


def a6_sign_in_errors(browser):
    c = Check("A6")
    ctx = browser.new_context(locale="fa-IR", timezone_id="Asia/Tehran")
    page = ctx.new_page()
    try:
        page.goto(f"{BASE}/login")
        page.wait_for_load_state("networkidle")
        title = page.inner_text("#login-title")
        c.ok(any("؀" <= ch <= "ۿ" for ch in title), f"login page Persian on a Persian browser «{title}»")
        page.fill("#username", "arman_owner"); page.fill("#password", "wrong-password-1")
        page.click("#submit-btn")
        wait_until(page, "() => document.getElementById('error-box') && document.getElementById('error-box').innerText.trim()")
        e1 = page.inner_text("#error-box").strip()
        page.fill("#username", "nobody_here"); page.fill("#password", "wrong-password-1")
        page.click("#submit-btn")
        page.wait_for_timeout(1200)
        e2 = page.inner_text("#error-box").strip()
        c.ok(e1 and any("؀" <= ch <= "ۿ" for ch in e1), f"wrong password in Persian «{e1}»")
        c.ok(e1 == e2, f"same message for an unknown user (no enumeration): «{e1}» vs «{e2}»")
        c.shots.append(shot(page, "A6", "login-error", full=False))
        ux(page, "A6", "login", lang="fa", shot_name="A6-login-error.png")
        page.fill("#username", "arman_owner"); page.fill("#password", PW)
        page.click("#submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_load_state("networkidle")
        page.click("#user-menu-btn")
        page.click("#topbar-logout")
        page.wait_for_url(lambda u: "/login" in u, timeout=10000)
        page.goto(f"{BASE}/#invoices")
        page.wait_for_timeout(1500)
        c.ok("/login" in page.url, f"after sign-out the app needs a sign-in again ({page.url})")
        s, _ = api(page, "GET", "/entities", None)
        c.ok(s == 401, f"API after sign-out → {s}")
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (a1_super_creates_tenants, a2_first_sign_in, a3_company_profile, a4_team, a5_isolation, a6_sign_in_errors):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:400])
        b.close()
