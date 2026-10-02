"""Group C (part 2) — Arman, in Persian (SCENARIOS.md C12–C22)."""
import json
import random
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import OUT, Check, alert_text, api, clear_alert, go, new_session, shot, ux, wait_until

FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))
st = json.load(open(f"{OUT}/state.json"))
P = st["parties"]
RUN = str(random.randint(100, 999))


def opts(page, sel):
    return page.locator(f"{sel} option").evaluate_all("os => os.map(o => [o.value, o.textContent.trim()])")


def pick(page, sel, text):
    for v, label in opts(page, sel):
        if text in label:
            page.select_option(sel, v)
            return v
    return None


def c12_statement(browser):
    c = Check("C12")
    csv = ("تاریخ,شرح,برداشت,واریز,مانده\n"
           "1405/07/01,واریز مشتری مهرگان,,14170000,1214170000\n"
           "1405/07/03,کارمزد خدمات بانکی,25000,,1214145000\n"
           "1405/07/07,اجاره دفتر مهر,45000000,,1169145000\n"
           "1405/07/09,خرید لوازم التحریر,3200000,,1165945000\n"
           f"1405/07/10,واریز پارس افزار {RUN},9500000,,1156445000\n")
    open(f"{OUT}/mellat-{RUN}.csv", "w", encoding="utf-8").write(csv)
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "bank-statements")
        page.set_input_files("#bs-file-input", f"{OUT}/mellat-{RUN}.csv")
        page.fill("#bs-bank-name", "ملت")
        with page.expect_response(lambda r: "/bank-statements/upload" in r.url) as res:
            page.click("#bs-upload-btn")
        c.ok(res.value.status in (200, 201), f"uploaded ({res.value.status} {res.value.text()[:160]})")
        body = res.value.json() if res.value.status in (200, 201) else {}
        c.note(f"status {body.get('status')} rows {body.get('total_rows')}")
        page.wait_for_timeout(1200)
        c.shots.append(shot(page, "C12", "after-upload"))
        if body.get("status") == "needs_mapping":
            c.ok(False, "Persian headers (تاریخ / شرح / برداشت / واریز / مانده) were not recognised — asked to map")
        else:
            c.ok(body.get("total_rows") == 5, f"5 rows read ({body.get('total_rows')})")
            rows = page.inner_text("#bs-rows-body") if page.locator("#bs-rows-body").is_visible() else ""
            c.ok("1405/07/07" in rows, "row dates shown in Jalali")
            c.ok("unmatched" not in rows and "matched" not in rows, "recon status in Persian")
            ux(page, "C12", "statement rows", lang="fa", shot_name="C12-after-upload.png")
        # the same file again
        page.click("#bs-back-btn") if page.locator("#bs-back-btn").is_visible() else None
        page.set_input_files("#bs-file-input", f"{OUT}/mellat-{RUN}.csv")
        with page.expect_response(lambda r: "/bank-statements/upload" in r.url) as res2:
            page.click("#bs-upload-btn")
        page.wait_for_timeout(800)
        b2 = res2.value.json() if res2.value.status in (200, 201) else {}
        msg = alert_text(page, 2500) or (page.inner_text("#bs-upload-status") if page.locator("#bs-upload-status").count() else "")
        if page.locator("#ui-confirm-modal").is_visible():
            c.note("duplicate asks to confirm: «" + page.inner_text("#ui-confirm-message")[:120] + "»")
            c.ok(FA(page.inner_text("#ui-confirm-message")), "duplicate warning in Persian")
            page.click("#ui-confirm-cancel")
        else:
            c.ok(b2.get("status") == "duplicate" or "تکرار" in msg, f"re-upload flagged as a duplicate ({b2.get('status')} «{msg[:100]}»)")
    finally:
        ctx.close()
    c.done()


def c13_sms(browser):
    c = Check("C13")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "bank-statements")
        page.evaluate("() => document.querySelectorAll('details').forEach(d => { if (d.querySelector('#sms-text')) d.open = true; })")
        sms = ("بانک ملت\nبرداشت:3,200,000\nحساب:123456789\nمانده:1,165,945,000\n0709-11:20\n\n"
               "بانک ملت\nواریز:9,500,000+\nحساب:123456789\nمانده:1,175,445,000\n0710-09:05")
        page.fill("#sms-text", sms)
        with page.expect_response(lambda r: "sms" in r.url and r.request.method == "POST") as res:
            page.click("#sms-add")
        c.ok(res.value.status in (200, 201), f"SMS accepted ({res.value.status} {res.value.text()[:200]})")
        page.wait_for_timeout(900)
        out = page.inner_text("#sms-result") if page.locator("#sms-result").count() else ""
        c.ok(FA(out), f"result in Persian «{out[:150]}»")
        c.shots.append(shot(page, "C13", "sms", full=False, selector="#sms-panel"))
    finally:
        ctx.close()
    c.done()


def c15_claims(browser):
    c = Check("C15")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        go(page, "expenses")
        if page.locator("#exp-rate").is_visible():
            page.fill("#exp-rate", "")
            page.locator("#exp-rate").press_sequentially("۸۰۰۰")
            page.fill("#exp-threshold", "5000000")
            page.click("#exp-save-settings")
            m = alert_text(page, 3000)
            c.ok(FA(m), f"settings saved message Persian «{m}»")
            clear_alert(page)
        s, users = api(page, "GET", "/admin/users", None)
        emp = next((u for u in (users or []) if u.get("username") == "arman_emp"), {})
        s2, _ = api(page, "PATCH", f"/admin/users/{emp.get('id')}", {"entity_id": P["علی رضایی"]})
        c.note(f"link arman_emp → علی رضایی ({s2})")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_emp", lang="fa")
    try:
        c.ok(page.evaluate("() => activePage()") == "time", "employee lands on Time")
        go(page, "expenses")
        page.fill("#exp-date", "2026-10-01")
        page.locator("#exp-distance").press_sequentially("۱۲۰")
        page.fill("#exp-purpose", "بازدید از انبار کرج")
        page.wait_for_timeout(300)
        calc = page.inner_text("#exp-calc") if page.locator("#exp-calc").count() else ""
        c.ok("960,000" in calc or "۹۶۰" in calc, f"120 km × 8,000 = 960,000 shown «{calc}»")
        with page.expect_response(lambda r: "/expenses" in r.url and r.request.method == "POST") as res:
            page.click("#exp-submit")
        c.ok(res.value.status in (200, 201), f"claim filed ({res.value.status} {res.value.text()[:160]})")
        page.wait_for_timeout(700)
        rows = page.inner_text("#exp-claims-body")
        c.ok(FA(rows) and "submitted" not in rows and "pending" not in rows, f"claim row in Persian «{rows[:120]}»")
        c.shots.append(shot(page, "C15", "employee-claim"))
        ux(page, "C15", "expenses (employee)", lang="fa", shot_name="C15-employee-claim.png")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_mgr", lang="fa")
    try:
        c.ok(page.evaluate("() => activePage()") == "expenses", "manager lands on Expenses")
        page.wait_for_timeout(600)
        q = page.inner_text("#exp-queue-body")
        # under the company's approval threshold a claim needs no manager (by design, run 1 ✳)
        queued = "بازدید" in q
        c.note("the claim is in the manager's queue" if queued else "below the approval threshold: no manager step (by design)")
        btn = page.locator("#exp-queue-body button").filter(has_text="تأیید").first
        if queued and btn.count():
            with page.expect_response(lambda r: "/approve" in r.url) as res:
                btn.click()
                if page.locator("#ui-confirm-modal").is_visible():
                    page.click("#ui-confirm-ok")
            c.ok(res.value.status in (200, 201), f"approved ({res.value.status} {res.value.text()[:150]})")
        elif queued:
            c.ok(False, "no approve button in the queue")
        c.shots.append(shot(page, "C15", "manager-queue"))
    finally:
        ctx.close()
    c.done()


def c16_time(browser):
    c = Check("C16")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "time")
        page.click("#tm-new-project-btn")
        if page.locator("#ui-prompt-modal").is_visible():
            page.fill("#ui-prompt-input", f"طراحی کاتالوگ {RUN}")
            page.click("#ui-prompt-ok")
        page.wait_for_timeout(700)
        pick(page, "#tm-worker", "مریم")
        pick(page, "#tm-client", "پارس")
        page.wait_for_timeout(400)
        pick(page, "#tm-project", "کاتالوگ")
        page.click("#tm-set-rate-btn")
        if page.locator("#ui-prompt-modal").is_visible():
            page.locator("#ui-prompt-input").press_sequentially("۱۵۰۰۰۰۰")
            page.click("#ui-prompt-ok")
            page.wait_for_timeout(600)
            clear_alert(page)
        for d, h in (("2026-10-01", "۴"), ("2026-10-02", "6"), ("2026-10-03", "2.5")):
            page.fill("#tm-date", d)
            page.fill("#tm-hours", ""); page.locator("#tm-hours").press_sequentially(h)
            page.fill("#tm-desc", "طراحی صفحات")
            with page.expect_response(lambda r: "/time" in r.url and r.request.method == "POST") as res:
                page.click("#tm-add")
            c.ok(res.value.status in (200, 201), f"{h} h on {d} ({res.value.status} {res.value.text()[:120]})")
            clear_alert(page)
        page.wait_for_timeout(700)
        ready = page.inner_text("#tm-ready-body")
        c.ok("12.5" in ready or "۱۲٫۵" in ready or "12٫5" in ready, f"12.5 h ready to invoice «{ready[:150]}»")
        btn = page.locator("#tm-ready-body button").first
        if btn.count():
            btn.click(); page.wait_for_timeout(800)
            pv = page.inner_text("#tm-preview") if page.locator("#tm-preview").is_visible() else ""
            c.ok("18,750,000" in pv, f"preview 12.5 × 1,500,000 = 18,750,000 «{pv[:160]}»")
            c.ok("1405/" in pv, "preview range in Jalali")
            c.shots.append(shot(page, "C16", "preview"))
            with page.expect_response(lambda r: "invoice" in r.url and r.request.method == "POST") as res:
                page.click("#tm-preview-confirm")
            c.ok(res.value.status in (200, 201), f"invoiced ({res.value.status} {res.value.text()[:150]})")
            page.wait_for_timeout(700)
            c.ok("12.5" not in page.inner_text("#tm-ready-body"), "nothing left to invoice")
        else:
            c.ok(False, "no invoice button for the ready time")
        ux(page, "C16", "time", lang="fa", shot_name="C16-preview.png")
    finally:
        ctx.close()
    c.done()


def c17_po(browser):
    c = Check("C17")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "purchase-orders")
        od = page.input_value("#po-order-date")
        c.ok(bool(od), f"order date starts at today ({od or 'empty'}) — #258")
        if not od:
            page.fill("#po-order-date", "2026-10-02")
        pick(page, "#po-supplier", "سپهر")
        rows = page.locator("#po-lines-body tr")
        rows.nth(0).locator("input").nth(0).fill("کاتالوگ چاپی")
        rows.nth(0).locator("input").nth(1).fill("500")
        rows.nth(0).locator("input").nth(2).fill("")
        rows.nth(0).locator("input").nth(2).press_sequentially("۴۰۰۰۰")
        with page.expect_response(lambda r: "/purchase-orders" in r.url and r.request.method == "POST") as res:
            page.click("#po-create-btn")
        c.ok(res.value.status in (200, 201), f"PO created ({res.value.status} {res.value.text()[:150]})")
        po = res.value.json() if res.value.status in (200, 201) else {}
        clear_alert(page)
        page.wait_for_timeout(700)
        c.shots.append(shot(page, "C17", "po-created"))
        lst = page.inner_text("#po-list-body")
        c.ok(FA(lst) and "draft" not in lst, f"PO list in Persian «{lst[:120]}»")
        ux(page, "C17", "purchase orders", lang="fa", shot_name="C17-po-created.png")
    finally:
        ctx.close()
    c.done()


def c18_payroll(browser):
    c = Check("C18")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "payroll")
        for name, typ, base in (("علی رضایی", None, "۱۵۰۰۰۰۰۰۰"), ("مریم کاظمی", "hourly", None)):
            pick(page, "#pr-emp", name)
            if typ:
                page.select_option("#pr-type", typ)
                page.fill("#pr-rate", ""); page.locator("#pr-rate").press_sequentially("900000")
            else:
                page.fill("#pr-base", ""); page.locator("#pr-base").press_sequentially(base)
            with page.expect_response(lambda r: "/payroll" in r.url and r.request.method in ("POST", "PUT")) as res:
                page.click("#pr-save-profile")
            c.ok(res.value.status in (200, 201), f"profile {name} ({res.value.status} {res.value.text()[:150]})")
            clear_alert(page)
        page.wait_for_timeout(600)
        prof = page.inner_text("#pr-profiles-body")
        c.ok("علی" in prof and "مریم" in prof and "monthly" not in prof, f"profiles in Persian «{prof[:140]}»")
        page.fill("#pr-start", "2026-09-23"); page.fill("#pr-end", "2026-10-22"); page.fill("#pr-paydate", "2026-10-22")
        with page.expect_response(lambda r: "/payroll" in r.url and r.request.method == "POST") as res:
            page.click("#pr-run-btn")
        page.wait_for_timeout(1000)
        m = alert_text(page, 2500)
        c.note(f"run → {res.value.status} «{m[:200]}»")
        c.ok(res.value.status in (200, 201) or FA(m), f"run made, or refused in Persian ({res.value.status} «{m[:150]}»)")
        c.shots.append(shot(page, "C18", "run"))
        ux(page, "C18", "payroll", lang="fa", shot_name="C18-run.png")
    finally:
        ctx.close()
    c.done()


def c19_assets(browser):
    c = Check("C19")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "fixed-assets")
        page.fill("#asset-name", f"لپ‌تاپ حسابداری {RUN}")
        page.select_option("#asset-category", index=min(2, len(opts(page, "#asset-category")) - 1))
        page.fill("#asset-cost", ""); page.locator("#asset-cost").press_sequentially("۳۶۰۰۰۰۰۰۰")
        page.fill("#asset-acquired", "2026-04-21"); page.fill("#asset-in-service", "2026-04-21")
        page.select_option("#asset-method", index=0)
        if page.locator("#asset-life").is_visible():
            page.fill("#asset-life", "36")
        with page.expect_response(lambda r: "/fixed-assets" in r.url and r.request.method == "POST") as res:
            page.click("#asset-save")
        c.ok(res.value.status in (200, 201), f"asset registered ({res.value.status} {res.value.text()[:160]})")
        page.wait_for_timeout(700)
        msg = page.inner_text("#asset-save-msg") if page.locator("#asset-save-msg").count() else ""
        c.ok(FA(msg) or not msg, f"saved message Persian «{msg}»")
        page.fill("#asset-run-through", "2026-09-22")
        with page.expect_response(lambda r: "/fixed-assets" in r.url) as res2:
            page.click("#asset-run-preview")
        page.wait_for_timeout(700)
        pv = page.inner_text("#asset-run-result")
        c.ok(FA(pv), f"preview in Persian «{pv[:160]}»")
        # depreciation starts the month after entering service (Article 149): 4 months, not 5 (run 1 ✳)
        months = pv.count("10,000,000")
        c.ok(months == 4, f"4 months × 10,000,000 previewed ({months} rows)")
        c.shots.append(shot(page, "C19", "assets"))
        ux(page, "C19", "fixed assets", lang="fa", shot_name="C19-assets.png")
    finally:
        ctx.close()
    c.done()


def c20_equity(browser):
    c = Check("C20")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "equity")
        for name, pct, shares in (("حسین آرمان", "60", "600"), ("نرگس آرمان", "40", "400")):
            page.evaluate("() => document.querySelectorAll('.card[data-page=\"equity\"] details').forEach(d => { d.open = true; })")
            pick(page, "#equity-sh-entity", name)
            page.fill("#equity-sh-percent", pct); page.fill("#equity-sh-shares", shares)
            with page.expect_response(lambda r: "/equity" in r.url and r.request.method == "POST") as res:
                page.click("#equity-sh-add")
            c.ok(res.value.status in (200, 201), f"{name} {pct}% ({res.value.status} {res.value.text()[:120]})")
            clear_alert(page)
        page.wait_for_timeout(700)
        tot = page.inner_text("#equity-total-percent")
        c.ok("100" in tot or "۱۰۰" in tot, f"cap table 100% «{tot}»")
        pick(page, "#equity-contrib-entity", "حسین")
        page.fill("#equity-contrib-amount", ""); page.locator("#equity-contrib-amount").press_sequentially("۳۰۰۰۰۰۰۰۰")
        with page.expect_response(lambda r: "/equity" in r.url and r.request.method == "POST") as res:
            page.click("#equity-contrib-post")
        c.ok(res.value.status in (200, 201), f"contribution posted ({res.value.status} {res.value.text()[:150]})")
        clear_alert(page)
        page.fill("#equity-div-amount", "100000000")
        with page.expect_response(lambda r: "/equity" in r.url and r.request.method == "POST") as res:
            page.click("#equity-div-post")
        c.ok(res.value.status in (200, 201), f"dividend declared ({res.value.status} {res.value.text()[:150]})")
        clear_alert(page)
        page.wait_for_timeout(700)
        cap = page.inner_text("#equity-captable-body")
        c.ok("60,000,000" in cap and "40,000,000" in cap, f"dividend split 60/40 «{cap[:200]}»")
        c.shots.append(shot(page, "C20", "equity"))
        ux(page, "C20", "equity", lang="fa", shot_name="C20-equity.png")
    finally:
        ctx.close()
    c.done()


def c21_budgets(browser):
    c = Check("C21")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "dashboard")
        if page.locator("#dash-tab-spend").count():
            page.click("#dash-tab-spend"); page.wait_for_load_state("networkidle")   # budgets: Spending & profit (#36)
        mopts = opts(page, "#budget-month-jalali") if page.locator("#budget-month-jalali").count() else []
        c.note(f"month picker: {[l for _, l in mopts][:4]}")
        c.ok(any("مهر" in l for _, l in mopts), "budget months are Jalali, by name")
        pick(page, "#budget-month", "مهر")
        page.fill("#budget-category", "6112")
        page.fill("#budget-limit", ""); page.locator("#budget-limit").press_sequentially("۵۰۰۰۰۰۰۰")
        with page.expect_response(lambda r: "/budgets" in r.url and r.request.method in ("POST", "PUT")) as res:
            page.click("#budget-save")
        c.ok(res.value.status in (200, 201), f"budget saved ({res.value.status} {res.value.text()[:150]})")
        page.wait_for_timeout(800)
        wrap = page.inner_text("#budget-wrap")
        c.ok(FA(wrap), f"budget table Persian «{wrap[:160]}»")
        c.shots.append(shot(page, "C21", "budget", full=False, selector="#budget-wrap"))
    finally:
        ctx.close()
    c.done()


def c22_fx(browser):
    c = Check("C22")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        go(page, "settings")
        page.wait_for_timeout(500)
        if page.locator("#fx-from").count():
            page.locator("#fx-from").fill("USD") if page.locator("#fx-from").evaluate("e => e.tagName") == "INPUT" else page.select_option("#fx-from", "USD")
            page.locator("#fx-to").fill("IRR") if page.locator("#fx-to").evaluate("e => e.tagName") == "INPUT" else page.select_option("#fx-to", "IRR")
            page.fill("#fx-rate", ""); page.locator("#fx-rate").press_sequentially("۹۸۰۰۰۰")
            page.fill("#fx-effective", "2026-10-01")
            with page.expect_response(lambda r: "/fx" in r.url and r.request.method == "POST") as res:
                page.click("#fx-add-rate-btn")
            c.ok(res.value.status in (200, 201), f"USD→IRR rate saved ({res.value.status} {res.value.text()[:150]})")
            page.wait_for_timeout(700)
            hist = page.inner_text("#fx-rates-wrap") if page.locator("#fx-rates-wrap").count() else ""
            c.ok("980,000" in hist or "۹۸۰" in hist, "rate listed")
            c.ok("1405/07/09" in hist, "effective date in Jalali")
            c.shots.append(shot(page, "C22", "fx", full=False, selector="#fx-rates-wrap"))
        else:
            c.ok(False, "no FX rate form in settings")
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (c12_statement, c13_sms, c15_claims, c16_time, c17_po, c18_payroll, c19_assets, c20_equity, c21_budgets, c22_fx):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:400])
        b.close()
