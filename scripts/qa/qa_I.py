"""Group I — every page, several languages and screen sizes: automated UI/UX checks plus a screenshot of each."""
import json
import sys
import time

sys.path.insert(0, "/qa")
from playwright.sync_api import sync_playwright

from qalib import OUT, finding, go, new_session, shot, ux

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
                    page.wait_for_timeout(200)
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
            finally:
                ctx.close()
        b.close()
    json.dump(summary, open(f"{OUT}/I-summary.json", "w"), indent=1)
