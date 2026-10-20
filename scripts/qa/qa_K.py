"""Group K: what shipped after the 2026-10-16 run — AL (the accountant speaks first: weekly and monthly
reviews, suggestions, a nudge, and their card) end to end, from real entries through the scheduler's job
to the web chat, the bell and the phone; and AK7 on the web (a bank renamed with its account).

Two phases, with the scheduler's job run between them on the server (the QA server's scheduler is off):

    sh scripts/qa/qa_run.sh qa_K.py seed
    docker exec aa-qa python /qa/qa_reviews_due.py ...      # the line the seed prints
    sh scripts/qa/qa_run.sh qa_K.py check

The AI is blanked on the QA server: what needs the model — AJ's scanned pages, AK's cards, AM's answer and
an idea's button turned into a card — is left to the pytest and e2e suites."""
import json
import random
import sys

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import BASE, OUT, PW, Check, api, go, lat, new_session, settle, shot, ux, wait_until

STATE = f"{OUT}/state_K.json"
FA = lambda s: any("؀" <= ch <= "ۿ" for ch in (s or ""))


def _state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save(**kw):
    st = _state()
    st.update(kw)
    json.dump(st, open(STATE, "w"), ensure_ascii=False)


def _phone(page, username):
    r = page.request.post(f"{BASE}/api/mobile/v1/auth/login",
                          data={"username": username, "password": PW, "device_name": "QA Pixel", "platform": "android"})
    return (r.json() if r.ok else {}).get("access_token")


def _mobile(page, token, method, path, body=None):
    r = page.request.fetch(f"{BASE}/api/mobile/v1{path}", method=method, data=body,
                           headers={"Authorization": f"Bearer {token}"})
    try:
        return r.status, r.json()
    except Exception:
        return r.status, r.text()


def _feed_reviews(page, token) -> list[dict]:
    s, feed = _mobile(page, token, "GET", "/feed")
    items = feed.get("items", feed) if isinstance(feed, dict) else feed
    return [i for i in (items or []) if isinstance(i, dict) and i.get("kind") == "review"]


def _post(page, day, text, dr, cr, amount):
    s, body = api(page, "POST", "/transactions", {"date": day, "description": text,
                                                  "lines": [{"account_code": dr, "debit": amount, "credit": 0},
                                                            {"account_code": cr, "debit": 0, "credit": amount}]})
    return s


def _codes(page) -> dict[str, str]:
    s, accts = api(page, "GET", "/accounts", None)
    return {str(a.get("code")): a.get("name") or "" for a in (accts or []) if isinstance(a, dict)}


def _pick(codes, want, *starts, words=()):
    if want in codes:
        return want
    for code, name in codes.items():
        if len(code) == 4 and code.startswith(starts) and (not words or any(w in name for w in words)):
            return code
    return None


# ── the seed: real entries, through the API the voucher form uses ─────────────────────────

def seed(browser):
    run = str(random.randint(100, 999))
    c = Check("K-seed")
    house, quiet = f"خانهٔ مرور {run}", f"Quiet household {run}"
    rv_user, qt_user = f"qa_rv_{run}", f"qa_qt_{run}"
    ctx, page, watch = new_session(browser, "qa_super", lang="en")
    try:
        for name, user in ((house, rv_user), (quiet, qt_user)):
            s, co = api(page, "POST", "/admin/companies", {"name": name, "locale": "ir", "kind": "personal",
                                                            "base_currency": "IRR", "username": user, "password": PW})
            c.ok(s == 201, f"{name} ({s} {str(co)[:120]})")
    finally:
        ctx.close()
    # a person's books: three months of salary, a grocery run a week, and dining out last week
    ctx, page, watch = new_session(browser, rv_user, lang="fa")
    try:
        codes = _codes(page)
        cash, equity, salary = _pick(codes, "1110", "11"), _pick(codes, "3110", "3"), _pick(codes, "4110", "4")
        food, dining = _pick(codes, "6110", "6"), _pick(codes, "6180", "6", words=("رستوران",))
        c.ok(all((cash, equity, salary, food, dining)), f"the personal chart's places {cash, equity, salary, food, dining}")
        M = 1_000_000
        rows = [("2026-07-01", "موجودی اول", cash, equity, 400 * M)]
        rows += [(d, "حقوق", cash, salary, 60 * M) for d in ("2026-07-25", "2026-08-25", "2026-09-25")]
        rows += [(d, "خرید سوپرمارکت", food, cash, 4 * M) for d in ("2026-07-28", "2026-08-04", "2026-08-11", "2026-08-18")]
        rows += [(d, "خرید سوپرمارکت", food, cash, 3 * M) for d in ("2026-08-26", "2026-09-02", "2026-09-09", "2026-09-16")]
        rows += [("2026-09-18", "شام بیرون", dining, cash, 2 * M)]
        rows += [(d, "خرید سوپرمارکت", food, cash, 3 * M) for d in ("2026-09-27", "2026-10-04")]
        rows += [("2026-10-11", "خرید سوپرمارکت", food, cash, 3 * M), ("2026-10-14", "رستوران با دوستان", dining, cash, 6 * M)]
        bad = [(r[0], s) for r in rows if (s := _post(page, *r)) != 201]
        c.ok(not bad, f"{len(rows)} entries ({bad[:4]})")
    finally:
        ctx.close()
    # a company's week: a sale and a bill in the Jalali week 18–24 Mehr
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        codes = _codes(page)
        try:
            bank = json.load(open(f"{OUT}/state.json")).get("bank_code") or "1110"
        except Exception:
            bank = "1110"
        sales = _pick(codes, "", "4", words=("فروش",))
        cost = _pick(codes, "", "6", "8", words=("اجاره", "اداری", "هزینه"))
        bad = [s for s in (_post(page, "2026-10-11", f"فروش هفته {run}", bank, sales, 50_000_000),
                           _post(page, "2026-10-13", f"هزینهٔ هفته {run}", cost, bank, 8_000_000)) if s != 201]
        c.ok(not bad, f"Arman's week: a sale to {sales}, a cost to {cost} ({bad})")
    finally:
        ctx.close()
    # a UK week, Monday to Sunday, after three before it
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        codes = _codes(page)
        bank, sales = _pick(codes, "1200", "12"), _pick(codes, "4000", "4")
        bad = [s for s in (_post(page, d, f"Studio fee {run}", bank, sales, amt) for d, amt in
                           (("2026-09-22", "1800.00"), ("2026-09-29", "2000.00"), ("2026-10-06", "2100.00"),
                            ("2026-10-13", "2400.00"))) if s != 201]
        c.ok(not bad, f"Thames' weeks ({bank} ← {sales}) ({bad})")
    finally:
        ctx.close()
    _save(run=run, house=house, quiet=quiet, rv_user=rv_user, qt_user=qt_user)
    c.done()
    print("RUN THIS:", "docker exec aa-qa python /qa/qa_reviews_due.py",
          f"'{house}=2026-10-17,2026-09-23,2026-10-29'", "'بازرگانی آرمان=2026-10-17'",
          "'Thames Studio Ltd=2026-10-19'", f"'{quiet}=2026-10-17,2026-09-23,2026-10-29'")


# ── AL: what the job made, where it shows ──────────────────────────────────────────────────

def al_person(browser):
    st = _state()
    made = json.load(open(f"{OUT}/reviews_made.json"))
    c1, c4, c5, c13, c16, c17 = (Check(x) for x in ("AL1", "AL4", "AL5", "AL13", "AL16", "AL17"))
    got = made.get(st["house"], {})
    c1.note(f"made: {got}")
    c1.ok(len(got.get("2026-10-17", [])) == 1, "the week 18–24 Mehr: one review")
    c13.ok(len(got.get("2026-10-29", [])) == 1, "nine quiet days: one nudge")
    # the phone first: a person's home on the web is the chat, which shows them (and so they are seen) at once
    pctx = browser.new_context()
    phone = pctx.new_page()
    token = _phone(phone, st["rv_user"])
    before = _feed_reviews(phone, token)
    c5.note(f"the phone's feed: {[(i.get('title'), i.get('body', '')[:60]) for i in before]}")
    c5.ok(len(before) == 3 and all(FA(i.get("title")) for i in before), "three reviews in the phone's feed, in Persian")
    ctx, page, watch = new_session(browser, st["rv_user"], lang="fa")
    try:
        go(page, "ai-accountant")
        settle(page, min_ms=800)
        wait_until(page, "() => document.querySelectorAll('.ai-review-card').length >= 3", timeout_ms=10_000)
        cards = page.locator(".ai-review-card")
        c4.ok(cards.count() == 3, f"three cards in the chat ({cards.count()})")
        page.wait_for_timeout(2500)                                 # the counts and the bars finish
        week = page.locator(".ai-review-card.kind-weekly")
        month = page.locator(".ai-review-card.kind-monthly")
        nudge = page.locator(".ai-review-card.kind-nudge")
        title = lat(week.locator(".ai-review-title").inner_text()) if week.count() else ""
        c16.ok("18" in title and "24" in title and "مهر" in title and "1405" in title, f"the week in Jalali, Persian digits («{title}»)")
        c16.ok(not any(ch.isascii() and ch.isdigit() for ch in week.locator(".ai-review-title").inner_text()), "no Latin digit")
        if week.count():
            c17.ok(week.locator(".ai-review-emoji").inner_text().strip() == "👀", f"above the usual: 👀 ({week.locator('.ai-review-emoji').inner_text()})")
            fig = lat(week.locator(".ai-review-figure").inner_text())
            c17.ok("9" in fig, f"the week's spending counted up to 9,000,000 («{fig}»)")
            c17.ok(week.locator(".ai-review-col").count() == 5 and week.locator(".ai-review-col-label.is-current").count() == 1,
                   f"five weeks of bars, this one marked ({week.locator('.ai-review-col').count()})")
            c17.ok(week.locator(".ai-review-usual").count() == 1, "a usual line")
            cats = [lat(x) for x in week.locator(".ai-review-cat-name").all_inner_texts()]
            c17.ok(len(cats) >= 2, f"the categories ({cats})")
            ideas = week.locator(".ai-review-idea-text").all_inner_texts()
            c1.note(f"ideas: {ideas}")
            asks = week.locator(".ai-review-ask").all_inner_texts()
            c1.ok(any("رستوران" in a and "بودجه" in a for a in asks), f"dining out ran hot: a budget button that carries it ({asks})")
            c17.shots.append(shot(page, "AL17", "week", selector=".ai-review-card.kind-weekly"))
        if month.count():
            c17.ok(month.locator(".ai-review-emoji").inner_text().strip() in ("🏆", "🌿"), f"a good month ({month.locator('.ai-review-emoji').inner_text()})")
            ring = lat(month.locator(".ai-review-ring-pct").inner_text()) if month.locator(".ai-review-ring-pct").count() else ""
            c17.ok("درآمد" in ring and "77" in ring, f"the savings ring: 46 of 60 saved («{ring}»)")
            mtitle = lat(month.locator(".ai-review-title").inner_text())
            c16.ok("شهریور" in mtitle and "1405" in mtitle, f"the month Shahrivar 1405 («{mtitle}»)")
            c17.shots.append(shot(page, "AL17", "month", selector=".ai-review-card.kind-monthly"))
        if nudge.count():
            c13.ok(nudge.locator(".ai-review-emoji").inner_text().strip() == "✏️", "the nudge's ✏️")
            c13.ok("ثبت" in nudge.inner_text(), f"«{nudge.inner_text()[:120]}»")
            c17.shots.append(shot(page, "AL13", "nudge", selector=".ai-review-card.kind-nudge"))
        ux(page, "AL17", "the chat with three reviews", lang="fa", shot_name="AL17-chat.png")
        c4.shots.append(shot(page, "AL4", "chat", selector="#ai-acct-messages"))
        # seen once: not again, but kept in the history, still
        page.reload()
        page.wait_for_load_state("networkidle")
        go(page, "ai-accountant")
        settle(page, min_ms=800)
        s, again = api(page, "POST", "/ai-accountant/reviews", {"session_id": None})
        c4.ok(s == 200 and not (again or {}).get("reviews"), f"not said twice ({s} {str(again)[:80]})")
        first = page.locator(".sess-item .sess-title").first
        if first.count():
            first.click()
        settle(page, min_ms=800)
        kept = page.locator("#ai-acct-messages .ai-review-card").count()
        c4.ok(kept == 3, f"the history redraws the three cards ({kept})")
        c4.shots.append(shot(page, "AL4", "history", selector="#ai-acct-messages"))
        c5.ok(not _feed_reviews(phone, token), "seen in the chat, they leave the phone's feed")
        page.click("#notify-bell-btn")
        page.wait_for_timeout(600)
        bell = page.inner_text("#notify-pop")
        c5.ok("مرور هفتگی آماده است" not in bell, f"and the bell («{bell[:160]}»)")
        c4.ok(not watch.problems(), f"no page errors ({watch.problems()[:3]})")
    finally:
        ctx.close()
        pctx.close()
    for c in (c1, c4, c5, c13, c16, c17):
        c.done()


def al_company(browser):
    st = _state()
    made = json.load(open(f"{OUT}/reviews_made.json"))
    c2, c16, c17 = Check("AL2"), Check("AL16-en"), Check("AL17-still")
    c2.note(f"made: {made.get('بازرگانی آرمان')} / {made.get('Thames Studio Ltd')}")
    c2.ok(len(made.get("بازرگانی آرمان", {}).get("2026-10-17", [])) == 1, "Arman's week: one review")
    # a viewer reads none
    ctx, page, watch = new_session(browser, "arman_view", lang="fa")
    try:
        token = _phone(page, "arman_view")
        c2.ok(not _feed_reviews(page, token), "a viewer's phone gets none")
        s, out = api(page, "POST", "/ai-accountant/reviews", {"session_id": None})
        c2.ok(s in (200, 403) and not (out or {}).get("reviews"), f"nor the viewer's chat ({s})")
    finally:
        ctx.close()
    # the owner reads it in Persian: the bell says so on the dashboard, then the chat shows it
    c5 = Check("AL5-bell")
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        settle(page)
        page.click("#notify-bell-btn")
        page.wait_for_timeout(600)
        bell = page.inner_text("#notify-pop")
        c5.ok("مرور هفتگی آماده است" in bell, f"the bell has the week («{bell[:200]}»)")
        c5.shots.append(shot(page, "AL5", "bell", full=False))
        page.keyboard.press("Escape")
        go(page, "ai-accountant")
        settle(page, min_ms=800)
        wait_until(page, "() => document.querySelector('.ai-review-card.kind-weekly')", timeout_ms=10_000)
        card = page.locator(".ai-review-card.kind-weekly")
        c2.ok(card.count() == 1, "the owner's chat has the week")
        text = lat(card.inner_text()) if card.count() else ""
        c2.note(f"«{text[:300]}»")
        c2.ok("50,000,000" in text or "50" in text, "the week's money in, from the sale")
        c2.shots.append(shot(page, "AL2", "owner", selector=".ai-review-card.kind-weekly"))
    finally:
        ctx.close()
    # the CFO reads the same review in English, with motion turned down: all there at once
    ctx, page, watch = new_session(browser, "arman_cfo", lang="en")
    try:
        page.emulate_media(reduced_motion="reduce")
        go(page, "ai-accountant")
        settle(page, min_ms=400)
        wait_until(page, "() => document.querySelector('.ai-review-card.kind-weekly')", timeout_ms=10_000)
        card = page.locator(".ai-review-card.kind-weekly")
        if card.count():
            fig0 = card.locator(".ai-review-figure").inner_text()
            page.wait_for_timeout(1500)
            c17.ok(fig0 == card.locator(".ai-review-figure").inner_text(), f"the figure is whole at once («{fig0}»)")
            title = card.locator(".ai-review-title").inner_text()
            c16.ok(not FA(title) and any(ch.isdigit() for ch in title), f"English words and digits («{title}»)")
            c16.note(f"«{card.inner_text()[:300]}»")
            c16.shots.append(shot(page, "AL16", "cfo-en", selector=".ai-review-card.kind-weekly"))
        else:
            c16.ok(False, "the CFO's chat has the week")
    finally:
        ctx.close()
    # a UK week on the phone: the review block the app draws
    ctx, page, watch = new_session(browser, "thames_owner", lang="en", timezone="Europe/London")
    try:
        token = _phone(page, "thames_owner")
        s, brief = _mobile(page, token, "POST", "/briefing", {"session_id": None})
        blocks = [b for m in ((brief or {}).get("messages") or [brief]) for b in ((m or {}).get("blocks") or [])
                  if isinstance(b, dict) and b.get("type") == "review"] if isinstance(brief, dict) else []
        c16.ok(len(blocks) == 1, f"the phone's briefing brings one review block ({s} {str(brief)[:200]})")
        if blocks:
            b = blocks[0]
            c16.note(f"phone: {b.get('title')} · {b.get('headline')}")
            c16.ok("12" in (b.get("title") or "") and "18" in (b.get("title") or "") and "Oct" in (b.get("title") or ""),
                   f"a Monday-to-Sunday week, 12–18 Oct ({b.get('title')})")
            c16.ok(len((b.get("chart") or {}).get("bars") or []) == 5, "five weeks of bars")
    finally:
        ctx.close()
    for c in (c2, c5, c16, c17):
        c.done()


def al_switches_and_quiet(browser):
    st = _state()
    made = json.load(open(f"{OUT}/reviews_made.json"))
    c14, c15 = Check("AL14"), Check("AL15")
    q = made.get(st["quiet"], {})
    c15.ok(q and not any(q.values()), f"books with nothing in them make nothing ({q})")
    ctx, page, watch = new_session(browser, st["qt_user"], lang="fa")
    try:
        token = _phone(page, st["qt_user"])
        c15.ok(not _feed_reviews(page, token), "no notification")
        go(page, "ai-accountant")
        settle(page, min_ms=800)
        c15.ok(page.locator(".ai-review-card").count() == 0, "no empty message")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        go(page, "ai-accountant")
        settle(page)
        box = page.locator("#ai-reviews-prefs")
        c14.ok(box.is_visible(), "the switches beside the chat")
        box.locator("summary").click()
        weekly = box.locator("input[data-kind='weekly']")
        c14.ok(weekly.is_checked(), "weekly is on")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/review-prefs") and r.request.method == "PUT") as res:
            weekly.uncheck()
        c14.ok(res.value.status == 200, f"turned off ({res.value.status})")
        c14.shots.append(shot(page, "AL14", "off", selector="#ai-reviews-prefs"))
        s, prefs = api(page, "GET", "/ai-accountant/review-prefs", None)
        c14.ok(prefs.get("weekly") is False and prefs.get("monthly") is not False, f"weekly only ({prefs})")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        s, prefs = api(page, "GET", "/ai-accountant/review-prefs", None)
        c14.ok(prefs.get("weekly") is not False, f"the accountant still gets theirs ({prefs})")
    finally:
        ctx.close()
    ctx, page, watch = new_session(browser, "arman_owner", lang="fa")
    try:
        s, prefs = api(page, "GET", "/ai-accountant/review-prefs", None)
        s, back = api(page, "PUT", "/ai-accountant/review-prefs", {**prefs, "weekly": True})
        c14.ok(s == 200 and back.get("weekly") is True, f"and back on ({s})")
    finally:
        ctx.close()
    c14.done()
    c15.done()


# ── AK7 on the web: a bank renamed on the Parties page ─────────────────────────────────────

def ak7_bank_renamed(browser):
    st = _state()
    c = Check("AK7-web")
    ctx, page, watch = new_session(browser, "arman_acc", lang="fa")
    try:
        name, new = f"تجارت {st['run']}", f"تجارت مرکزی {st['run']}"
        s, bank = api(page, "POST", "/entities", {"type": "bank", "name": name})
        c.ok(s in (200, 201), f"a bank party ({s} {str(bank)[:120]})")
        code = (bank or {}).get("code")
        before = _codes(page).get(code)
        c.note(f"its account {code}: «{before}»")
        s, renamed = api(page, "PATCH", f"/entities/{bank.get('id')}", {"name": new})
        c.ok(s == 200, f"renamed ({s})")
        after = _codes(page).get(code)
        c.ok(after and new in after, f"its account follows the new name: «{before}» → «{after}»")
        go(page, "accounts")
        settle(page)
        c.shots.append(shot(page, "AK7", "chart"))
    finally:
        ctx.close()
    c.done()


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        fns = (seed,) if phase == "seed" else (al_person, al_company, al_switches_and_quiet, ak7_bank_renamed)
        for fn in fns:
            if only and not fn.__name__.startswith(only):
                continue
            try:
                fn(b)
            except Exception as e:
                from qalib import record
                record(fn.__name__.split("_")[0].upper(), "ERROR", repr(e)[:500])
        b.close()
