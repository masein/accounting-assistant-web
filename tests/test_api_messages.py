"""API error messages come back in the page's language (app/core/messages.py):
"Invalid username or password" reached a Persian user in English, like every
other error an endpoint raised."""
from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

from app.core.messages import EXACT, LANGS, PATTERNS, localize_detail, request_language

APP = Path(__file__).resolve().parents[1] / "app"


def _raised_details() -> tuple[set[str], list[str]]:
    """Every HTTPException detail in the code: whole strings, and the f-strings'
    shapes (each run-time part as a placeholder)."""
    literal, shapes = set(), []

    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "\x00" for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = text(node.left), text(node.right)
            return None if left is None or right is None else left + right
        return None

    for path in APP.rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "HTTPException":
                d = next((k.value for k in n.keywords if k.arg == "detail"), n.args[1] if len(n.args) > 1 else None)
                s = text(d) if d is not None else None
                if s is None:
                    continue
                (shapes.append(s) if "\x00" in s else literal.add(s))
    return literal, shapes


def test_every_translation_is_for_a_message_the_code_raises():
    literal, shapes = _raised_details()
    shared = {"Request validation failed", "Internal server error"}      # written by app/main.py's handlers
    stale = sorted(k for k in EXACT if k not in literal and k not in shared)
    assert stale == [], stale
    for key, said in EXACT.items():
        assert set(said) == set(LANGS), key
    # each pattern matches some message the code builds (its run-time parts filled with a sample)
    samples = [s.replace("\x00", "x1") for s in shapes]
    samples += [s.replace("\x00", word, 1).replace("\x00", "x1") for s in shapes for word in ("Sales", "void")]
    samples += [s.replace("\x00", "1") for s in shapes]
    for p in PATTERNS:
        assert any(p.regex.match(s) for s in samples), p.regex.pattern
        assert set(p.text) == set(LANGS), p.regex.pattern
        fields = set(p.regex.groupindex)
        for lang, t in p.text.items():
            assert set(re.findall(r"\{(\w+)\}", t)) == fields, (p.regex.pattern, lang)


def test_messages_read_in_the_language_asked_for():
    assert localize_detail("Invoice not found", "fa") == "فاکتور پیدا نشد"
    assert localize_detail("Invoice not found", "es") == "Factura no encontrada"
    assert localize_detail("Invoice not found", "en") == "Invoice not found"
    assert localize_detail("Something nobody translated", "fa") == "Something nobody translated"
    assert localize_detail({"code": "x"}, "fa") == {"code": "x"}            # a structured detail is left alone
    # a message built at run time: its parts kept whole (and isolated in a right-to-left line)
    assert localize_detail("Account not found: 6112", "fa") == "حساب پیدا نشد: ⁨6112⁩"
    assert localize_detail("Cannot pay a void invoice.", "es") == "No se puede pagar una factura anulada."
    assert localize_detail("Sales invoice number 'S-7' already exists (id 12).", "fa").startswith("شماره فاکتور ⁨فروش⁩")
    # a reason inside a reason is translated too
    nested = localize_detail("Could not post the payment — Account not found: 6112", "fa")
    assert nested == "پرداخت ثبت نشد — ⁨حساب پیدا نشد: ⁨6112⁩⁩"


def test_the_language_comes_from_the_page_then_the_browser():
    assert request_language({"x-ui-language": "fa"}) == "fa"
    assert request_language({"x-ui-language": "en", "accept-language": "fa-IR,fa;q=0.9"}) == "en"   # the page wins
    assert request_language({"accept-language": "es-ES,es;q=0.9,en;q=0.8"}) == "es"
    assert request_language({"accept-language": "de-DE,ar;q=0.7"}) == "ar"
    assert request_language({"x-ui-language": "klingon"}) == "en"
    assert request_language({}) == "en"


def test_an_endpoints_error_comes_back_in_the_page_language(auth_client):
    missing = f"/transactions/{uuid.uuid4()}"
    r = auth_client.get(missing, headers={"X-UI-Language": "fa"})
    assert r.status_code == 404 and r.json()["detail"] == "سند پیدا نشد"
    r = auth_client.get(missing, headers={"Accept-Language": "ar,en;q=0.5"})
    assert r.json()["detail"] == "القيد غير موجود"
    assert auth_client.get(missing).json()["detail"] == "Transaction not found"
    # validation errors keep their structure, with the summary translated
    r = auth_client.post("/invoices", json={"number": 7}, headers={"X-UI-Language": "es"})
    assert r.status_code == 422 and r.json()["detail"] == "La solicitud no es válida" and r.json()["errors"]


def test_a_wrong_password_reads_in_persian_on_the_sign_in_page(client):
    r = client.post("/auth/login", json={"username": f"nobody-{uuid.uuid4().hex[:6]}", "password": "x" * 12},
                    headers={"X-UI-Language": "fa"})
    assert r.status_code == 401 and r.json()["detail"] == "نام کاربری یا رمز عبور نادرست است"


def test_a_timed_out_second_step_says_so_in_a_code_not_just_words(client):
    r = client.post("/auth/login/2fa", json={"challenge": "not-a-challenge", "code": "123456"},
                    headers={"X-UI-Language": "fa"})
    assert r.status_code == 401
    assert r.headers.get("x-error-code") == "signin_timed_out"
    assert r.json()["detail"] == "مهلت ورود تمام شد. رمز عبور را دوباره وارد کنید."


def test_payroll_time_users_and_quotes_read_in_the_language_too():
    assert localize_detail("Pay run not found.", "fa") == "دوره حقوق پیدا نشد."
    assert localize_detail("Run is draft; post it before paying.", "es") == "La nómina está en borrador; contabilízala antes de pagarla."
    assert localize_detail("Run already paid; cannot post again.", "fa") == "این دوره حقوق قبلاً ⁨پرداخت‌شده⁩ است؛ دوباره ثبت نمی‌شود."
    assert localize_detail("A uk rule set for 2027 already exists.", "ar") == "توجد بالفعل مجموعة قواعد ⁨المملكة المتحدة⁩ لسنة ⁨2027⁩."
    assert localize_detail("A sent quote can't be edited; set it back to draft first.", "es") == \
        "Un presupuesto enviado no se puede editar; vuelve a ponerlo en borrador primero."
    hours = localize_detail("That would be 26 hours on 2026-10-01 (20 already logged); a day has 24.", "fa")
    assert hours.startswith("با این ثبت، ⁨2026-10-01⁩ به ⁨26⁩ ساعت")
    assert localize_detail("You cannot delete yourself", "fa") == "نمی‌توانید خودتان را حذف کنید"
