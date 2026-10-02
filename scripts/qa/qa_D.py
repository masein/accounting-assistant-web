"""Groups D (reports, control, compliance), E (AI chat, migration), F (roles) — Arman, Persian."""
import json
import random
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import BASE, OUT, Check, alert_text, api, clear_alert, go, new_session, shot, ux, wait_until, confirm_shown

FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))
st = json.load(open(f"{OUT}/state.json"))
RUN = str(random.randint(100, 999))
REPORTS = ["balance_sheet", "income_statement", "comprehensive_income", "changes_in_equity", "cash_flow", "general_journal",
           "general_ledger", "trial_balance", "debtor_creditor", "sales_by_product", "sales_by_invoice", "purchase_by_product",
           "purchase_by_invoice", "accounts_payable"]


def d1_search(browser):
    c = Check("D1")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        for q, want in (("اجاره", True), ("اجاره", True), ("RNT-", True), ("هیچ‌چیز-نیست-xyz", False)):
            s, res = api(page, "GET", f"/reports/transactions/search?search={q}", None)
            rows = (res or {}).get("rows", []) if isinstance(res, dict) else []
            c.ok(s == 200 and (bool(rows) == want), f"search «{q}» → {len(rows)} rows ({s})")
        go(page, "ledger")
        page.wait_for_timeout(800)
        row = page.locator('.card[data-page="ledger"] tr').filter(has_text="6112").first
        if row.count():
            row.click()
            page.wait_for_timeout(1200)
            modal = page.locator("#account-modal-body")
            c.ok(modal.is_visible(), "an account row opens its detail")
            txt = modal.inner_text() if modal.is_visible() else ""
            c.ok("1405/07/07" in txt, "the rent line shows its Jalali date")
            c.shots.append(shot(page, "D1", "account-detail", full=False))
            ux(page, "D1", "account detail", lang="fa", shot_name="D1-account-detail.png")
            page.keyboard.press("Escape")
        else:
            c.ok(False, "no 6112 row on the ledger summary")
    finally:
        ctx.close()
    c.done()


def d2_statements(browser):
    c = Check("D2")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        s0, tb0 = api(page, "GET", "/manager-reports/books/trial-balance?to_date=2026-12-31", None)
        c.ok(s0 == 200 and (tb0 or {}).get("total", 0) > 0, f"trial balance up to a date (no start) is not empty ({(tb0 or {}).get('total')} rows)")
        s, tb = api(page, "GET", "/manager-reports/books/trial-balance?from_date=2026-03-21&to_date=2026-12-31&page_size=500", None)
        if s == 200:
            rows = tb.get("rows") or tb.get("lines") or []
            dr = sum((r.get("debit_turnover") or r.get("debit") or 0) for r in rows)
            cr = sum((r.get("credit_turnover") or r.get("credit") or 0) for r in rows)
            c.ok(dr == cr and dr > 0, f"trial balance balances (Dr {dr:,} / Cr {cr:,})")
        else:
            c.ok(False, f"trial balance → {s}")
        s, bs = api(page, "GET", "/manager-reports/financial/iran/balance-sheet?to_date=2026-12-31", None)
        if s == 200:
            by = {r.get("key"): r.get("amount_current") for r in bs.get("rows", [])}
            ta = next((v for k, v in by.items() if k and k.startswith("total_assets")), None)
            tle = next((v for k, v in by.items() if k and ("total_liabilities_and_equity" in k or "total_equity_and_liabilities" in k)), None)
            c.note(f"BS total keys: {[k for k in by if k and k.startswith('total')]}")
            c.note(f"BS assets {ta} L+E {tle}")
            c.ok(ta is not None and ta == tle, f"balance sheet A = L + E ({ta} vs {tle})")
        else:
            c.ok(False, f"Iran balance sheet → {s} {str(bs)[:150]}")
        s, pl = api(page, "GET", "/manager-reports/financial/iran/income-statement?from_date=2026-03-21&to_date=2026-12-31", None)
        c.ok(s == 200, f"Iran income statement → {s}")
        if s == 200:
            c.note(f"P&L net {pl.get('net_income') or pl.get('net_profit')}")
        go(page, "manager")
        page.fill("#mgr-from-date", "2026-03-21"); page.fill("#mgr-to-date", "2026-12-31")
        empty, english = [], []
        for rt in REPORTS:
            if not page.locator(f'#mgr-report-type option[value="{rt}"]').count():
                continue
            page.select_option("#mgr-report-type", rt)
            with page.expect_response(lambda r: "/manager-reports/" in r.url, timeout=20000) as res:
                page.click("#mgr-run-btn")
            page.wait_for_timeout(900)
            if res.value.status != 200:
                c.ok(False, f"{rt} → {res.value.status} {res.value.text()[:120]}")
                continue
            prev = page.inner_text("#mgr-report-preview")
            if not prev.strip():
                empty.append(rt)
            hits = ux(page, "D2", f"report {rt}", lang="fa", shot_name=f"D2-{rt}.png")
            shot(page, "D2", rt, full=False, selector="#mgr-report-preview")
            english += [h[1] for h in hits if h[0] == "I3"]
        c.ok(not empty, f"reports with an empty preview: {empty}")
        c.ok(not english, f"English in reports: {english[:6]}")
        for kind, fmt in (("balance_sheet", "pdf"), ("income_statement", "xlsx")):
            r = page.request.get(f"{BASE}/manager-reports/financial/export?report={kind}&format={fmt}&to_date=2026-12-31&from_date=2026-03-21")
            c.ok(r.status == 200 and len(r.body()) > 1000, f"export {kind}.{fmt} → {r.status} ({len(r.body())} bytes)")
            if r.status == 200:
                open(f"{OUT}/D2-{kind}.{fmt}", "wb").write(r.body())
    finally:
        ctx.close()
    c.done()


def d3_d4_dashboards(browser):
    c3, c4 = Check("D3"), Check("D4")
    ctx, page, watch = new_session(browser, "arman_cfo", lang="fa")
    try:
        go(page, "dashboard")
        page.wait_for_timeout(1500)
        c3.shots.append(shot(page, "D3", "dashboard"))
        hits = ux(page, "D3", "dashboard", lang="fa", shot_name="D3-dashboard.png")
        s, od = api(page, "GET", "/reports/owner-dashboard", None)
        c3.ok(s == 200, f"owner dashboard → {s}")
        txt = page.inner_text('.card[data-page="dashboard"]')
        c3.ok("NaN" not in txt and "undefined" not in txt, "no broken numbers")
        # one section at a time under the top (#36): each tab, its own look
        tabs = page.evaluate("() => [...document.querySelectorAll('#dash-tabs [role=tab]')].map(b => b.dataset.tab)")
        c3.ok(tabs == ["cash", "arap", "spend", "books"], f"dashboard tabs {tabs}")
        for tab in tabs:
            page.click(f"#dash-tab-{tab}")
            page.wait_for_load_state("networkidle"); page.wait_for_timeout(400)
            c3.shots.append(shot(page, "D3", f"dashboard-{tab}"))
            ux(page, "D3", f"dashboard / {tab}", lang="fa", shot_name=f"D3-dashboard-{tab}.png")
            t3 = page.inner_text(f"#dash-panel-{tab}")
            c3.ok("NaN" not in t3 and "undefined" not in t3, f"{tab}: no broken numbers")
        h = page.evaluate("() => document.querySelector('.card[data-page=\"dashboard\"]').getBoundingClientRect().height")
        c3.ok(h < 3000, f"dashboard height {round(h)} px (was about 4,400)")
        page.click("#dash-tab-cash")
        for name in ("ceo", "cfo"):
            go(page, name)
            page.wait_for_timeout(1500)
            c4.shots.append(shot(page, "D4", name))
            ux(page, "D4", name, lang="fa", shot_name=f"D4-{name}.png")
            t = page.inner_text(f'.card[data-page="{name}"]')
            c4.ok("NaN" not in t and "undefined" not in t, f"{name}: no broken numbers")
        go(page, "manager")
        c3.ok(watch.problems() == [], f"problems {watch.problems()}")
    finally:
        ctx.close()
    c3.done(); c4.done()


def d5_audit(browser):
    c = Check("D5")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "audit")
        wait_until(page, "() => document.querySelectorAll('#audit-log-body tr').length > 0", timeout_ms=10000)
        n = page.locator("#audit-log-body tr").count()
        c.ok(n >= 10, f"audit trail has the run's actions ({n} rows)")
        with page.expect_response(lambda r: "/audit/report" in r.url, timeout=30000):
            page.click("#audit-run-btn")
        wait_until(page, "() => document.querySelectorAll('#audit-findings-list > *').length > 0", timeout_ms=15000)
        f = page.inner_text("#audit-findings-list")
        c.ok(FA(f), f"findings Persian «{f[:160]}»")
        c.shots.append(shot(page, "D5", "audit"))
        ux(page, "D5", "audit", lang="fa", shot_name="D5-audit.png")
    finally:
        ctx.close()
    c.done()


def d6_lock(browser):
    c = Check("D6")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        go(page, "settings")
        page.fill("#closed-period-input", "2026-09-22")      # end of Shahrivar 1405
        with page.expect_response(lambda r: "/admin/closed-period" in r.url and r.request.method == "PUT") as res:
            page.click("#closed-period-save")
            if confirm_shown(page):
                page.click("#ui-confirm-ok")
        c.ok(res.value.status == 200, f"books locked ({res.value.status} {res.value.text()[:120]})")
        page.wait_for_timeout(500)
        stt = page.inner_text("#closed-period-status")
        c.ok("1405/06/31" in stt, f"lock date shown in Jalali «{stt}»")
        go(page, "transactions")
        page.fill("#date", "2026-09-10")
        rows = page.locator("#lines-tbody tr")
        rows.nth(0).locator(".line-code").fill("6112"); rows.nth(0).locator(".line-debit").fill("1000")
        rows.nth(1).locator(".line-code").fill(st["bank_code"]); rows.nth(1).locator(".line-credit").fill("1000")
        clear_alert(page)
        page.click("#submit-btn")
        if confirm_shown(page):
            page.click("#ui-confirm-ok")
        page.wait_for_timeout(1200)
        m = alert_text(page)
        c.ok(FA(m) and ("1405/06/31" in m or "۱۴۰۵" in m), f"a back-dated voucher refused in Persian, naming the lock date «{m}»")
        c.shots.append(shot(page, "D6", "refused", full=False))
    finally:
        ctx.close()
    c.done()


def d7_tax(browser):
    c = Check("D7")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "invoices")
        page.evaluate("() => document.querySelectorAll('.card[data-page=\"invoices\"] details').forEach(d => { d.open = true; })")
        if page.locator("#ttms-load").is_visible():
            with page.expect_response(lambda r: "ttms" in r.url or "quarterly" in r.url or "tax/ir" in r.url, timeout=20000) as res:
                page.click("#ttms-load")
            page.wait_for_timeout(1000)
            c.ok(res.value.status == 200, f"TTMS season loads ({res.value.status})")
            summ = page.inner_text("#ttms-summary")
            c.ok(FA(summ), f"TTMS summary Persian «{summ[:160]}»")
            c.shots.append(shot(page, "D7", "ttms", full=False, selector="#ttms-panel"))
        else:
            c.ok(False, "TTMS panel not visible")
        c.ok(page.locator("#mo-export").count() > 0, "Moadian export offered")
        rows = page.inner_text("#mo-tbody") if page.locator("#mo-tbody").count() else ""
        c.note(f"Moadian rows: «{rows[:120]}»")
    finally:
        ctx.close()
    c.done()


def d8_forecast(browser):
    c = Check("D8")
    ctx, page, watch = new_session(browser, "arman_cfo", lang="fa")
    try:
        go(page, "manager")
        page.wait_for_timeout(800)
        if not page.locator("#forecast-wrap").is_visible():
            go(page, "dashboard"); page.wait_for_timeout(800)
            if page.locator("#dash-tab-cash").count():
                page.click("#dash-tab-cash")                 # the forecast is under Cash (#36)
        vis = page.locator("#forecast-summary").is_visible()
        c.ok(vis, "cash forecast shown")
        if vis:
            fs = page.inner_text("#forecast-summary")
            c.ok(FA(fs) and "1405/" in fs or "مهر" in fs or "آبان" in fs, f"forecast in Persian with Jalali weeks «{fs[:160]}»")
            c.shots.append(shot(page, "D8", "forecast", full=False, selector="#forecast-wrap"))
        s, ins = api(page, "GET", "/insights", None)
        c.note(f"insights → {s} {len(ins) if isinstance(ins, list) else (len(ins.get('items', [])) if isinstance(ins, dict) else '')}")
    finally:
        ctx.close()
    c.done()


def e1_e2_chat(browser):
    c1, c2 = Check("E1"), Check("E2")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "ai-accountant")
        page.fill("#ai-acct-input", "مانده بانک ملت چقدر است؟")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat"), timeout=60000) as res:
            page.click("#ai-acct-send")
        wait_until(page, "() => document.querySelectorAll('.msg-row.assistant .msg').length > 0", timeout_ms=15000)
        bubble = page.locator(".msg-row.assistant .msg").last.inner_text()
        c1.ok(res.value.status == 502 and FA(bubble), f"no provider → Persian notice ({res.value.status} «{bubble[:120]}»)")
        c1.shots.append(shot(page, "E1", "chat", full=False))
        ux(page, "E1", "chat", lang="fa", shot_name="E1-chat.png")
        csv = f"{OUT}/mellat-chat-{RUN}.csv"
        open(csv, "w", encoding="utf-8").write("تاریخ,شرح,برداشت,واریز,مانده\n1405/07/12,خرید تونر,1800000,,100000000\n1405/07/13,واریز نقدی,,5000000,105000000\n")
        page.set_input_files("#ai-acct-file", csv)
        page.wait_for_timeout(600)
        page.fill("#ai-acct-input", "این صورتحساب را ثبت کن")
        with page.expect_response(lambda r: "/ai-accountant/chat" in r.url, timeout=60000) as res:
            page.click("#ai-acct-send")
        page.wait_for_timeout(2500)
        last = page.locator(".msg-row.assistant .msg").last.inner_text()
        c2.note(f"intake → {res.value.status} «{last[:200]}»")
        c2.ok(FA(last), "the reply is Persian")
        c2.shots.append(shot(page, "E2", "intake", full=False))
    finally:
        ctx.close()
    c1.done(); c2.done()


def e3_migration(browser):
    c = Check("E3")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "migration")
        csv = f"{OUT}/journal-{RUN}.csv"
        open(csv, "w", encoding="utf-8").write("date,voucher,account,description,debit,credit\n"
                                               f"2026-10-14,J{RUN}1,6112,هزینه آب و برق,2500000,0\n"
                                               f"2026-10-14,J{RUN}1,{st['bank_code']},هزینه آب و برق,0,2500000\n")
        page.set_input_files("#ji-file", csv)
        with page.expect_response(lambda r: "/migration" in r.url or "journal" in r.url, timeout=20000) as res:
            page.click("#ji-preview-btn")
        page.wait_for_timeout(1200)
        out = page.inner_text("#ji-result")
        c.ok(res.value.status == 200, f"journal import preview ({res.value.status} {res.value.text()[:160]})")
        c.ok(FA(out), f"preview in Persian «{out[:160]}»")
        c.shots.append(shot(page, "E3", "preview"))
        ux(page, "E3", "migration", lang="fa", shot_name="E3-preview.png")
    finally:
        ctx.close()
    c.done()


def f_roles(browser):
    c1, c2, c3 = Check("F1"), Check("F2"), Check("F3")
    ctx, page, watch = new_session(browser, "arman_emp", lang="fa")
    try:
        page.evaluate("() => { location.hash = 'invoices'; }")
        page.wait_for_timeout(1200)
        c1.ok(page.evaluate("() => activePage()") != "invoices", f"employee sent away from #invoices (on {page.evaluate('() => activePage()')})")
        page.evaluate("() => { location.hash = 'settings'; }")
        page.wait_for_timeout(1000)
        c1.ok(page.evaluate("() => activePage()") != "settings", "employee sent away from #settings")
        s, body = api(page, "POST", "/invoices", {"number": "X", "kind": "sales", "amount": 1, "issue_date": "2026-10-01", "due_date": "2026-10-02"})
        c1.ok(s == 403 and FA(json.dumps(body, ensure_ascii=False)), f"employee write → {s} {str(body)[:120]}")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_view", lang="fa")
    try:
        go(page, "ledger")
        c2.shots.append(shot(page, "F2", "viewer-ledger", full=False))
        s, body = api(page, "POST", "/transactions", {"date": "2026-10-01", "lines": [{"account_code": "6112", "debit": 1, "credit": 0},
                                                                                    {"account_code": "1110", "debit": 0, "credit": 1}]})
        c2.ok(s == 403 and FA(json.dumps(body, ensure_ascii=False)), f"viewer write → {s} {str(body)[:120]}")
        saves = page.evaluate("() => [...document.querySelectorAll('.card[data-page=\"ledger\"] button')].filter(b => b.offsetParent !== null).map(b => b.innerText.trim())")
        c2.note(f"buttons a viewer sees on the ledger: {saves[:10]}")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_mgr", lang="fa")
    try:
        s, body = api(page, "GET", "/invoices", None)
        c3.ok(s == 403, f"manager reads invoices → {s}")
    finally:
        ctx.close()
    c1.done(); c2.done(); c3.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (d1_search, d2_statements, d3_d4_dashboards, d5_audit, d7_tax, d8_forecast, e1_e2_chat, e3_migration, f_roles, d6_lock):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:400])
        b.close()
