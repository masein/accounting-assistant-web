"""Group I — every page, several languages and screen sizes: automated UI/UX checks plus a screenshot of each."""
import json
import sys
import time

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import OUT, finding, go, new_session, settle, shot, ux

COMBOS = [("arman_owner", "fa", "desktop"), ("arman_owner", "fa", "tablet"), ("arman_owner", "fa", "mobile"),
          ("thames_owner", "en", "desktop"), ("thames_owner", "en", "mobile"),
          ("thames_owner", "es", "desktop"), ("thames_owner", "ar", "desktop"), ("sara", "fa", "mobile")]


def visible_pages(page):
    return page.evaluate("() => [...document.querySelectorAll('.nav-btn[data-page]')].filter(b => getComputedStyle(b).display !== 'none' && !b.closest('[hidden]')).map(b => b.dataset.page)")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    summary = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for user, lang, size in COMBOS:
            tag = f"{user}-{lang}-{size}"
            if only and only not in tag:
                continue
            ctx, page, watch = new_session(b, user, lang=lang, mobile=(size == "mobile"), tablet=(size == "tablet"),
                                           timezone="Europe/London" if user == "thames_owner" else "Asia/Tehran")
            try:
                pages = visible_pages(page)
                for name in pages:
                    watch.reset()
                    reqs = []
                    page.on("request", lambda r: reqs.append(r.url) if "/static/" not in r.url else None)
                    t0 = time.monotonic()
                    page.evaluate("(p) => { location.hash = p; }", name)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(500)
                    ms = int((time.monotonic() - t0) * 1000)
                    page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
                    settle(page)          # a section fetches when it opens (the Moadian panel was scanned empty)
                    sname = f"I-{tag}-{name}"
                    shot(page, "I", f"{tag}-{name}", full=(size != "mobile"))
                    hits = ux(page, "I", f"{name} [{tag}]", lang=lang, mobile=(size == "mobile"), shot_name=sname + ".png")
                    probs = watch.problems()
                    for p in probs:
                        finding("I", "I9", f"{name} [{tag}]", "request/js problem: " + p, sname + ".png")
                    if ms > 3000:
                        finding("I", "I9", f"{name} [{tag}]", f"slow: {ms} ms to idle", sname + ".png")
                    summary.append({"combo": tag, "page": name, "ms": ms, "requests": len(reqs), "ux": len(hits), "problems": len(probs)})
                    print(f"{tag:28} {name:18} {ms:5} ms {len(reqs):3} req  ux {len(hits):2}  probs {len(probs)}")
                # I11: the top bar's popups, open (the bell's panel ran off a phone, #52)
                for pop, btn, box, open_js in (("bell panel", "#notify-bell-btn", "#notify-pop", "() => getComputedStyle(document.getElementById('notify-pop')).display !== 'none'"),
                                               ("account menu", "#user-menu-btn", "#user-pop", "() => document.getElementById('user-pop').classList.contains('open')")):
                    watch.reset()
                    page.click(btn)
                    settle(page)
                    if not page.evaluate(open_js):
                        finding("I", "I11", f"{pop} [{tag}]", "did not open", "")
                        continue
                    sname = f"I-{tag}-{pop.replace(' ', '-')}"
                    shot(page, "I", f"{tag}-{pop.replace(' ', '-')}", full=False)
                    hits = ux(page, "I", f"{pop} [{tag}]", lang=lang, mobile=(size == "mobile"), shot_name=sname + ".png", scope=box)
                    for p in watch.problems():
                        finding("I", "I9", f"{pop} [{tag}]", "request/js problem: " + p, sname + ".png")
                    page.keyboard.press("Escape")
                    page.mouse.click(5, 300)            # a click outside closes either
                    page.wait_for_timeout(200)
                    print(f"{tag:28} {pop:18}            ux {len(hits):2}")
            finally:
                ctx.close()
        b.close()
    json.dump(summary, open(f"{OUT}/I-summary.json", "w"), indent=1)
