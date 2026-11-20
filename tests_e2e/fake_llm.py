"""A stand-in AI for end-to-end runs and screenshots (no key, no cost, the same answer every time).

It speaks the OpenAI chat-completions shape the app's openai_client sends, on port 1234 — where the dev stack's
default "LM Studio" provider looks (LM_STUDIO_BASE_URL=http://host.docker.internal:1234):

    docker run -d --rm --name fake-llm -p 127.0.0.1:1234:1234 -v "$PWD/tests_e2e:/w:ro" python:3.12-slim \\
        python /w/fake_llm.py

What it answers:
* a message with an amount ("paid 20 for lunch", «۵۰ هزار نون») → the app's own `propose_create_transaction`
  tool: an expense from cash, so the chat shows a real card with Confirm / Cancel;
* the tool's result → one line asking to confirm;
* anything else → a greeting that says what to try.
Account codes: FAKE_LLM_EXPENSE / FAKE_LLM_CASH (the Iranian chart's 6112 / 1110 by default; the UK chart's
7400 / 1210), in FAKE_LLM_CURRENCY (IRR).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPENSE = os.environ.get("FAKE_LLM_EXPENSE", "6112")
CASH = os.environ.get("FAKE_LLM_CASH", "1110")
CURRENCY = os.environ.get("FAKE_LLM_CURRENCY", "IRR")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _amount(text: str) -> int | None:
    t = text.translate(_FA_DIGITS).replace(",", "").replace("٬", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    n = float(m.group(0))
    if re.search(r"(هزار|k\b|thousand)", t, re.I):
        n *= 1000
    return int(n)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def answer(payload: dict) -> dict:
    messages = payload.get("messages") or []
    tools = {(t.get("function") or {}).get("name") for t in payload.get("tools") or []}
    last = messages[-1] if messages else {}
    persian = any(re.search(r"[\u0600-\u06FF]", _text(m.get("content"))) for m in messages if m.get("role") == "user")
    if last.get("role") == "tool":
        reply = ("پیش‌نویس سند آماده است — برای ثبت «تأیید» را بزنید." if persian
                 else "I've drafted the entry — tap Confirm to record it, or Cancel.")
        return _message(content=reply)
    user = _text(last.get("content")) if last.get("role") == "user" else ""
    amount = _amount(user)
    if amount and "propose_create_transaction" in tools:
        args = {"date": date.today().isoformat(), "description": (user.strip()[:80] or "Expense"), "currency": CURRENCY,
                "lines": [{"account_code": EXPENSE, "debit": amount, "credit": 0, "line_description": user.strip()[:80]},
                          {"account_code": CASH, "debit": 0, "credit": amount, "line_description": "cash"}]}
        return _message(tool_calls=[{"id": f"call_{int(time.time() * 1000)}", "type": "function",
                                     "function": {"name": "propose_create_transaction", "arguments": json.dumps(args)}}])
    reply = ("سلام! من دستیار آزمایشی هستم. بگویید چه خرجی داشتید — مثلاً «۵۰ هزار تومان نون»." if persian
             else "Hello! I'm the test assistant. Tell me what you spent or earned — for example \"paid 20 for lunch\".")
    return _message(content=reply)


def _message(content: str | None = None, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": f"chatcmpl-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": "fake-llm",
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        out = json.dumps(answer(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):  # noqa: N802 — /v1/models, for anything that asks
        out = json.dumps({"object": "list", "data": [{"id": "fake-llm", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        print("fake-llm:", fmt % args, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "1234"))), Handler).serve_forever()
