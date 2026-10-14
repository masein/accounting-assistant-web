"""Recording a voice note on the web chat (scenarios W1–W5, W7). The browser here has no
microphone: getUserMedia hands back a tone, and the test answers the transcription, so no
AI is needed. What's checked is the bar, the ways to finish and to throw it away, and that
the words land in the text box without being sent."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

FAKE_MIC = """
navigator.mediaDevices.getUserMedia = async () => {
  if (window.__micDenied) throw new DOMException('denied', 'NotAllowedError');
  const ac = new AudioContext();
  const osc = ac.createOscillator();
  const dst = ac.createMediaStreamDestination();
  osc.frequency.value = 220; osc.connect(dst); osc.start();
  const stream = dst.stream;
  stream.getTracks().forEach((tr) => { const stop = tr.stop.bind(tr);
    tr.stop = () => { window.__micStopped = (window.__micStopped || 0) + 1; stop(); }; });
  return stream;
};
"""
SAID = "پرداخت اجاره ۵۰۰ هزار تومان"


def _chat(flow_page):
    page, watch = flow_page("e2e_voice")
    page.add_init_script(FAKE_MIC)
    calls = []

    def answer(route):
        calls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body='{"text": "%s"}' % SAID)
    page.route("**/ai-accountant/transcribe", answer)
    sent = []
    page.on("request", lambda r: sent.append(r.url) if r.url.endswith("/ai-accountant/chat") else None)
    page.reload()
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-mic:not([hidden])")
    return page, watch, calls, sent


def _bar_up(page):
    wait_until(page, "() => !document.getElementById('ai-acct-rec').hidden")
    assert not page.locator("#ai-acct-input").is_visible()                 # the bar in the text box's place


def _bar_down(page):
    wait_until(page, "() => document.getElementById('ai-acct-rec').hidden")
    assert page.locator("#ai-acct-input").is_visible()


def _hold(page, ms, slide=0):
    box = page.locator("#ai-acct-mic").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(ms)
    if slide:
        page.mouse.move(x + slide, y, steps=8)
    page.mouse.up()


def test_w1_w2_tap_finish_and_throw_away(flow_page):
    page, watch, calls, sent = _chat(flow_page)
    page.click("#ai-acct-mic")                                              # W1: a tap, hands-free
    _bar_up(page)
    wait_until(page, "() => /0:0[1-9]/.test(document.getElementById('ai-acct-rec-time').textContent)")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator(".ai-composer").screenshot(path=os.path.join(ARTIFACTS, "voice-recording.png"))
    page.click("#ai-acct-rec-done")
    wait_until(page, f"() => document.getElementById('ai-acct-input').value === {SAID!r}")
    _bar_down(page)
    assert len(calls) == 1 and sent == []                                  # written, not sent
    stopped = page.evaluate("() => window.__micStopped || 0")
    assert stopped >= 1                                                    # the microphone let go

    page.fill("#ai-acct-input", "")
    page.click("#ai-acct-mic")                                              # W2: ✕
    _bar_up(page)
    page.wait_for_timeout(700)
    page.click("#ai-acct-rec-cancel")
    _bar_down(page)
    page.click("#ai-acct-mic")                                              # …and Esc
    _bar_up(page)
    page.wait_for_timeout(700)
    page.keyboard.press("Escape")
    _bar_down(page)
    assert len(calls) == 1 and page.input_value("#ai-acct-input") == ""     # nothing more transcribed
    assert page.evaluate("() => window.__micStopped") >= stopped + 2
    assert watch.problems() == [], watch.problems()


def test_w3_w4_w5_hold_slide_and_too_short(flow_page):
    page, watch, calls, sent = _chat(flow_page)
    _hold(page, 1200)                                                       # W3: hold, let go
    wait_until(page, f"() => document.getElementById('ai-acct-input').value === {SAID!r}")
    _bar_down(page)
    assert len(calls) == 1

    page.fill("#ai-acct-input", "")
    _hold(page, 900, slide=-140)                                            # W4: slide toward the start (left)
    _bar_down(page)
    assert len(calls) == 1 and page.input_value("#ai-acct-input") == ""

    page.click("#ai-acct-mic")                                              # W5: too short to hold words
    _bar_up(page)
    page.click("#ai-acct-rec-done")
    _bar_down(page)
    assert len(calls) == 1
    assert sent == [] and watch.problems() == [], watch.problems()


def test_w4_in_persian_the_start_is_the_right(flow_page):
    page, watch, calls, sent = _chat(flow_page)
    switch_language(page, "fa")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_timeout(500)
    _hold(page, 900, slide=-140)                                            # leftward is the end side: kept
    wait_until(page, f"() => document.getElementById('ai-acct-input').value === {SAID!r}")
    page.fill("#ai-acct-input", "")
    _hold(page, 900, slide=140)                                             # rightward: thrown away
    _bar_down(page)
    assert len(calls) == 1
    switch_language(page, "en")
    assert watch.problems() == [], watch.problems()


def test_w7_without_the_microphone(flow_page):
    page, watch, calls, sent = _chat(flow_page)
    page.evaluate("() => { window.__micDenied = true; }")
    page.click("#ai-acct-mic")
    page.wait_for_timeout(500)
    assert page.locator("#ai-acct-rec").is_hidden() and page.locator("#ai-acct-input").is_visible()
    assert calls == []
