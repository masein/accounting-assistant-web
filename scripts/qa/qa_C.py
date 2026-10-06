"""Group C (part 1) — daily bookkeeping for Arman, in Persian (SCENARIOS.md C1–C11)."""
import json
import re
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import OUT, Check, alert_text, api, clear_alert, go, iso, new_session, shot, ux, wait_until, confirm_shown

FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))
STATE = f"{OUT}/state.json"
st = json.load(open(STATE))
P = st["parties"]
BANK, RENT, AR, SALES = st["bank_code"], "6112", "1112", "4110"
import random
RUN = str(random.randint(100, 999))      # numbers stay unique when the group is run again


def save_state(**kw):
    s = json.load(open(STATE)); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False, indent=1)


def inv_get(page, iid):
    s, lst = api(page, "GET", "/invoices", None)
    rows = lst if isinstance(lst, list) else (lst or {}).get("items", []) if isinstance(lst, dict) else []
    return next((r for r in rows if r.get("id") == iid), {})


def cm_get(page, cid):
    s, lst = api(page, "GET", "/commitments", None)
    return next((r for r in (lst or []) if r.get("id") == cid), {})


def type_number(page, sel, text):
    page.locator(sel).fill("")
    page.locator(sel).press_sequentially(text)


def c1_voucher(browser):
    c = Check("C1")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "transactions")
        # the date from the Jalali grid: 7 Mehr 1405 = 29 Sep 2026
        page.click("#date >> xpath=.. >> .jdate-btn")
        c.ok(page.locator("#jdate-pop").is_visible(), "Jalali grid opens")
        page.locator('#jdate-pop .jdate-day[data-iso="2026-09-29"]').click()
        c.ok(page.input_value("#date") == "2026-09-29", f"picked 7 Mehr → {page.input_value('#date')}")
        page.fill("#reference", "RNT-" + RUN)
        page.fill("#description", "اجاره دفتر مهر ۱۴۰۵")
        rows = page.locator("#lines-tbody tr")
        rows.nth(0).locator(".line-code").fill(RENT)
        type_number(page, "#lines-tbody tr >> nth=0 >> .line-debit", "۴۵۰۰۰۰۰۰")
        rows.nth(0).locator(".line-desc").fill("اجاره مهر")
        rows.nth(1).locator(".line-code").fill(BANK)
        type_number(page, "#lines-tbody tr >> nth=1 >> .line-credit", "۴۰۰۰۰۰۰۰")       # unbalanced on purpose
        bar = page.inner_text("#voucher-balance-bar")
        c.ok(FA(bar) and ("5,000,000" in bar or "۵٬۰۰۰٬۰۰۰" in bar), f"balance bar shows the 5,000,000 difference «{bar}»")
        page.click("#submit-btn")
        msg = alert_text(page)
        c.ok(FA(msg), f"unbalanced refused in Persian «{msg}»")
        clear_alert(page)
        type_number(page, "#lines-tbody tr >> nth=1 >> .line-credit", "۴۵۰۰۰۰۰۰")
        if page.locator("#entity-supplier option").count() > 1:
            page.select_option("#entity-supplier", P["پخش البرز"]) if False else None
        page.set_input_files("#attachment-input", f"{OUT}/logo.png")
        page.click("#attachment-upload-btn")
        m = alert_text(page)
        c.ok(FA(m), f"receipt upload message Persian «{m}»")
        clear_alert(page)
        c.shots.append(shot(page, "C1", "voucher-filled"))
        page.click("#submit-btn")
        c.ok(confirm_shown(page), "saving asks to confirm (app dialog)")
        body = page.inner_text("#ui-confirm-message")
        c.shots.append(shot(page, "C1", "confirm", full=False))
        c.ok("Date:" not in body and "Debit entries" not in body, f"the confirmation is in Persian «{body[:120]}»")
        c.ok("1405/07/07" in body, "the confirmation shows the Jalali date")
        with page.expect_response(lambda r: r.url.rstrip("/").endswith("/transactions") and r.request.method == "POST") as res:
            page.click("#ui-confirm-ok")
        c.ok(res.value.status in (200, 201), f"voucher saved ({res.value.status} {res.value.text()[:150]})")
        m = alert_text(page)
        c.ok(FA(m), f"saved message Persian «{m}»")
        tx = res.value.json() if res.value.status in (200, 201) else {}
        c.ok(tx.get("date") == "2026-09-29", f"stored date {tx.get('date')}")
        s, full = api(page, "GET", f"/transactions/{tx.get('id')}", None)
        c.ok(s == 200 and len((full or {}).get("attachments") or []) == 1, f"one attachment on it ({len((full or {}).get('attachments') or []) if s == 200 else s})")
        save_state(rent_tx=tx.get("id"))
        go(page, "ledger")
        page.wait_for_timeout(800)
        txt = page.inner_text('.card[data-page="ledger"]')
        pass
        c.note("ledger page is the account summary; the journal is checked under D1")
        c.shots.append(shot(page, "C1", "ledger"))
        ux(page, "C1", "ledger", lang="fa", shot_name="C1-ledger.png")
        c.ok(watch.problems() == [], f"problems {watch.problems()}")
    finally:
        ctx.close()
    c.done()


def _create_invoice(page, c, *, kind, number, entity, issue, lines=None, amount=None, quote=False):
    go(page, "invoices")
    page.select_option("#inv-kind", kind)
    page.fill("#inv-number", number)
    page.fill("#inv-issue", issue)
    page.locator("#inv-issue").dispatch_event("change")
    with page.expect_response(lambda r: f"/entities/{entity}" in r.url):
        page.select_option("#inv-entity", entity)
    page.wait_for_timeout(300)
    if lines:
        page.click("#inv-mode-itemized")
        body = page.locator("#inv-items-body .inv-line")
        while body.count() < len(lines):
            page.click("#inv-add-line")
        for i, (desc, qty, price, code) in enumerate(lines):
            row = body.nth(i)
            row.locator(".il-desc").fill(desc)
            row.locator(".il-qty").fill(str(qty))
            row.locator(".il-price").fill("")
            row.locator(".il-price").press_sequentially(price)
            if code:
                opts = row.locator(".il-code option").all_text_contents()
                pick = next((o for o in opts if code in o), None)
                c.ok(pick is not None, f"tax code {code} offered ({opts[:6]})")
                if pick:
                    row.locator(".il-code").select_option(label=pick)
        page.wait_for_timeout(300)
    else:
        page.click("#inv-mode-simple")
        page.fill("#inv-amount", str(amount))
    btn = "#inv-save-quote" if quote else "#inv-add"
    with page.expect_response(lambda r: ("/quotes" if quote else "/invoices") in r.url and r.request.method == "POST") as res:
        page.click(btn)
    return res.value


def c3_c4_sales_invoice(browser):
    c = Check("C3")
    c4 = Check("C4")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "invoices")
        res = _create_invoice(page, c, kind="sales", number="ARM-1" + RUN, entity=P["فروشگاه مهرگان"], issue="2026-10-04",
                              lines=[("کاغذ A4", 10, "۳۰۰۰۰۰", "IR_VAT_STANDARD"), ("خدمات طراحی", 5, "2000000", "IR_VAT_STANDARD")])
        due = page.input_value("#inv-due")
        c.note(f"due after picking the Net 30 client: {due}")
        c.ok(res.status in (200, 201), f"invoice created ({res.status} {res.text()[:200]})")
        inv = res.json() if res.status in (200, 201) else {}
        c.ok(inv.get("due_date") == "2026-11-03", f"due = issue + 30 days ({inv.get('due_date')})")
        sub = sum(l.get("line_net", l.get("quantity", 0) * l.get("unit_price", 0)) for l in inv.get("items", []) or []) if inv else 0
        c.note(f"amount {inv.get('amount')} subtotal {inv.get('subtotal')} tax {inv.get('tax_total')}")
        c.ok(inv.get("subtotal") in (13_000_000, None) or sub == 13_000_000, f"subtotal 13,000,000 ({inv.get('subtotal')})")
        vat = inv.get("tax_total")
        c.ok(vat == 1_300_000, f"VAT at 10% (Iran, since 1403) = 1,300,000 — got {vat}")
        m = alert_text(page)
        c.ok(FA(m), f"created message Persian «{m}»")
        clear_alert(page)
        c.shots.append(shot(page, "C3", "invoices-list"))
        ux(page, "C3", "invoices", lang="fa", shot_name="C3-invoices-list.png")
        r = page.request.get(f"{page.url.split('/#')[0]}/invoices/{inv.get('id')}/pdf")
        c.ok(r.status == 200 and r.headers.get("content-type", "").startswith("application/pdf"), f"PDF ({r.status})")
        open(f"{OUT}/C3-invoice.pdf", "wb").write(r.body())
        save_state(inv1=inv.get("id"), inv1_total=inv.get("amount"))
        # C4 — payments through the app's dialog, Persian digits
        btn = page.locator(f'.inv-payment[data-id="{inv.get("id")}"]')
        btn.click()
        c4.ok(page.locator("#ui-prompt-modal").is_visible(), "payment asks in the app's dialog")
        lbl = page.inner_text("#ui-prompt-label")
        c4.ok(FA(lbl), f"prompt Persian «{lbl}»")
        page.fill("#ui-prompt-input", "")
        page.locator("#ui-prompt-input").press_sequentially("۵۰۰۰۰۰۰")
        with page.expect_response(lambda r: "/payments" in r.url and r.request.method == "POST") as res:
            page.click("#ui-prompt-ok")
        c4.ok(res.value.status in (200, 201), f"partial payment ({res.value.status} {res.value.text()[:150]})")
        page.wait_for_timeout(800)
        i2 = inv_get(page, inv.get("id"))
        c4.ok((i2 or {}).get("status") == "partially_paid", f"status partially_paid ({(i2 or {}).get('status')})")
        rest = (inv.get("amount") or 0) - 5_000_000
        page.locator(f'.inv-payment[data-id="{inv.get("id")}"]').click()
        page.fill("#ui-prompt-input", str(rest))
        with page.expect_response(lambda r: "/payments" in r.url and r.request.method == "POST") as res:
            page.click("#ui-prompt-ok")
        page.wait_for_timeout(800)
        i3 = inv_get(page, inv.get("id"))
        c4.ok((i3 or {}).get("status") == "paid", f"status paid ({(i3 or {}).get('status')})")
        row = page.inner_text("#invoices-tbody") if page.locator("#invoices-tbody").count() else ""
        c4.ok(FA(row) and "paid" not in row, "status shown in Persian in the list")
        c4.shots.append(shot(page, "C4", "paid"))
        c.ok(watch.problems() == [], f"problems {watch.problems()}")
    finally:
        ctx.close()
    c.done(); c4.done()


def c5_bill(browser):
    c = Check("C5")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        res = _create_invoice(page, c, kind="purchase", number="ALB-5" + RUN, entity=P["پخش البرز"], issue="2026-10-02", amount=20_000_000)
        c.ok(res.status in (200, 201), f"bill created ({res.status} {res.text()[:150]})")
        bill = res.json() if res.status in (200, 201) else {}
        clear_alert(page)
        page.locator(f'.inv-payment[data-id="{bill.get("id")}"]').click()
        page.fill("#ui-prompt-input", "20000000")
        with page.expect_response(lambda r: "/payments" in r.url) as r2:
            page.click("#ui-prompt-ok")
        c.ok(r2.value.status in (200, 201), f"bill paid ({r2.value.status})")
        page.wait_for_timeout(600)
        b2 = inv_get(page, bill.get("id"))
        c.ok((b2 or {}).get("status") == "paid", f"bill status paid ({(b2 or {}).get('status')})")
        c.shots.append(shot(page, "C5", "bill-paid"))
    finally:
        ctx.close()
    c.done()


def c6_c7_quote_credit_void(browser):
    c6, c7 = Check("C6"), Check("C7")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        res = _create_invoice(page, c6, kind="sales", number="Q-2" + RUN, entity=P["شرکت پارس‌افزار"], issue="2026-10-05",
                              lines=[("کارتریج لیزری", 4, "6500000", "IR_VAT_STANDARD")], quote=True)
        c6.ok(res.status in (200, 201), f"quote saved ({res.status} {res.text()[:150]})")
        q = res.json() if res.status in (200, 201) else {}
        clear_alert(page)
        page.wait_for_timeout(700)
        for status_btn in ('data-status="sent"', 'data-status="accepted"'):
            b = page.locator(f'.qt-set[data-id="{q.get("id")}"][{status_btn}]')
            if b.count():
                b.click(); page.wait_for_timeout(600)
                open_modal = page.evaluate("() => [...document.querySelectorAll('.modal, [id$=-modal]')].filter(m => m.offsetParent !== null || getComputedStyle(m).display === 'flex').map(m => m.id)")
                if open_modal:
                    c6.note(f"after {status_btn}: dialog {open_modal}")
                    c6.shots.append(shot(page, "C6", "dialog-" + status_btn.split('"')[1], full=False))
                    if confirm_shown(page):
                        page.click("#ui-confirm-ok"); page.wait_for_timeout(600)
            else:
                c6.ok(False, f"no quote button {status_btn}")
        conv = page.locator(f'.qt-convert[data-id="{q.get("id")}"]')
        if conv.count():
            conv.click()
            page.wait_for_timeout(300)
            if page.locator("#ui-prompt-modal").is_visible():       # asks for the invoice's number
                c6.ok(FA(page.inner_text("#ui-prompt-label")), "convert asks in Persian")
                page.click("#ui-prompt-ok")
            elif confirm_shown(page):
                page.click("#ui-confirm-ok")
            page.wait_for_timeout(1000)
            s, ql = api(page, "GET", "/quotes", None)
            q2 = next((x for x in (ql if isinstance(ql, list) else (ql or {}).get("items", [])) if x.get("id") == q.get("id")), {})
            c6.ok((q2 or {}).get("status") == "converted", f"quote converted ({(q2 or {}).get('status')})")
            c6.ok(bool((q2 or {}).get("converted_invoice_id")), f"it names its invoice ({(q2 or {}).get('converted_invoice_number')})")
        else:
            c6.ok(False, "no convert button")
        c6.shots.append(shot(page, "C6", "quotes"))
        # C7 — a credit note on the first invoice, a void on another
        inv1 = json.load(open(STATE)).get("inv1")
        res2 = _create_invoice(page, c7, kind="sales", number="ARM-2" + RUN, entity=P["كافه نارنج"], issue="2026-10-06", amount=8_000_000)
        inv2 = res2.json() if res2.status in (200, 201) else {}
        clear_alert(page)
        # a paid invoice takes a credit note too (#280; C23 follows it through)
        c7.ok(page.locator(f'.inv-credit-note[data-id="{inv1}"]').is_enabled(), "credit note offered on a fully paid invoice (#280)")
        res3 = _create_invoice(page, c7, kind="sales", number="ARM-3" + RUN, entity=P["شرکت پارس‌افزار"], issue="2026-10-06", amount=5_000_000)
        inv3 = res3.json() if res3.status in (200, 201) else {}
        clear_alert(page)
        page.locator(f'.inv-credit-note[data-id="{inv2.get("id")}"]').locator('xpath=ancestor::details[1]/summary').click(); page.locator(f'.inv-credit-note[data-id="{inv2.get("id")}"]').click()  # under ⋯ (#277)
        if page.locator("#ui-prompt-modal").is_visible():
            page.fill("#ui-prompt-input", "1000000"); page.click("#ui-prompt-ok"); page.wait_for_timeout(400)
            if page.locator("#ui-prompt-modal").is_visible():
                page.fill("#ui-prompt-input", "برگشت بخشی از کالا"); page.click("#ui-prompt-ok")
        page.wait_for_timeout(900)
        m = alert_text(page, 3000)
        c7.note(f"credit note: «{m}»")
        c7.ok(FA(m), "credit note message Persian")
        clear_alert(page)
        page.locator(f'.inv-void[data-id="{inv3.get("id")}"]').locator('xpath=ancestor::details[1]/summary').click(); page.locator(f'.inv-void[data-id="{inv3.get("id")}"]').click()  # under ⋯ (#277)
        if confirm_shown(page):
            page.click("#ui-confirm-ok")
        elif page.locator("#ui-prompt-modal").is_visible():
            page.fill("#ui-prompt-input", "صدور اشتباه"); page.click("#ui-prompt-ok")
        page.wait_for_timeout(900)
        v = inv_get(page, inv3.get("id"))
        cn = inv_get(page, inv2.get("id"))
        c7.note(f"after the credit note: {cn.get('status')} balance {cn.get('balance_due', cn.get('balance'))}")
        c7.ok(cn.get("status") == "issued" and cn.get("balance_due") == 7_000_000,
              f"a credit note is not a payment: issued, 7,000,000 owed ({cn.get('status')}, {cn.get('balance_due')})")
        c7.ok((v or {}).get("status") == "voided", f"voided ({(v or {}).get('status')})")
        rows = page.inner_text("#invoices-tbody")
        c7.ok("voided" not in rows and "partially_paid" not in rows, "statuses in Persian")
        c7.shots.append(shot(page, "C7", "after"))
    finally:
        ctx.close()
    c6.done(); c7.done()


def _digits(text):
    return "".join(ch for ch in (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")) if ch.isdigit())


def _row_action(page, sel, iid):
    """A row action under its ⋯ menu (#277), or on the row itself."""
    btn = page.locator(f'{sel}[data-id="{iid}"]').first
    menu = btn.locator("xpath=ancestor::details[1]")
    if menu.count() and menu.get_attribute("open") is None:
        menu.locator("summary").click()
    btn.click()


def c23_credit_paid(browser):
    """C23 — a credit note on a paid invoice (#280): kept as the customer's credit,
    used on their next invoice, the rest refunded; a fully credited invoice reads
    «برگشت‌شده»; more than is left is refused; the VAT goes back once."""
    c = Check("C23")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "invoices")
        inv1 = json.load(open(STATE)).get("inv1")
        before = inv_get(page, inv1)
        c.note(f"ARM-1: {before.get('status')} total {before.get('amount')} VAT {before.get('tax_total')}")
        c.ok(before.get("status") == "paid", f"ARM-1 is paid ({before.get('status')})")
        # 1. a credit note on the paid invoice, kept as the customer's credit
        _row_action(page, ".inv-credit-note", inv1)
        page.fill("#ui-prompt-input", ""); page.locator("#ui-prompt-input").press_sequentially("۲۲۰۰۰۰۰")
        page.click("#ui-prompt-ok"); page.wait_for_timeout(300)
        page.fill("#ui-prompt-input", "مرجوعی دو بسته کاغذ"); page.click("#ui-prompt-ok")
        c.ok(confirm_shown(page), "asks: refund now, or keep as the customer's credit")
        ask = page.inner_text("#ui-confirm-message")
        keep, now = page.inner_text("#ui-confirm-cancel"), page.inner_text("#ui-confirm-ok")
        c.ok(FA(ask) and "2200000" in _digits(ask) and FA(keep) and FA(now), f"in Persian, naming 2,200,000: «{ask}» [{now} | {keep}]")
        c.shots.append(shot(page, "C23", "refund-or-keep", full=False))
        with page.expect_response(lambda r: r.url.endswith("/credit-notes") and r.request.method == "POST") as res:
            page.click("#ui-confirm-cancel")
        c.ok(res.value.status == 201, f"credit note recorded ({res.value.status} {res.value.text()[:150]})")
        note = res.value.json() if res.value.status == 201 else {}
        m = alert_text(page); clear_alert(page)
        c.ok(FA(m), f"message Persian «{m}»")
        a = inv_get(page, inv1)
        c.ok(a.get("status") == "paid" and a.get("credited") == 2_200_000 and a.get("credit_available") == 2_200_000,
             f"still paid, 2,200,000 credited and held as credit ({a.get('status')}, {a.get('credited')}, {a.get('credit_available')})")
        s, txn = api(page, "GET", f"/transactions/{note.get('transaction_id')}", None)
        vat_back = sum(ln.get("debit", 0) for ln in (txn or {}).get("lines", []) if ln.get("account_code") == "2130")
        want = round((before.get("tax_total") or 0) * 2_200_000 / max(1, before.get("amount") or 1))
        c.ok(want == 200_000 and vat_back == want, f"output VAT 2130 back by its share, once: {vat_back} (want {want})")
        row = page.inner_text(f'#invoices-tbody tr:has([data-id="{inv1}"])')
        c.ok(FA(row) and "2200000" in _digits(row), f"the row shows the credit «{' '.join(row.split())[:160]}»")
        c.shots.append(shot(page, "C23", "credit-kept"))
        # 2. the credit pays the customer's next invoice, no money moving
        res = _create_invoice(page, c, kind="sales", number="ARM-23" + RUN, entity=P["فروشگاه مهرگان"], issue="2026-10-07", amount=1_500_000)
        nxt = res.json() if res.status in (200, 201) else {}
        clear_alert(page); page.wait_for_timeout(600)
        page.locator(f'.inv-payment[data-id="{nxt.get("id")}"]').click()
        c.ok(confirm_shown(page), "the payment offers the customer's credit first")
        offer = page.inner_text("#ui-confirm-message")
        c.ok(FA(offer) and "1500000" in _digits(offer), f"«{offer}»")
        with page.expect_response(lambda r: "/apply-credit" in r.url) as res:
            page.click("#ui-confirm-ok")
        c.ok(res.value.status in (200, 201), f"credit used ({res.value.status} {res.value.text()[:150]})")
        page.wait_for_timeout(800)
        c.ok(not page.locator("#ui-prompt-modal").is_visible(), "nothing left to pay: no money asked for")
        clear_alert(page)
        n2, a = inv_get(page, nxt.get("id")), inv_get(page, inv1)
        c.ok(n2.get("status") == "paid" and a.get("credit_available") == 700_000,
             f"ARM-23 paid by the credit; 700,000 left on ARM-1 ({n2.get('status')}, {a.get('credit_available')})")
        # 3. the rest refunded from the bank
        _row_action(page, ".inv-refund-credit", inv1)
        c.ok(page.locator("#ui-prompt-modal").is_visible() and page.input_value("#ui-prompt-input") == "700000",
             f"refund offers the 700,000 left ({page.input_value('#ui-prompt-input') if page.locator('#ui-prompt-input').count() else ''})")
        with page.expect_response(lambda r: "/refund-credit" in r.url) as res:
            page.click("#ui-prompt-ok")
        c.ok(res.value.status in (200, 201), f"refunded ({res.value.status} {res.value.text()[:150]})")
        m = alert_text(page); clear_alert(page)
        c.ok(FA(m), f"refund message Persian «{m}»")
        c.ok(inv_get(page, inv1).get("credit_available") == 0, "no credit left on ARM-1")
        # 4. a fully credited invoice reads «برگشت‌شده»; more than is left is refused
        res = _create_invoice(page, c, kind="sales", number="ARM-24" + RUN, entity=P["شرکت پارس‌افزار"], issue="2026-10-07", amount=2_000_000)
        full = res.json() if res.status in (200, 201) else {}
        clear_alert(page); page.wait_for_timeout(600)
        _row_action(page, ".inv-credit-note", full.get("id"))
        page.fill("#ui-prompt-input", "2000000"); page.click("#ui-prompt-ok"); page.wait_for_timeout(300)
        page.fill("#ui-prompt-input", "لغو سفارش"); page.click("#ui-prompt-ok")
        page.wait_for_timeout(900); clear_alert(page)
        f2 = inv_get(page, full.get("id"))
        c.ok(f2.get("status") == "credited" and f2.get("balance_due") == 0, f"credited, nothing owed ({f2.get('status')}, {f2.get('balance_due')})")
        frow = page.inner_text(f'#invoices-tbody tr:has([data-id="{full.get("id")}"])')
        c.ok("برگشت‌شده" in frow and "credited" not in frow, f"the row reads «برگشت‌شده» «{' '.join(frow.split())[:120]}»")
        s, body = api(page, "POST", f"/invoices/{full.get('id')}/credit-notes", {"amount": 1000})
        detail = (body or {}).get("detail", "") if isinstance(body, dict) else ""
        c.ok(s == 400 and FA(detail), f"a further credit note is refused, in Persian ({s} «{detail}»)")
        # 5. the history names each step, in Persian, in the company's calendar
        _row_action(page, ".inv-timeline", inv1)          # under ⋯ (#277)
        c.ok(confirm_shown(page), "the history opens")
        hist = page.inner_text("#ui-confirm-message")
        raw = re.search(r"\d{4}-\d{2}-\d{2}T|credit_note|Credit note", hist)
        c.ok(FA(hist) and "1405/" in hist and not raw, f"history in Persian, Jalali, no raw timestamps or keys «{' | '.join(hist.splitlines())[:300]}»")
        c.shots.append(shot(page, "C23", "history", full=False))
        page.click("#ui-confirm-ok")
        c.shots.append(shot(page, "C23", "after"))
        ux(page, "C23", "invoices after credit", lang="fa", shot_name="C23-after.png")
        # the one 400 is the refusal asked for above
        refused = (r"^403 ", r"^404 GET /favicon", rf"^400 POST /invoices/{full.get('id')}/credit-notes$")
        c.ok(watch.problems(allow=refused) == [], f"problems {watch.problems(allow=refused)}")
    finally:
        ctx.close()
    c.done()


def c9_c10_cheques(browser):
    c9, c10 = Check("C9"), Check("C10")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "commitments")
        # a cheque received from a client
        page.fill("#cm-c-title", "چک مشتری پارس‌افزار")
        page.locator("#cm-c-amount").press_sequentially("۱۲۰۰۰۰۰۰")
        page.fill("#cm-c-due", "2026-11-10")
        page.fill("#cm-c-ref", "۸۸۴۴۲۱")
        page.fill("#cm-c-sayad", "۱۲۳۴۵۶۷۸۹۰۱۲۳" + "".join("۰۱۲۳۴۵۶۷۸۹"[int(d)] for d in RUN))
        page.fill("#cm-c-bank", "ملت")
        page.select_option("#cm-c-dir", index=1)    # receive
        with page.expect_response(lambda r: "/commitments" in r.url and r.request.method == "POST") as res:
            page.click("#cm-c-save")
        c9.ok(res.value.status in (200, 201), f"received cheque added ({res.value.status} {res.value.text()[:150]})")
        chq = res.value.json() if res.value.status in (200, 201) else {}
        c9.ok((chq.get("sayad_id") or "").isascii(), f"Sayad ID stored in 0–9 ({chq.get('sayad_id')})")
        clear_alert(page)
        page.wait_for_timeout(600)
        c9.shots.append(shot(page, "C9", "cheque-added"))
        for step in ("deposit", "clear"):
            sel = page.locator(f'select.cm-step[data-id="{chq.get("id")}"]')
            if not sel.count():
                c9.ok(False, f"no actions for the cheque before {step}"); break
            opts = sel.locator("option").evaluate_all("os => os.map(o => o.value)")
            if step not in opts:
                c9.ok(False, f"step {step} not offered ({opts})"); break
            sel.select_option(step)
            page.wait_for_timeout(400)
            if page.locator("#cm-step-modal").is_visible():
                c9.shots.append(shot(page, "C9", f"step-{step}", full=False))
                if page.locator("#cm-step-bank").is_visible():
                    page.select_option("#cm-step-bank", index=1)
                with page.expect_response(lambda r: "/commitments/" in r.url and r.request.method == "POST") as r2:
                    page.click("#cm-step-ok")
                c9.ok(r2.value.status in (200, 201), f"{step} done ({r2.value.status} {r2.value.text()[:150]})")
            page.wait_for_timeout(700)
        chq2 = cm_get(page, chq.get("id"))
        c9.note(f"cheque status now {(chq2 or {}).get('status')}")
        c9.ok((chq2 or {}).get("status") in ("cleared", "settled"), f"cleared ({(chq2 or {}).get('status')})")
        ux(page, "C9", "commitments", lang="fa", shot_name="C9-cheque-added.png")
        # C10 — a 12-instalment loan
        page.fill("#cm-p-title", "وام بانک ملت")
        page.locator("#cm-p-total").press_sequentially("۱۲۰۰۰۰۰۰۰")
        page.fill("#cm-p-count", "12")
        page.fill("#cm-p-first", "2026-10-22")
        with page.expect_response(lambda r: "/commitments" in r.url and r.request.method == "POST") as res:
            page.click("#cm-p-save")
        c10.ok(res.value.status in (200, 201), f"plan created ({res.value.status} {res.value.text()[:150]})")
        page.wait_for_timeout(800)
        rows = page.inner_text("#cm-rows")
        c10.ok("1405/07/30" in rows, "first instalment shown on 1405/07/30 (22 Oct)")
        c10.ok("(1/12)" in rows or "(۱/۱۲)" in rows, "numbered 1/12")
        c10.shots.append(shot(page, "C10", "plan"))
    finally:
        ctx.close()
    c9.done(); c10.done()


def c11_recurring_rule(browser):
    c = Check("C11")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        go(page, "recurring")
        page.fill("#rec-name", "اجاره ماهانه دفتر")
        page.select_option("#rec-direction", "payment")
        page.select_option("#rec-frequency", "monthly")
        page.locator("#rec-amount").press_sequentially("45000000")
        page.fill("#rec-start", "2026-09-29")
        opts = page.locator("#rec-bank option").evaluate_all("os => os.map(o => o.value)")
        c.ok(BANK in opts, f"the new bank account is offered ({opts[:5]})")
        if BANK in opts:
            page.select_option("#rec-bank", BANK)
        copts = page.locator("#rec-counter option").evaluate_all("os => os.map(o => o.value)")
        if RENT in copts:
            page.select_option("#rec-counter", RENT)
        if not page.locator("#rec-autopost").is_checked():
            page.check("#rec-autopost")
        with page.expect_response(lambda r: "/recurring" in r.url and r.request.method == "POST") as res:
            page.click("#rec-manual-create")
        c.ok(res.value.status in (200, 201), f"rule saved ({res.value.status} {res.value.text()[:150]})")
        page.wait_for_timeout(700)
        rows = page.inner_text("#recurring-tbody")
        c.ok("ماهانه" in rows and "پرداخت" in rows and "monthly" not in rows, f"row in Persian ({rows[:120]})")
        with page.expect_response(lambda r: "/recurring" in r.url and "run" in r.url) as r2:
            page.click("#rec-run-due")
        c.ok(r2.value.status in (200, 201), f"run due ({r2.value.status} {r2.value.text()[:150]})")
        page.wait_for_timeout(800)
        c.shots.append(shot(page, "C11", "recurring"))
        ux(page, "C11", "recurring", lang="fa", shot_name="C11-recurring.png")
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for fn in (c1_voucher, c3_c4_sales_invoice, c5_bill, c6_c7_quote_credit_void, c23_credit_paid, c9_c10_cheques, c11_recurring_rule):
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:500])
        b.close()
