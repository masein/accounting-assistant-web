"""Group J: what shipped since the last deep run (2026-10-07): sections Q (pence), S, T (stock in
the books), U (a party's transactions), X (parties from accounts), Y (bank SMS from the phone),
Z (a person's books currency), AA (what changed on the balance sheet), AC (reply, copy, share)
and AE (the web chat keeps its cards; a phone-made voucher seen and confirmed on the web).

The AI is blanked on the QA server, so what needs the model (the chat's own proposals, AB, R,
T22) is left to the pytest suites; a voucher here is made by the phone's statement walk, which
needs no model."""
import json
import random
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import BASE, OUT, PW, Check, alert_text, api, clear_alert, confirm_shown, go, lat, new_session, settle, shot, ux, wait_until

RUN = str(random.randint(100, 999))
FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))
MONTH_START = date.today().replace(day=1).isoformat()
STATE = f"{OUT}/state_J.json"
try:   # Arman's bank account, as group B made it
    BANK = json.load(open(f"{OUT}/state.json")).get("bank_code") or "1110"
except Exception:
    BANK = "1110"


def _save(**kw):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    st.update(kw)
    json.dump(st, open(STATE, "w"))


def _phone(page, username):
    """A phone's bearer session, as the app signs in."""
    r = page.request.post(f"{BASE}/api/mobile/v1/auth/login",
                          data={"username": username, "password": PW, "device_name": "QA Pixel", "platform": "android"})
    body = r.json() if r.ok else {}
    return body.get("access_token")


def _mobile(page, token, method, path, body=None, **kw):
    r = page.request.fetch(f"{BASE}/api/mobile/v1{path}", method=method, data=body,
                           headers={"Authorization": f"Bearer {token}"}, **kw)
    try:
        return r.status, r.json()
    except Exception:
        return r.status, r.text()


# ── Q, S: pence ──────────────────────────────────────────────────────────────────────────

def q_pence(browser):
    c = Check("Q2")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        s, txn = api(page, "POST", "/transactions", {"date": iso_days(-3), "description": f"Bank charge {RUN}", "reference": f"FEE-{RUN}",
                                                      "lines": [{"account_code": "8000", "debit": "7.50", "credit": 0},
                                                                {"account_code": "1200", "debit": 0, "credit": "7.50"}]})
        c.ok(s == 201, f"£7.50 voucher ({s} {str(txn)[:160]})")
        lines = (txn or {}).get("lines", []) if isinstance(txn, dict) else []
        c.ok(any(ln.get("debit") == 7.5 for ln in lines), f"its line keeps the pence ({[ln.get('debit') for ln in lines]})")
        s, tb = api(page, "GET", "/manager-reports/books/trial-balance", None)
        row = next((r for r in (tb or {}).get("rows", []) if "8000" in (str(r.get("account_code")), str(r.get("code")))), {})
        turnover = float(row.get("debit_turnover") or 0)
        c.ok(s == 200 and round(turnover * 100) % 100 == 50, f"the trial balance keeps the pence on 8000 ({row.get('debit_turnover')})")
        go(page, "ledger")
        settle(page)
        c.ok("7.50" in page.inner_text('.card[data-page="ledger"]'), "the ledger page shows £7.50")
        c.shots.append(shot(page, "Q2", "ledger"))
    finally:
        ctx.close()
    c.done()

    c = Check("Q3")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        s, err = api(page, "POST", "/transactions", {"date": iso_days(-3), "description": "too many places",
                                                      "lines": [{"account_code": "8000", "debit": "7.505", "credit": 0},
                                                                {"account_code": "1200", "debit": 0, "credit": "7.505"}]})
        c.ok(s == 400 and "2 decimal places" in str(err), f"£7.505 refused in words ({s} {str(err)[:120]})")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        s, err = api(page, "POST", "/transactions", {"date": iso_days(-3), "description": "اعشار ریال",
                                                      "lines": [{"account_code": "6112", "debit": "2500000.5", "credit": 0},
                                                                {"account_code": BANK, "debit": 0, "credit": "2500000.5"}]})
        c.ok(s == 400 and FA(str(err)) and "اعشار" in str(err), f"2,500,000.5 rials refused in Persian ({s} {str(err)[:120]})")
    finally:
        ctx.close()
    c.done()

    c = Check("Q7")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        s, ent = api(page, "POST", "/entities", {"type": "client", "name": f"Pennyworth {RUN}"})
        s, inv = api(page, "POST", "/invoices", {"number": f"PN-{RUN}", "kind": "sales", "status": "issued",
                                                  "issue_date": iso_days(-5), "due_date": iso_days(25), "amount": 0,
                                                  "entity_id": (ent or {}).get("id"),
                                                  "items": [{"product_name": "Widget", "quantity": 3, "unit_price": 19.99, "tax_rate": 20}]})
        c.ok(s == 201 and inv.get("grand_total") == 71.96 and inv.get("tax_total") == 11.99,
             f"3 × £19.99 + VAT = £71.96 ({s} total {(inv or {}).get('grand_total')} VAT {(inv or {}).get('tax_total')})")
        iid = (inv or {}).get("id")
        s1, _ = api(page, "POST", f"/invoices/{iid}/payments", {"amount": "30.00", "date": iso_days(-1)})
        s, invs = api(page, "GET", "/invoices", None)
        row = next((x for x in (invs or []) if x.get("id") == iid), {})
        c.ok(s1 == 201 and row.get("balance_due") == 41.96 and row.get("status") == "partially_paid",
             f"£30 paid leaves £41.96 ({s1} {row.get('balance_due')} {row.get('status')})")
        s2, _ = api(page, "POST", f"/invoices/{iid}/payments", {"amount": "41.96", "date": iso_days(-1)})
        s, invs = api(page, "GET", "/invoices", None)
        row = next((x for x in (invs or []) if x.get("id") == iid), {})
        c.ok(s2 == 201 and row.get("status") == "paid" and not row.get("balance_due"), f"£41.96 closes it ({row.get('status')} {row.get('balance_due')})")
        r = page.request.get(f"{BASE}/invoices/{iid}/pdf")
        c.ok(r.status == 200, f"its PDF ({r.status})")
        go(page, "invoices")
        settle(page)
        txt = page.inner_text('.card[data-page="invoices"]')
        c.ok("71.96" in txt, "the invoices list shows £71.96")
        c.shots.append(shot(page, "Q7", "invoices"))
    finally:
        ctx.close()
    c.done()


def s2_voucher_form_clears(browser):
    c = Check("S2")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        go(page, "transactions")
        page.fill("#date", iso_days(-2))
        page.fill("#reference", f"S2-{RUN}")
        page.fill("#description", f"Postage {RUN}")
        rows = page.locator("#lines-tbody tr")
        rows.nth(0).locator(".line-code").fill("8000")
        rows.nth(0).locator(".line-debit").fill("7.50")
        rows.nth(1).locator(".line-code").fill("1200")
        rows.nth(1).locator(".line-credit").fill("7.50")
        page.wait_for_timeout(300)
        before = page.inner_text("#voucher-balance-bar")
        page.click("#submit-btn")
        if confirm_shown(page):
            page.click("#ui-confirm-ok")
        m = alert_text(page)
        c.note(f"saved: «{m[:80]}»")
        page.wait_for_timeout(600)
        after = page.inner_text("#voucher-balance-bar")
        c.ok("7.50" in before, f"the totals bar showed £7.50 before saving «{before[:80]}»")
        c.ok("7.50" not in after, f"after saving the form's totals are 0 again «{after[:80]}»")
        c.shots.append(shot(page, "S2", "after-save", full=False))
    finally:
        ctx.close()
    c.done()


# ── T: stock in the books (Thames, UK) ───────────────────────────────────────────────────

def t_stock(browser):
    c1, c11, c15, c20 = Check("T1"), Check("T12"), Check("T15"), Check("T20")
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    INV = "/manager-reports/inventory"
    try:
        s, widget = api(page, "POST", f"{INV}/items", {"name": f"Widget {RUN}", "unit": "unit"})
        s2, gadget = api(page, "POST", f"{INV}/items", {"name": f"Gadget {RUN}", "unit": "unit"})
        c1.ok(s == 201 and s2 == 201, f"two stock items ({s}, {s2})")
        s, _ = api(page, "POST", f"{INV}/movements", {"item_id": widget["id"], "movement_type": "IN", "quantity": 20,
                                                      "unit_cost": 75, "movement_date": MONTH_START})
        c1.ok(s == 201, f"20 Widgets at £75 on hand ({s})")
        s, st = api(page, "PUT", f"{INV}/ledger", {"enabled": True, "from": MONTH_START})
        c1.ok(s == 200, f"stock posting on from {MONTH_START} ({s} {str(st)[:160]})")
        s, st = api(page, "GET", f"{INV}/ledger", None)
        c1.ok(st.get("enabled") and st.get("difference") == 0, f"books equal the valuation ({st.get('books_value')} / {st.get('valuation')})")
        s, bs = api(page, "GET", "/manager-reports/financial/uk/balance-sheet", None)
        stocks = next((r.get("amount_current") for r in (bs or {}).get("rows", []) if r.get("key") == "ca_stocks"), None)
        c1.ok(stocks is not None and float(stocks) >= 1500, f"the balance sheet's Stocks carry it ({stocks})")
        s, again = api(page, "POST", f"{INV}/ledger/sync", None)
        c1.ok(s == 200 and again.get("posted") == [], f"running the sync again posts nothing ({str(again.get('posted'))[:80]})")
        go(page, "inventory")
        settle(page)
        status = page.inner_text("#stock-ledger-status") if page.locator("#stock-ledger-status").count() else ""
        c1.ok("Posting since" in status, f"the Inventory page says it posts «{status[:140]}»")
        c1.shots.append(shot(page, "T1", "inventory"))
        ux(page, "T1", "inventory (UK)", lang="en", shot_name="T1-inventory.png")

        # T11/T12: buy 10 at £100 and 10 at £200, sell 15 → cost of sales £2,250 (weighted average)
        s, sup = api(page, "POST", "/entities", {"type": "supplier", "name": f"Gadgets Wholesale {RUN}"})
        s, cli = api(page, "POST", "/entities", {"type": "client", "name": f"Gadget Shop {RUN}"})
        for n, price in (("1", 100), ("2", 200)):
            s, bill = api(page, "POST", "/invoices", {"number": f"GB{n}-{RUN}", "kind": "purchase", "status": "issued",
                                                       "issue_date": iso_days(-2), "due_date": iso_days(28), "amount": 0, "currency": "GBP",
                                                       "entity_id": sup.get("id"),
                                                       "items": [{"product_name": f"Gadget {RUN}", "quantity": 10, "unit_price": price,
                                                                  "tax_rate": 0, "inventory_item_id": gadget["id"]}]})
            c11.ok(s == 201, f"bill {n}: 10 Gadgets at £{price} ({s} {str(bill)[:120]})")
        s, sale = api(page, "POST", "/invoices", {"number": f"GS-{RUN}", "kind": "sales", "status": "issued",
                                                   "issue_date": iso_days(-1), "due_date": iso_days(29), "amount": 0, "currency": "GBP",
                                                   "entity_id": cli.get("id"),
                                                   "items": [{"product_name": f"Gadget {RUN}", "quantity": 15, "unit_price": 300,
                                                              "tax_rate": 0, "inventory_item_id": gadget["id"]}]})
        c11.ok(s == 201, f"15 Gadgets sold at £300 ({s})")
        s, val = api(page, "GET", f"{INV}/valuation", None)
        g = next((r for r in (val or {}).get("rows", []) if r.get("item_id") == gadget["id"]), {})
        c11.ok(g.get("on_hand") == 5 and abs(float(g.get("value") or 0) - 750) < 0.01, f"5 Gadgets left worth £750 ({g.get('on_hand')} £{g.get('value')})")
        s, st = api(page, "GET", f"{INV}/ledger", None)
        c11.ok(st.get("difference") == 0, f"books equal the valuation after the sale ({st.get('books_value')} / {st.get('valuation')})")
        s, pl = api(page, "GET", "/manager-reports/financial/uk/profit-and-loss", None)
        cos = next((r.get("amount_current") for r in (pl or {}).get("rows", []) if r.get("key") == "cost_of_sales"), None)
        c11.note(f"P&L cost of sales {cos}")

        # T15: 5 come back on a credit note, at what they cost
        s, back = api(page, "GET", f"/invoices/{sale.get('id')}/stock-returns", None)
        line = next((ln for ln in (back or {}).get("lines", []) if ln.get("item_id") == gadget["id"]), {})
        c15.ok(line.get("moved") == 15 and line.get("left") == 15, f"the sale offers 15 to come back ({line})")
        s, cn = api(page, "POST", f"/invoices/{sale.get('id')}/credit-notes",
                     {"amount": 1500, "reason": "5 returned", "returns": [{"item_id": gadget["id"], "quantity": 5}]})
        c15.ok(s == 201, f"credit note with 5 back ({s} {str(cn)[:140]})")
        s, too_many = api(page, "POST", f"/invoices/{sale.get('id')}/credit-notes",
                          {"amount": 1000, "returns": [{"item_id": gadget["id"], "quantity": 11}]})
        c15.ok(s == 422 and "can come back" in str(too_many), f"11 more refused in words ({s} {str(too_many)[:120]})")
        s, st = api(page, "GET", f"{INV}/ledger", None)
        c15.ok(st.get("difference") == 0, f"books equal the valuation after the return ({st.get('books_value')} / {st.get('valuation')})")

        # T20: a count: 18 Widgets where 20 are on hand
        s, sheet = api(page, "GET", f"{INV}/count-sheet", None)
        w = next((r for r in (sheet or {}).get("rows", []) if r.get("item_id") == widget["id"]), {})
        c20.ok(w.get("on_hand") == 20, f"the sheet shows 20 Widgets on hand ({w.get('on_hand')})")
        s, counted = api(page, "POST", f"{INV}/counts", {"date": iso_days(0), "lines": [{"item_id": widget["id"], "counted": 18}]})
        adj = (counted or {}).get("adjusted", []) if isinstance(counted, dict) else []
        c20.ok(s == 201 and len(adj) == 1 and adj[0].get("difference") == -2, f"one adjustment of −2 ({s} {str(adj)[:140]})")
        s, again = api(page, "POST", f"{INV}/counts", {"date": iso_days(0), "lines": [{"item_id": widget["id"], "counted": 18}]})
        c20.ok(s == 201 and (again or {}).get("adjusted") == [], f"the same count again adjusts nothing ({str(again)[:100]})")
        s, st = api(page, "GET", f"{INV}/ledger", None)
        c20.ok(st.get("difference") == 0, f"books equal the valuation after the count ({st.get('books_value')} / {st.get('valuation')})")
        go(page, "inventory")
        settle(page)
        c20.shots.append(shot(page, "T20", "inventory-after"))
    finally:
        ctx.close()
    for c in (c1, c11, c15, c20):
        c.done()


# ── U: a party's transactions, newest first, searchable ──────────────────────────────────

def u_party_window(browser):
    c = Check("U1")
    u2 = Check("U2")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        name = f"مدرسه آزمون {RUN}"
        s, ent = api(page, "POST", "/entities", {"type": "client", "name": name})
        eid = (ent or {}).get("id")
        # after D6's lock (it closes the books to 1405/06/31)
        for i, (d, desc, amt) in enumerate(((iso_days(-6), "طراحي کاتالوگ", 48478500), (iso_days(-4), "شهریه مهر", 12000000),
                                             (iso_days(-2), "تنخواه پرداختی", 3000000))):
            s, body = api(page, "POST", "/transactions", {"date": d, "description": desc, "reference": f"IMPORT-sepidar-{RUN}-{i}",
                                                        "entity_links": [{"role": "client", "entity_id": eid}],
                                                        "lines": [{"account_code": "1112", "debit": amt, "credit": 0},
                                                                  {"account_code": "4110", "debit": 0, "credit": amt}]})
            c.ok(s == 201, f"entry {i + 1} ({s} {str(body)[:140] if s != 201 else ''})")
        go(page, "entities")
        settle(page)
        page.locator(f'.view-entity-txns[data-entity-id="{eid}"]').first.click()
        page.wait_for_selector("tr.entity-tx-row")
        rows = page.locator("tr.entity-tx-row")
        top = lat(rows.first.inner_text())
        c.ok(rows.count() == 3, f"three rows ({rows.count()})")
        c.ok(f"sepidar-{RUN}-2" in top, f"the newest entry on top «{top[:120]}»")
        c.ok("63,478,500" in lat(rows.first.locator("td.ent-tx-balance").inner_text()), "the top row's balance is today's")
        c.shots.append(shot(page, "U1", "window", full=False))
        box = page.locator("input.entity-tx-search")
        for q, want in (("طراحی", 1), ("۴۸٬۴۷۸٬۵۰۰", 1), ("48478500", 1), (f"sepidar-{RUN}-1", 1), ("zzz", 0)):
            box.fill(q)
            page.wait_for_timeout(250)
            shown = page.locator("tr.entity-tx-row:not([hidden])").count()
            u2.ok(shown == want, f"«{q}» → {shown} row(s), {want} expected")
        u2.ok(page.locator("tr.entity-tx-none").is_visible(), "«zzz» says no transaction matches")
        box.fill("طراحی")
        page.wait_for_timeout(250)
        count = page.inner_text("span.entity-tx-count")
        u2.ok(lat(count).strip() in ("1 of 3", "1 از 3"), f"the count beside the box «{count}»")
        u2.shots.append(shot(page, "U2", "search", full=False))
        box.fill("")
        page.wait_for_timeout(250)
        u2.ok(page.locator("tr.entity-tx-row:not([hidden])").count() == 3, "clearing the box shows every row again")
    finally:
        ctx.close()
    c.done()
    u2.done()


# ── X: parties from accounts ─────────────────────────────────────────────────────────────

def x_parties_from_accounts(browser):
    c1, c2 = Check("X1"), Check("X2")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        # a debtor's own sub-account under trade receivables (1112): an Iranian chart places an account by
        # its code, and a new ledger under 11 lands after 1150, prepayments, which no party is
        s2, school = api(page, "POST", "/accounts", {"name": f"دبیرستان آزمون {RUN}", "parent_code": "1112"})
        c1.ok(s2 == 201, f"a debtor sub-account under 1112 ({s2} {str(school)[:120] if s2 != 201 else ''})")
        code = (school or {}).get("code")
        s, body = api(page, "POST", "/transactions", {"date": iso_days(-2), "description": f"سهم اجاره {RUN}", "reference": f"X-{RUN}",
                                                    "lines": [{"account_code": code, "debit": 600000, "credit": 0},
                                                              {"account_code": BANK, "debit": 0, "credit": 600000}]})
        c1.ok(s == 201, f"an entry on it ({s} {str(body)[:140] if s != 201 else ''})")
        s, cands = api(page, "GET", "/entities/account-candidates", None)
        mine = [a for a in (cands or {}).get("accounts", []) if a.get("code") == code]
        c1.ok(len(mine) == 1 and mine[0].get("side") == "receivable", f"offered as a debtor ({mine})")
        c1.ok(not any(str(a.get("code", "")).startswith(("61", "41")) for a in (cands or {}).get("accounts", [])),
              "no expense or income account offered")
        go(page, "entities")
        settle(page)
        wait_until(page, "() => !document.getElementById('party-accounts').hidden")
        page.click("#party-accounts > summary")
        row = page.locator("#party-accounts-tbody tr", has_text=f"دبیرستان آزمون {RUN}")
        c1.ok(row.count() == 1, "listed on اشخاص")
        c1.shots.append(shot(page, "X1", "offered", selector="#party-accounts"))
        if row.count():
            row.locator(".pa-pick").check()
            row.locator(".pa-type").select_option("client")
            with page.expect_response(lambda r: "/entities/from-accounts" in r.url) as made:
                page.click("#pa-make")
            c2.ok(made.value.status == 201, f"made a party ({made.value.status})")
            party = page.locator("#entities-tbody tr", has_text=f"دبیرستان آزمون {RUN}")
            party.wait_for()
            c2.ok(code in lat(party.inner_text()), "the party carries its account's code")
            party.locator(".view-entity-txns").click()
            page.wait_for_selector("tr.entity-tx-row")
            first = page.locator("tr.entity-tx-row").first.inner_text()
            c2.ok(f"سهم اجاره {RUN}" in lat(first), "its account's entry is its transaction")
            c2.shots.append(shot(page, "X2", "party-txns", full=False))
        s, view = api(page, "GET", "/entities/account-candidates", None)
        c2.ok(not any(a.get("code") == code for a in (view or {}).get("accounts", [])), "no longer offered")
    finally:
        ctx.close()
    for c in (c1, c2):
        c.done()
    c6 = Check("X6")
    ctx, page, watch = new_session(browser, "arman_view", lang="fa")
    try:
        s, _ = api(page, "POST", "/entities/from-accounts", {"accounts": []})
        c6.ok(s == 403, f"a viewer may not make parties ({s})")
    finally:
        ctx.close()
    c6.done()


# ── Y: bank SMS from the phone ───────────────────────────────────────────────────────────

def y_bank_sms(browser):
    c1, c2, c3 = Check("Y1"), Check("Y2"), Check("Y3")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        mmdd = page.evaluate("""() => { const d = new Date(Date.now() - 864e5);
            const p = new Intl.DateTimeFormat('en-u-ca-persian', { month: '2-digit', day: '2-digit' }).formatToParts(d);
            return p.find(x => x.type === 'month').value + p.find(x => x.type === 'day').value; }""")
        when = (date.today() - timedelta(days=1)).isoformat() + "T20:00:00+03:30"
        acct = "9" + RUN + "56789"
        msgs = [{"sender": "98700717", "received_at": when,
                 "text": f"بانک ملت\nبرداشت:125,000\nحساب:{acct}\nمانده:1,234,567\n{mmdd}-13:45"},
                {"sender": "98700717", "received_at": when,
                 "text": f"بانک ملت\nواریز:65,433+\nحساب:{acct}\nمانده:1,300,000\n{mmdd}-19:10"}]
        tok = _phone(page, "arman_acc")
        c1.ok(bool(tok), "the phone signs in")
        s, r = _mobile(page, tok, "POST", "/bank-sms", {"messages": msgs})
        c1.ok(s == 200 and (r.get("received"), r.get("added"), r.get("duplicates"), r.get("unread")) == (2, 2, 0, 0),
              f"two rows to record ({s} {str(r)[:200]})")
        name = ((r.get("statements") or [{}])[0] or {}).get("bank_name") if isinstance(r, dict) else None
        c1.note(f"statement: {name}")
        s, again = _mobile(page, tok, "POST", "/bank-sms", {"messages": msgs})
        c1.ok(s == 200 and again.get("added") == 0 and again.get("duplicates") == 2, f"sent again, nothing added ({str(again)[:120]})")
        go(page, "bank-statements")
        settle(page)
        c1.ok("SMS" in page.inner_text('.card[data-page="bank-statements"]'), "the SMS statement is on the bank statements page")
        c1.shots.append(shot(page, "Y1", "statements"))
        s, otp = _mobile(page, tok, "POST", "/bank-sms", {"messages": [{"sender": "98700717", "received_at": when,
                                                                         "text": "بانک ملت\nرمز پویا: 482913\nاعتبار ۲ دقیقه"}]})
        c3.ok(s == 200 and otp.get("added") == 0 and otp.get("unread") == 1, f"a one-time code is not filed ({str(otp)[:120]})")
        s, many = _mobile(page, tok, "POST", "/bank-sms", {"messages": [msgs[0]] * 51})
        c3.ok(s == 422, f"51 at once refused ({s})")
    finally:
        ctx.close()
    for role in ("arman_emp", "arman_view", "arman_mgr"):
        ctx, page, watch = new_session(browser, role, lang="fa")
        try:
            tok = _phone(page, role)
            s, _ = _mobile(page, tok, "POST", "/bank-sms", {"messages": []}) if tok else (None, None)
            c2.ok(s == 403, f"{role} refused ({s})")
        finally:
            ctx.close()
    for c in (c1, c2, c3):
        c.done()


# ── Z3: a person's books choose their currency ───────────────────────────────────────────

def z3_books_currency(browser):
    c = Check("Z3")
    ctx, page, watch = new_session(browser, "qa_super", lang="en")
    user = f"qa_pz_{RUN}"
    try:
        s, co = api(page, "POST", "/admin/companies", {"name": f"Omani household {RUN}", "locale": "ir", "kind": "personal",
                                                        "base_currency": "IRR", "username": user, "password": PW})
        c.ok(s == 201, f"a new person's books ({s} {str(co)[:120]})")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, user, lang="en")
    try:
        go(page, "personal-dashboard")
        settle(page)
        c.ok(page.locator("#books-currency-panel").is_visible(), "the books' currency is offered on My finances")
        c.shots.append(shot(page, "Z3", "panel", selector="#books-currency-panel"))
        page.select_option("#bc-select", "OMR")
        page.click("#bc-save")
        if confirm_shown(page):
            page.click("#ui-confirm-ok")
        m = alert_text(page)
        c.ok("Omani rial" in m, f"«{m}»")
        s, cur = api(page, "GET", "/personal/currency", None)
        c.ok(cur.get("currency") == "OMR" and cur.get("locked") is False, f"counts in OMR, still open ({cur.get('currency')} {cur.get('locked')})")
        s, accts = api(page, "GET", "/accounts", None)
        codes = [str(a.get("code")) for a in (accts or []) if isinstance(a, dict)]
        cash = next((x for x in codes if x.startswith("111") and len(x) == 4), "1110")
        spend = next((x for x in codes if x[:1] in "68" and len(x) == 4), None) or next((x for x in codes if x.startswith("5") and len(x) == 4), "")
        s, t = api(page, "POST", "/transactions", {"date": iso_days(-1), "description": "lunch", "lines": [
            {"account_code": spend, "debit": "1.250", "credit": 0}, {"account_code": cash, "debit": 0, "credit": "1.250"}]})
        c.note(f"first entry → {s}")
        if s == 201:
            s, cur = api(page, "GET", "/personal/currency", None)
            c.ok(cur.get("locked") is True, "with an entry the currency stays")
            s, refused = api(page, "PUT", "/personal/currency", {"currency": "IRR"})
            c.ok(s == 422 and "already have entries" in str(refused), f"a change is refused ({s} {str(refused)[:100]})")
            go(page, "personal-dashboard")
            settle(page)
            c.ok(not page.locator("#bc-save").is_visible(), "no Save once it is fixed")
    finally:
        ctx.close()
    c.done()


# ── AA: what changed on the balance sheet ────────────────────────────────────────────────

def aa_balance_change(browser):
    c1, c2, c6, c7, c8 = Check("AA1"), Check("AA2"), Check("AA6"), Check("AA7"), Check("AA8")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        prior = (date.today() - timedelta(days=60)).isoformat()
        go(page, "manager")
        page.select_option("#mgr-report-type", "balance_sheet")
        page.fill("#mgr-from-date", prior)
        page.fill("#mgr-to-date", iso_days(0))
        page.click("#mgr-run-btn")
        settle(page, 800)
        page.evaluate("() => openBalanceSection(lastManagerReport, 0)")
        settle(page, 600)
        modal = page.locator("#chart-drilldown-modal")
        c1.ok(modal.is_visible(), "clicking assets opens what changed")
        summ = page.inner_text("p.bsc-summary") if page.locator("p.bsc-summary").count() else ""
        c1.ok(FA(summ) and ("→" in summ or (" از " in summ and " به " in summ)), f"from the earlier figure to the later «{summ[:160]}»")
        lines = page.locator("table.bsc-table tr[data-bs-line]")
        c1.ok(lines.count() >= 1, f"the lines that moved ({lines.count()})")
        c1.shots.append(shot(page, "AA1", "assets", full=False))
        if lines.count():
            key = lines.first.get_attribute("data-bs-line")
            lines.first.click()
            settle(page, 600)
            accts = page.locator("table.bsc-table tr[data-code]")
            c2.ok(accts.count() >= 1, f"the accounts behind «{key}» ({accts.count()})")
            c2.shots.append(shot(page, "AA2", "line", full=False))
            s, drill = api(page, "GET", f"/manager-reports/financial/iran/balance-sheet/lines/{key}?as_of={iso_days(0)}&comparative_as_of={prior}", None)
            total = sum(float(a.get("change") or 0) for a in (drill or {}).get("accounts", []))
            c2.ok(s == 200 and abs(total - float(((drill or {}).get("total") or {}).get("change") or 0)) < 1,
                  f"the accounts add up to the line ({total} vs {((drill or {}).get('total') or {}).get('change')})")
        page.keyboard.press("Escape")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        prior = (date.today() - timedelta(days=30)).isoformat()
        go(page, "manager")
        page.select_option("#mgr-report-type", "balance_sheet")
        page.fill("#mgr-from-date", prior)
        page.fill("#mgr-to-date", iso_days(-1))
        with page.expect_response(lambda r: "/balance-sheet" in r.url and "/lines/" not in r.url) as res:
            page.click("#mgr-run-btn")
        url = res.value.url
        body = res.value.json() if res.value.ok else {}
        c7.note(f"asked {url.split('?')[-1][:160]}")
        got = str(body.get("as_of") or body.get("to_date") or "")
        c7.ok(got == iso_days(-1), f"a UK balance sheet as of the date chosen ({got} for {iso_days(-1)})")
        settle(page, 600)
        bar, want = page.evaluate("""() => { const ch = Chart.getChart(document.getElementById('mgr-report-chart'));
            const rows = (lastManagerReport && lastManagerReport.rows) || [];
            const amt = (k) => Math.abs(Number((rows.find(r => r.key === k) || {}).amount_current || 0));
            return [ch ? Number(ch.data.datasets[0].data[1]) : null, amt('creditors_within_one_year') + amt('creditors_after_one_year')]; }""")
        c8.ok(bar is not None and abs(bar - want) < 0.01 and want > 0, f"the Liabilities bar is both kinds of creditor ({bar} for {want})")
        page.evaluate("() => openBalanceSection(lastManagerReport, 0)")
        settle(page, 600)
        c6.ok(page.locator("#chart-drilldown-modal").is_visible(), "what changed opens in English")
        c6.shots.append(shot(page, "AA6", "uk", full=False))
    finally:
        ctx.close()
    for c in (c1, c2, c6, c7, c8):
        c.done()


# ── AE: a voucher the phone made, seen and confirmed on the web; then AC on the web ──────

# amounts of this run's own: rows the earlier groups recorded (E2, C12) would match and leave no difference
MELLAT = "تاریخ,شرح,برداشت,واریز,مانده\n1405/07/12,خرید تونر {run},{w},,100000000\n1405/07/13,واریز نقدی {run},,{d},{b}\n"


def ae_cards_on_the_web(browser):
    c1, c2, c3 = Check("AE1"), Check("AE2"), Check("AE3")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        tok = _phone(page, "arman_acc")
        r = page.request.post(f"{BASE}/api/mobile/v1/uploads", headers={"Authorization": f"Bearer {tok}"},
                              multipart={"file": {"name": f"mellat-{RUN}.csv", "mimeType": "text/csv",
                                                  "buffer": MELLAT.format(run=RUN, w=1_800_000 + int(RUN) * 1000,
                                                                          d=5_000_000 + int(RUN) * 1000,
                                                                          b=105_000_000 + int(RUN) * 1000).encode()}})
        up = r.json() if r.ok else {}
        c1.ok(r.status == 201, f"a statement sent from the phone ({r.status})")
        s, reply = _mobile(page, tok, "POST", "/chat", {"message": "", "attachment_ids": [up.get("id")], "thread_id": None})
        intake = next((b for b in (reply or {}).get("blocks", []) if b.get("type") == "intake"), {}) if isinstance(reply, dict) else {}
        thread, sid = (reply or {}).get("thread_id"), intake.get("statement_id")
        c1.ok(s == 200 and sid, f"read as a statement ({s} {intake.get('status')} rows {intake.get('total_rows')})")
        s, nxt = _mobile(page, tok, "POST", f"/statements/{sid}/next", {"thread_id": thread})
        card = next((b for b in (nxt or {}).get("blocks", []) if b.get("type") == "proposal"), {}) if isinstance(nxt, dict) else {}
        said = [b.get("text") for b in (nxt or {}).get("blocks", []) if b.get("type") == "text"] if isinstance(nxt, dict) else []
        c1.ok(s == 200 and card.get("token"), f"the phone's next difference is a voucher ({s} {card.get('title')} {said})")
        api(page, "PATCH", f"/ai-accountant/sessions/{thread}", {"title": f"QA-AE-{RUN}"})
        _save(ae_thread=thread, ae_token=card.get("token"))

        def open_thread():
            go(page, "ai-accountant")
            settle(page, 600)
            item = page.locator(".sess-item", has_text=f"QA-AE-{RUN}").first
            item.click()
            settle(page, 800)
            return page.locator(f'.ai-card[data-token="{card.get("token")}"]')

        web = open_thread()
        c1.ok(web.count() == 1, "the phone's voucher is a card on the web")
        c3.ok(web.locator(".ai-voucher-line").count() == 2 and web.locator(".ai-card-summary").count() == 0,
              "drawn as the card itself, two lines, no summary")
        sides = " ".join(web.locator(".ai-voucher-side").all_inner_texts())
        c3.ok("بد" in sides and "بس" in sides, f"a company's lines say بد and بس «{sides}»")
        c1.shots.append(shot(page, "AE1", "pending", selector="#ai-acct-messages"))
        ux(page, "AE1", "ai-accountant (a phone thread)", lang="fa", shot_name="AE1-chat.png")
        btn = web.locator(".ai-card-actions button.btn-primary")
        c1.ok(btn.is_enabled(), "still pending: Confirm works")
        with page.expect_response(lambda r: "/ai-accountant/execute" in r.url) as done:
            btn.click()
        c1.ok(done.value.status == 200, f"confirmed from the web ({done.value.status})")
        page.reload()
        page.wait_for_load_state("networkidle")
        web = open_thread()
        note = web.locator(".ai-card-posted")
        c2.ok(note.count() == 1 and "ثبت شد" in note.inner_text(), f"opened again: posted «{note.inner_text() if note.count() else ''}»")
        c2.ok(web.locator(".ai-card-actions").count() == 0, "no buttons on a posted card")
        c2.shots.append(shot(page, "AE2", "posted", selector="#ai-acct-messages"))
        # the phone sees it posted too
        s, hist = _mobile(page, tok, "GET", f"/threads/{thread}/messages")
        blocks = [b for m in (hist or []) if isinstance(m, dict) for b in m.get("blocks") or []]
        stamp = next((b for b in blocks if b.get("token") == card.get("token")), {})
        c2.ok(stamp.get("state") == "executed", f"the phone's history says posted too ({stamp.get('state')})")
    finally:
        ctx.close()
    for c in (c1, c2, c3):
        c.done()


def ac_reply_copy_share(browser):
    c1, c3, c4 = Check("AC1"), Check("AC3"), Check("AC4")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    try:
        go(page, "ai-accountant")
        settle(page, 600)
        page.click("#ai-acct-new-session") if page.locator("#ai-acct-new-session").is_visible() else None
        page.fill("#ai-acct-input", "موجودی چقدره؟")
        page.click("#ai-acct-send")
        said = page.locator("#ai-acct-messages .msg-row.assistant").last
        said.wait_for()
        wait_until(page, "() => [...document.querySelectorAll('#ai-acct-messages .msg-row.assistant')].pop().innerText.includes('موجودی')")
        words = said.locator(".msg").inner_text().strip()
        said.hover()
        said.get_by_role("button", name="کپی").click()
        page.wait_for_timeout(300)
        clip = page.evaluate("() => navigator.clipboard.readText()")
        c1.ok(clip.strip() == words, f"copied as it reads «{clip[:80]}»")
        c1.ok(said.locator(".msg-actions").get_attribute("data-note") == "کپی شد", "says کپی شد")
        said.get_by_role("button", name="ارسال").click()
        links = page.locator(".msg-share-menu a")
        c3.ok(links.count() == 3, f"no share sheet here: Telegram, WhatsApp, e-mail ({links.count()})")
        c3.shots.append(shot(page, "AC3", "share", selector="#ai-acct-messages"))
        page.locator("#ai-acct-input").click()
        said.hover()
        said.get_by_role("button", name="پاسخ").click()
        strip = page.locator(".ai-reply-strip")
        c4.ok(strip.is_visible() and "در پاسخ به دستیار" in strip.inner_text(), "replying to the assistant, above the box")
        page.fill("#ai-acct-input", "و به دلار؟")
        c4.shots.append(shot(page, "AC4", "strip", full=False))
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
            page.click("#ai-acct-send")
        c4.note(f"the reply → {res.value.status} (no AI on the QA server)")
        mine = page.locator("#ai-acct-messages .msg-row.user").last
        c4.ok(mine.locator(".msg-quote").count() == 1 and "موجودی" in mine.locator(".msg-quote").inner_text(), "the message shows its quote")
        c4.ok(not strip.is_visible(), "the strip goes once it is sent")
        s, sessions = api(page, "GET", "/ai-accountant/sessions", None)
        sid = (sessions or [{}])[0].get("id")
        s, msgs = api(page, "GET", f"/ai-accountant/sessions/{sid}/messages", None)
        kept = [m["content"] for m in (msgs or []) if m.get("role") == "user" and (m.get("content") or {}).get("reply_to")]
        c4.ok(kept and kept[-1]["said"] == "و به دلار؟" and "موجودی" in kept[-1]["reply_to"]["text"], f"kept with its quote ({str(kept)[:160]})")
        c4.shots.append(shot(page, "AC4", "sent", selector="#ai-acct-messages"))
    finally:
        ctx.close()
    for c in (c1, c3, c4):
        c.done()


def iso_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (q_pence, s2_voucher_form_clears, t_stock, u_party_window, x_parties_from_accounts, y_bank_sms,
                   z3_books_currency, aa_balance_change, ae_cards_on_the_web, ac_reply_copy_share):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:500])
        b.close()
