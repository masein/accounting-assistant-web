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
# exceptions whose text an endpoint puts in its detail ("<employee>: <reason>")
REASONS = {"PayrollInputError"}


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
            name = getattr(n.func, "id", getattr(n.func, "attr", None)) if isinstance(n, ast.Call) else None
            if name in REASONS and n.args:      # its text becomes part of a detail
                reason = text(n.args[0])
                if reason is not None and "\x00" not in reason:
                    literal.add(reason)
            if name == "HTTPException":
                d = next((k.value for k in n.keywords if k.arg == "detail"), n.args[1] if len(n.args) > 1 else None)
                s = text(d) if d is not None else None
                if s is None:
                    continue
                (shapes.append(s) if "\x00" in s else literal.add(s))
    return literal, shapes


def _samples(shapes):
    out = [s.replace("\x00", "x1") for s in shapes]
    out += [s.replace("\x00", word, 1).replace("\x00", "x1") for s in shapes for word in ("Sales", "void")]
    out += [s.replace("\x00", "1") for s in shapes]
    return out


def _shape_rx(shape: str) -> re.Pattern:
    return re.compile("^" + "(.+)".join(re.escape(part) for part in shape.split("\x00")) + "$", re.S)


# Built at run time and never reached from the app's pages: a value the page
# only offers from a list, or a check on what the server itself stored.
API_ONLY = {
    "Costing method must be one of \x00.": "a select on the stock page",
    "Invalid entity type \x00.": "the AI's own proposal, checked before it runs",
    "Stored proposal payload is malformed: \x00": "a stored proposal the server wrote",
    "Unknown acquisition: \x00": "a select on the fixed-asset form",
    "Unknown equity tool \x00": "the AI's own tool call",
    "Unknown field: \x00": "the journal import's column map, from a select",
    "Unknown method: \x00": "a select on the fixed-asset form",
    "Unknown preset: \x00": "the journal import's presets, from a select",
    "Unsupported shape \x00. Supported: \x00 or '' (auto).": "a select in the AI settings",
    "entry_type must be one of \x00.": "a select on the time page",
    "repeat must be one of \x00": "a select on the reminders form",
    "state must be one of \x00": "the Moadian status, set by the integration",
    "status must be one of \x00; use convert to invoice it.": "the quote's status buttons",
    "\x00 must be one of \x00.": "the recurring-invoice form's selects",
}


def test_every_message_built_at_run_time_has_a_translation():
    """An f-string detail a page can reach reads in the page's language: the
    rate feeds' and the cheque layout's checks, payroll's reasons, a bad
    entity name all came back in English."""
    literal, shapes = _raised_details()
    samples = _samples(shapes)
    missing = []
    for shape in sorted(set(shapes)):
        if shape in API_ONLY:
            continue
        own = [x for x in samples if _shape_rx(shape).match(x)]
        if any(p.regex.match(x) for p in PATTERNS for x in own):
            continue
        if any(p.example is not None and _shape_rx(shape).match(p.example) for p in PATTERNS):
            continue
        missing.append(shape.replace("\x00", "{}"))
    assert missing == [], missing
    assert all(k in set(shapes) for k in API_ONLY), "API_ONLY lists a message the code no longer builds"


def test_a_payroll_reason_reads_in_the_language_after_the_name():
    said = localize_detail("Sara Ahmadi: Withholdings exceed gross pay — check the tax/deduction rates.", "fa")
    assert said.startswith("\u2068Sara Ahmadi\u2069: ") and "کسورات" in said
    # a reason no one translated stays as it was, name and all
    assert localize_detail("Sara: Hours of something new.", "es") == "Sara: Hours of something new."


def test_the_cheque_layout_says_which_box():
    assert localize_detail("The top of national_id must be between 0 and 75.", "es") == \
        "El borde superior del campo «documento de identidad» debe estar entre 0 y 75."
    assert localize_detail("The height must be a number.", "ar") == "يجب أن يكون \u2068الارتفاع\u2069 رقماً."


def test_every_translation_is_for_a_message_the_code_raises():
    literal, shapes = _raised_details()
    shared = {"Request validation failed", "Internal server error"}      # written by app/main.py's handlers
    stale = sorted(k for k in EXACT if k not in literal and k not in shared)
    assert stale == [], stale
    for key, said in EXACT.items():
        assert set(said) == set(LANGS), key
    # each pattern matches some message the code builds (its run-time parts filled with a sample)
    samples = _samples(shapes)
    for p in PATTERNS:
        if p.example is not None:
            # a fixed set of run-time parts: the example is one the code builds
            assert p.regex.match(p.example), p.regex.pattern
            assert any(_shape_rx(s).match(p.example) for s in shapes), p.example
        else:
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


def test_the_books_cheques_parties_and_orders_read_in_the_language_too():
    closed = localize_detail("Period is closed through 2026-06-30; cannot post or back-date an entry dated 2026-05-01. "
                             "Reopen the period or use a later date.", "fa")
    assert closed.startswith("دوره تا ⁨2026-06-30⁩ بسته است")
    assert localize_detail("This cheque is bounced.", "es") == "Este cheque está rechazado."
    assert localize_detail("A supplier named 'Paper Co' already exists (id 9). Use it, or send allow_duplicate=true "
                           "to create another one on purpose.", "fa") == "یک ⁨تأمین‌کننده⁩ با نام «⁨Paper Co⁩» قبلاً وجود دارد. همان را به کار ببرید."
    assert localize_detail("PO is closed; cannot receive against it.", "ar") == "أمر الشراء ⁨مغلق⁩؛ لا يمكن الاستلام عليه."
    assert localize_detail("Account not found", "fa") == "حساب پیدا نشد"
    assert localize_detail("You don't have permission to perform this action", "es") == "No tienes permiso para realizar esta acción"
