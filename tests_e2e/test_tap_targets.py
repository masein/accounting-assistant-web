"""The small controls the retest found on a phone (2026-10-02, #51): the
chat's session rename/delete (20×17 px, and hidden until hover — a touch
screen has none), the password eye (24×24), the daily-digest checkbox
(13×20) and a reminder's pause/delete (unnamed, 21×17); and the bell's
panel, which ran off the screen (#52)."""
from __future__ import annotations

from datetime import date, timedelta

from tests_e2e.conftest import BASE_URL, USERNAME, PageWatch, _flow_states, _log_in, switch_language, wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""
SIZE = """(sel) => [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null).map(e => {
  const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), getComputedStyle(e).opacity,
  e.getAttribute('aria-label') || '']; })"""


def _phone(browser, username):
    """A touch phone: no hover, a coarse pointer."""
    if username not in _flow_states:
        _flow_states[username] = _log_in(browser, username)
    ctx = browser.new_context(storage_state=_flow_states[username], viewport={"width": 390, "height": 844},
                              is_mobile=True, has_touch=True, device_scale_factor=2)
    page = ctx.new_page()
    watch = PageWatch(page)
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    return ctx, page, watch


def test_the_small_controls_are_thumb_sized_on_a_phone(browser):
    ctx, page, watch = _phone(browser, "e2e_taps")
    try:
        assert page.evaluate("() => matchMedia('(hover: none)').matches")
        switch_language(page, "fa")
        assert page.evaluate(POST, ["/ai-accountant/sessions", {"title": "گفتگوی آزمایشی"}])[0] == 201
        due = (date.today() + timedelta(days=5)).isoformat()
        assert page.evaluate(POST, ["/notifications/reminders", {"title": "تمدید بیمه", "due_date": due}])[0] == 201

        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        wait_until(page, "() => document.querySelectorAll('#ai-acct-session-list .sess-act').length >= 2")
        acts = page.evaluate(SIZE, "#ai-acct-session-list .sess-act")
        assert acts and all(w >= 32 and h >= 32 for w, h, _o, _l in acts), acts
        assert all(o == "1" for _w, _h, o, _l in acts), acts                 # shown without a hover
        assert all(label for *_x, label in acts), acts

        page.click("#notify-bell-btn")
        wait_until(page, "() => document.querySelectorAll('#rem-list .rem-del').length > 0")
        rem = page.evaluate(SIZE, "#rem-list .rem-toggle, #rem-list .rem-del")
        assert rem and all(w >= 32 and h >= 32 for w, h, *_ in rem), rem
        assert [label for *_x, label in rem][:2] == ["توقف یادآور", "حذف"], rem
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
        # the bell's panel fits the screen in both directions (it ran 30 px off
        # the right in Persian and off the left in English, #52)
        fits = "() => { const r = document.getElementById('notify-pop').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), innerWidth]; }"
        left, right, width = page.evaluate(fits)
        assert 0 <= left and right <= width, (left, right, width)
        page.click("#notify-bell-btn")                                    # closes
        switch_language(page, "en")
        page.click("#notify-bell-btn")
        wait_until(page, "() => getComputedStyle(document.getElementById('notify-pop')).display === 'block'")
        left, right, width = page.evaluate(fits)
        assert 0 <= left and right <= width, (left, right, width)
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
        ctx.close()


def test_the_password_eye_and_the_digest_box_on_a_phone(browser):
    """The owner's Settings: the eye on the new user's password and the AI key,
    and the daily-digest checkbox (its inline width:auto beat the phone size)."""
    ctx, page, watch = _phone(browser, USERNAME)
    try:
        page.evaluate("() => { location.hash = 'settings'; }")
        page.wait_for_load_state("networkidle")
        wait_until(page, "() => [...document.querySelectorAll('.card[data-page=\"settings\"] .pw-toggle')].some(e => e.offsetParent)")
        eyes = page.evaluate(SIZE, ".card[data-page='settings'] .pw-toggle")
        assert eyes and all(w >= 32 and h >= 32 for w, h, *_ in eyes), eyes
        box = page.evaluate(SIZE, "#digest-enabled")
        assert box and box[0][0] >= 20 and box[0][1] >= 20, box
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
        assert watch.problems() == [], watch.problems()
    finally:
        ctx.close()
