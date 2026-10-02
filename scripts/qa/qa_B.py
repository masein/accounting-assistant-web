"""Group B — master data for Arman (SCENARIOS.md B1–B4), as the accountant, in Persian."""
import json
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import OUT, Check, alert_text, api, clear_alert, go, new_session, shot, ux, wait_until

FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))
STATE = f"{OUT}/state.json"


def save_state(**kw):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    st.update(kw)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=1)


def flat(tree, out=None):
    out = [] if out is None else out
    for n in tree or []:
        out.append(n)
        flat(n.get("children"), out)
    return out


def b1_chart(browser):
    c = Check("B1")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "accounts")
        wait_until(page, "() => document.querySelectorAll('#coa-tree button[data-coa]').length > 0", timeout_ms=10000)
        c.shots.append(shot(page, "B1", "tree"))
        ux(page, "B1", "accounts", lang="fa", shot_name="B1-tree.png")
        s, tree = api(page, "GET", "/accounts/tree", None)
        nodes = flat(tree if isinstance(tree, list) else (tree or {}).get("children") or (tree or {}).get("tree") or [])
        if not nodes:
            s, lst = api(page, "GET", "/accounts", None)
            nodes = lst if isinstance(lst, list) else []
        bank = next((n for n in nodes if "بانک" in (n.get("name") or "") and len(n.get("code", "")) <= 4), None)
        cash = next((n for n in nodes if ("صندوق" in (n.get("name") or "") or "نقد" in (n.get("name") or "")) and len(n.get("code", "")) <= 4), None)
        capital = next((n for n in nodes if "سرمایه" in (n.get("name") or "") and len(n.get("code", "")) <= 4), None)
        rent = next((n for n in nodes if "اجاره" in (n.get("name") or "")), None)
        c.ok(bank and cash and capital, f"seeded chart has bank/cash/capital: {[ (n or {}).get('code') for n in (bank, cash, capital)]}")
        save_state(bank_parent=(bank or {}).get("code"), cash_code=(cash or {}).get("code"), capital_code=(capital or {}).get("code"),
                   rent_code=(rent or {}).get("code"))
        # add a bank sub-account under the bank group
        page.fill("#coa-new-parent", bank["code"])
        page.locator("#coa-new-parent").dispatch_event("change")
        wait_until(page, "() => document.getElementById('coa-new-code').value")
        sug = page.input_value("#coa-new-code")
        c.ok(sug.startswith(bank["code"]), f"suggested code {sug} starts with {bank['code']}")
        page.fill("#coa-new-name", "بانک ملت جاری")
        with page.expect_response(lambda r: r.url.endswith("/accounts") and r.request.method == "POST") as res:
            page.click("#coa-new-save")
        c.ok(res.value.status in (200, 201), f"sub-account created ({res.value.status} {res.value.text()[:150]})")
        new_code = (res.value.json() or {}).get("code", sug) if res.value.status in (200, 201) else sug
        msg = page.inner_text("#coa-new-msg")
        c.ok(FA(msg), f"confirmation Persian «{msg}»")
        save_state(bank_code=new_code)
        # a code outside its parent
        page.fill("#coa-new-parent", bank["code"]); page.fill("#coa-new-code", "9999"); page.fill("#coa-new-name", "اشتباه")
        with page.expect_response(lambda r: r.url.endswith("/accounts") and r.request.method == "POST") as res:
            page.click("#coa-new-save")
        msg = page.inner_text("#coa-new-msg")
        c.ok(res.value.status >= 400 and FA(msg), f"a child code outside its parent is refused in Persian ({res.value.status} «{msg}»)")
        # rename through the app's prompt
        page.wait_for_timeout(600)
        node = page.locator(f'#coa-tree button[data-coa="rename"]').filter(has=page.locator("xpath=.")).first
        s, accs = api(page, "GET", "/accounts", None)
        acc = next((a for a in (accs or []) if a.get("code") == new_code), None)
        if acc:
            btn = page.locator(f'#coa-tree button[data-coa="rename"][data-id="{acc["id"]}"]')
            if btn.count():
                btn.locator('xpath=ancestor::details[1]/summary').click()  # under ⋯ (#277)
                btn.click()
                c.ok(page.locator("#ui-prompt-modal").is_visible(), "rename asks in the app's dialog")
                page.fill("#ui-prompt-input", "بانک ملت - جاری ۱۲۳")
                page.click("#ui-prompt-ok")
                page.wait_for_timeout(800)
                s, a2 = api(page, "GET", "/accounts", None)
                c.ok(any(x.get("code") == new_code and "۱۲۳" in (x.get("name") or "") or "123" in (x.get("name") or "") for x in (a2 or [])), "renamed")
            else:
                c.ok(False, "no rename button for the new account")
        # deactivate an unused leaf; delete a used parent is refused (it has children)
        delb = page.locator(f'#coa-tree button[data-coa="delete"][data-id="{bank["id"]}"]')
        if delb.count():
            delb.locator('xpath=ancestor::details[1]/summary').click()  # under ⋯ (#277)
            delb.click()
            if page.locator("#ui-confirm-modal").is_visible():
                page.click("#ui-confirm-ok")
            page.wait_for_timeout(800)
            m = alert_text(page, 3000) or page.inner_text("#coa-new-msg")
            c.ok(FA(m), f"deleting a parent with sub-accounts refused in Persian «{m}»")
            clear_alert(page)
        c.shots.append(shot(page, "B1", "after-edits"))
        c.ok(watch.problems() == [] or all(p.split()[0] in ("400", "409", "422") for p in watch.problems()), f"problems {watch.problems()}")
    finally:
        ctx.close()
    c.done()


def b2_opening(browser):
    c = Check("B2")
    st = json.load(open(STATE))
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "accounts")
        wait_until(page, "() => document.querySelectorAll('#coa-opening-grid input').length > 0", timeout_ms=10000)
        page.fill("#coa-opening-date", "2026-03-21")         # 1 Farvardin 1405
        def put(code, dr=None, cr=None):
            if dr is not None:
                page.fill(f'#coa-opening-grid input.coa-dr[data-code="{code}"]', str(dr))
            if cr is not None:
                page.fill(f'#coa-opening-grid input.coa-cr[data-code="{code}"]', str(cr))
        for code in (st["cash_code"], st["bank_code"], st["capital_code"]):
            c.ok(page.locator(f'#coa-opening-grid input[data-code="{code}"]').count() == 2, f"opening grid has {code}")
        put(st["cash_code"], dr=500_000_000)
        put(st["bank_code"], dr=1_200_000_000)
        put(st["capital_code"], cr=1_000_000_000)          # unbalanced on purpose
        page.wait_for_timeout(300)
        tot = page.inner_text("#coa-opening-totals")
        c.ok(FA(tot), f"totals line Persian «{tot}»")
        # an unbalanced opening is not refused: the difference goes to an adjustment, and the message says how much
        page.click("#coa-opening-save")
        c.ok(page.locator("#ui-confirm-modal").is_visible(), "saving asks to confirm (app dialog)")
        with page.expect_response(lambda r: "/accounts/opening-balances" in r.url and r.request.method == "PUT") as res:
            page.click("#ui-confirm-ok")
        page.wait_for_timeout(700)
        msg = page.inner_text("#coa-opening-msg")
        c.ok(res.value.status in (200, 201) and FA(msg) and ("700" in msg or "۷۰۰" in msg), f"unbalanced → adjustment of 700,000,000 named ({res.value.status} «{msg}»)")
        c.shots.append(shot(page, "B2", "unbalanced", full=False, selector="#coa-opening-grid"))
        wait_until(page, "() => document.querySelectorAll('#coa-opening-grid input').length > 0", timeout_ms=8000)
        page.fill("#coa-opening-date", "2026-03-21")
        put(st["cash_code"], dr=500_000_000); put(st["bank_code"], dr=1_200_000_000)
        put(st["capital_code"], cr=1_700_000_000)
        page.click("#coa-opening-save")
        with page.expect_response(lambda r: "/accounts/opening-balances" in r.url and r.request.method == "PUT") as res:
            page.click("#ui-confirm-ok")
        page.wait_for_timeout(600)
        msg = page.inner_text("#coa-opening-msg")
        c.ok(res.value.status in (200, 201), f"balanced posts ({res.value.status} {res.value.text()[:150]})")
        c.ok(FA(msg), f"saved message Persian «{msg}»")
        s, tb = api(page, "GET", "/reports/trial-balance?to_date=2026-03-31", None)
        c.note(f"trial balance → {s}")
        c.shots.append(shot(page, "B2", "saved"))
    finally:
        ctx.close()
    c.done()


PARTIES = [("client", "شرکت پارس‌افزار", "۱۰۱"), ("client", "فروشگاه مهرگان", "۱۰۲"), ("client", "كافه نارنج", "۱۰۳"),
           ("supplier", "پخش البرز", "۲۰۱"), ("supplier", "چاپ سپهر", "۲۰۲"), ("bank", "بانک ملت", "۳۰۱"),
           ("employee", "علی رضایی", "۴۰۱"), ("employee", "مریم کاظمی", "۴۰۲"),
           ("shareholder", "حسین آرمان", "۵۰۱"), ("shareholder", "نرگس آرمان", "۵۰۲"), ("client", "مشتری حذفی", "۹۰۹")]


def b3_parties(browser):
    c = Check("B3")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    ids = {}
    try:
        go(page, "entities")
        for typ, name, code in PARTIES:
            page.select_option("#entity-type", typ)
            page.fill("#entity-name", name)
            page.fill("#entity-code", code)
            if page.locator("#entity-phone").count() and page.locator("#entity-phone").is_visible():
                page.fill("#entity-phone", "۰۹۱۲۳۴۵۶۷۸۹")
            if typ == "bank":
                # a bank's code is its ledger account (#276): «۳۰۱» isn't one → refused in Persian; empty opens one
                c.ok(page.locator("#entity-code-hint").is_visible(), "the code field says what a bank's code is")
                with page.expect_response(lambda r: r.url.endswith("/entities") and r.request.method == "POST") as res:
                    page.click("#entity-add")
                msg = alert_text(page)
                c.ok(res.value.status == 422 and FA(msg), f"a bank code that isn't an account is refused in Persian ({res.value.status} «{msg[:80]}»)")
                clear_alert(page)
                page.fill("#entity-code", "")
            with page.expect_response(lambda r: r.url.endswith("/entities") and r.request.method == "POST") as res:
                page.click("#entity-add")
            ok = res.value.status in (200, 201)
            c.ok(ok, f"{name} added ({res.value.status} {res.value.text()[:120]})")
            if ok:
                ids[name] = res.value.json()["id"]
            clear_alert(page)
        s, ent = api(page, "GET", f"/entities/{ids.get('شرکت پارس‌افزار')}", None)
        c.ok(s == 200 and ent.get("code") == "101", f"code typed in Persian digits stored as 0–9 ({ent.get('code') if s == 200 else s})")
        # a duplicate name
        page.select_option("#entity-type", "client"); page.fill("#entity-name", "شرکت پارس‌افزار"); page.fill("#entity-code", "")
        with page.expect_response(lambda r: r.url.endswith("/entities") and r.request.method == "POST") as res:
            page.click("#entity-add")
        msg = alert_text(page)
        c.ok(res.value.status == 409 and FA(msg), f"duplicate warned in Persian ({res.value.status} «{msg[:120]}»)")
        clear_alert(page)
        # search across letterforms: كافه (Arabic kaf) finds the Persian-typed name too, and the other way round
        page.fill("#entity-search", "کافه")
        page.wait_for_timeout(900)
        rows = page.inner_text("#entities-tbody")
        c.ok("نارنج" in rows, "search «کافه» finds «كافه نارنج»")
        page.fill("#entity-search", "")
        page.wait_for_timeout(700)
        c.shots.append(shot(page, "B3", "list"))
        ux(page, "B3", "entities", lang="fa", shot_name="B3-list.png")
        rows = page.inner_text("#entities-tbody")
        for t in ("مشتری", "تأمین‌کننده", "بانک", "کارمند", "سهامدار"):
            c.ok(t in rows, f"type «{t}» shown in Persian")
        # edit one
        eid = ids.get("فروشگاه مهرگان")
        page.click(f'.edit-entity[data-entity-id="{eid}"]')
        page.wait_for_timeout(400)
        c.ok(page.locator("#entity-edit-modal").is_visible(), "edit opens")
        c.shots.append(shot(page, "B3", "edit", full=False))
        page.fill("#entity-edit-name", "فروشگاه مهرگان (شعبه ۲)")
        page.evaluate("() => document.querySelectorAll('#entity-edit-modal details').forEach(d => { d.open = true; })")
        if page.locator("#entity-edit-payment_terms").is_visible():
            page.fill("#entity-edit-payment_terms", "Net 30")
        page.click("#entity-edit-save")
        page.wait_for_timeout(800)
        c.ok(FA(alert_text(page, 3000)), "edit saved message Persian")
        clear_alert(page)
        # delete the unused one, through the app's confirm
        did = ids.get("مشتری حذفی")
        page.click(f'.delete-entity[data-entity-id="{did}"]')
        page.wait_for_timeout(300)
        c.ok(page.locator("#ui-confirm-modal").is_visible(), "delete asks to confirm (app dialog)")
        page.click("#ui-confirm-ok")
        page.wait_for_timeout(800)
        s, _ = api(page, "GET", f"/entities/{did}", None)
        c.ok(s == 404, f"deleted ({s})")
        expected = ("409 POST /entities", "422 POST /entities", "404 GET /entities/")
        c.ok(all(p.startswith(expected) for p in watch.problems()), f"problems {watch.problems()}")
        ids.pop("مشتری حذفی", None)
        save_state(parties=ids)
    finally:
        ctx.close()
    c.done()


def b4_stock(browser):
    c = Check("B4")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "inventory")
        items = [("کاغذ A4", "PAP-A4", "بسته", "۶۲۹۱۰۰۰۰۰۱"), ("کارتریج لیزری", "TON-12", "عدد", "")]
        made = {}
        for name, sku, unit, barcode in items:
            page.fill("#mgr-inv-item-name", name); page.fill("#mgr-inv-item-sku", sku); page.fill("#mgr-inv-item-unit", unit)
            page.fill("#mgr-inv-item-barcode", barcode)      # empty clears what the last item left
            page.fill("#mgr-inv-item-reorder", "10")
            with page.expect_response(lambda r: r.url.endswith("/manager-reports/inventory/items") and r.request.method == "POST") as res:
                page.click("#mgr-add-item-btn")
            if res.value.status == 409:      # made by an earlier run
                s0, lst = api(page, "GET", "/manager-reports/inventory/items", None)
                made[name] = next(x["id"] for x in lst if x.get("name") == name)
                clear_alert(page)
                continue
            c.ok(res.value.status in (200, 201), f"{name} added ({res.value.status} {res.value.text()[:100]})")
            if res.value.status in (200, 201):
                made[name] = res.value.json()["id"]
                if barcode:
                    s0, it = api(page, "GET", "/manager-reports/inventory/items", None)
                    bc = next((x.get("barcode") for x in it if x.get("id") == made[name]), None)
                    c.ok(bc == "6291000001", f"barcode typed in Persian digits stored as 0–9 ({bc})")
            m = alert_text(page, 3000)
            c.ok(FA(m), f"item message Persian «{m}»")
            clear_alert(page)
        page.wait_for_timeout(700)
        for name, qty, cost in (("کاغذ A4", "۴۰", "۲۵۰۰۰۰"), ("کارتریج لیزری", "15", "4500000")):
            page.select_option("#mgr-mv-item", made[name])
            page.select_option("#mgr-mv-type", "IN")
            # typed as on a Persian keyboard (fill() refuses non-ASCII in a number field; the app converts keystrokes)
            for sel, val in (("#mgr-mv-qty", qty), ("#mgr-mv-cost", cost)):
                page.fill(sel, ""); page.locator(sel).press_sequentially(val)
            with page.expect_response(lambda r: r.url.endswith("/manager-reports/inventory/movements") and r.request.method == "POST") as res:
                page.click("#mgr-add-mv-btn")
            c.ok(res.value.status in (200, 201), f"stock in for {name} ({res.value.status} {res.value.text()[:100]})")
            clear_alert(page)
        s, bal = api(page, "GET", "/manager-reports/inventory/balance", None)
        rows = (bal or {}).get("rows", []) if isinstance(bal, dict) else []
        paper = next((r for r in rows if r.get("item_name") == "کاغذ A4"), {})
        c.ok(paper.get("on_hand_qty") == 40 and paper.get("inventory_value") == 10_000_000,
             f"paper: 40 on hand worth 10,000,000 ({paper.get('on_hand_qty')}, {paper.get('inventory_value')})")
        page.click("#inv-run-balance-btn"); page.wait_for_timeout(900)
        c.shots.append(shot(page, "B4", "balance"))
        ux(page, "B4", "inventory", lang="fa", shot_name="B4-balance.png")
        save_state(items=made)
        c.ok(watch.problems() == [], f"problems {watch.problems()}")
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (b1_chart, b2_opening, b3_parties, b4_stock):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:500])
        b.close()
