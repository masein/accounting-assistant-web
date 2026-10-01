"""The self-audit's findings read in the user's language: "Negative asset
balance: Cash" used to reach a Persian user in English, title and detail. Each
finding carries a FINDING_TEXT key and its values; GET /brain/audit/report
words it for the user (title/detail stay English on the finding itself)."""
from __future__ import annotations

import ast
import re
import string
from pathlib import Path

from app.services import audit_service
from app.services.audit_service import FINDING_TEXT, AuditFinding, AuditReport

LANGS = {"en", "fa", "es", "ar"}
SOURCE = Path(audit_service.__file__)


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_finding_reads_in_every_language_with_the_same_values():
    for key, text in FINDING_TEXT.items():
        for part in ("title", "detail"):
            assert set(text[part]) == LANGS, (key, part)
            fields = {lang: _fields(s) for lang, s in text[part].items()}
            assert len({frozenset(v) for v in fields.values()}) == 1, (key, part, fields)


def test_every_check_names_its_finding_and_gives_its_values():
    calls = [n for n in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8")))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "AuditFinding"]
    assert len(calls) >= 8
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "key" in kw and isinstance(kw["key"], ast.Constant), ast.unparse(call)[:80]
        key = kw["key"].value
        assert key in FINDING_TEXT, key
        given = {k.value for k in kw["params"].keys}
        wanted = _fields(FINDING_TEXT[key]["title"]["en"]) | _fields(FINDING_TEXT[key]["detail"]["en"])
        assert wanted <= given, (key, wanted - given)


def _negative_cash() -> AuditFinding:
    return AuditFinding(severity="warning", category="negative_balance", title="Negative asset balance: Cash",
                        detail="Account 1110 (Cash) has negative balance: -5,000", key="negative_asset",
                        params={"code": "1110", "name": "Cash", "balance": "-5,000"})


def test_a_finding_reads_in_persian_with_its_values_kept_whole():
    title, detail = _negative_cash().localized("fa")
    assert title == "مانده منفی دارایی: ⁨Cash⁩"
    # the minus stays in front of its number inside a right-to-left line
    assert "⁨-5,000⁩" in detail and detail.startswith("حساب ⁨1110⁩")
    assert _negative_cash().localized("es")[0] == "Saldo negativo de activo: Cash"
    # an unknown language, or a finding without a key, keeps its English wording
    assert _negative_cash().localized("de") == ("Negative asset balance: Cash", "Account 1110 (Cash) has negative balance: -5,000")
    plain = AuditFinding(severity="warning", category="x", title="T", detail="D")
    assert plain.localized("fa") == ("T", "D")


def test_the_audit_report_is_worded_for_the_user(auth_client, monkeypatch):
    from app.api import brain

    report = AuditReport(findings=[_negative_cash()])
    monkeypatch.setattr(audit_service, "run_full_audit", lambda db: report)
    monkeypatch.setattr(brain, "_preferred_report_language", lambda db, user: "fa")
    r = auth_client.get("/brain/audit/report")
    assert r.status_code == 200, r.text
    f = r.json()["findings"][0]
    assert f["key"] == "negative_asset" and f["title"].startswith("مانده منفی دارایی")
    assert re.search(r"[؀-ۿ]", f["detail"]) and "negative balance" not in f["detail"]

    monkeypatch.setattr(brain, "_preferred_report_language", lambda db, user: "en")
    f = auth_client.get("/brain/audit/report").json()["findings"][0]
    assert f["title"] == "Negative asset balance: Cash"
    assert f["detail"] == "Account 1110 (Cash) has a negative balance: -5,000"
