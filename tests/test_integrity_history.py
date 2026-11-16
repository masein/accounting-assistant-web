"""The integrity history records each check as it went (chat review 2026-11-10, item 25, scenario CG1).

Each check's row in the history was matched to findings by title, and six of the seven titles never contained
their check's name («Duplicate payments» against «Potential duplicate payment»): those checks were "pass" always.
"""
from __future__ import annotations

from sqlalchemy import select

from app.services import audit_service
from app.services.audit_service import AuditFinding


def _finding(title):
    return AuditFinding(severity="warning", category="duplicate", title=title, detail="d", key="k", params={})


def test_cg1_each_check_records_its_own_findings(db, monkeypatch):
    from app.models.audit_log import IntegrityCheck
    monkeypatch.setattr(audit_service, "detect_duplicate_payments", lambda d: [_finding("Potential duplicate payment")])
    monkeypatch.setattr(audit_service, "detect_backdated_entries", lambda d: [_finding("Backdated entry"), _finding("Backdated entry")])
    def broken(d):
        raise RuntimeError("boom")
    monkeypatch.setattr(audit_service, "detect_negative_balances", broken)
    before = {r.id for r in db.execute(select(IntegrityCheck)).scalars()}
    audit_service.run_full_audit(db)
    rows = {r.check_type: r for r in db.execute(select(IntegrityCheck)).scalars() if r.id not in before}
    assert rows["duplicate_payments"].status == "warning" and rows["duplicate_payments"].score == 90
    assert rows["backdated_entries"].status == "warning" and rows["backdated_entries"].score == 80
    assert rows["negative_balances"].status == "warning"                     # the check itself failed
    assert "Check failed" in rows["negative_balances"].detail


def test_cg2_a_bank_in_persian_words():
    from app.services.ai_accountant.statement_intake import bank_label
    assert bank_label("Day", "fa") == "بانک دی"            # the reader's spelling; «Day» stood in a Persian sentence
    assert bank_label("Mellat", "fa") == "ملت" and bank_label("Day", "en") == "Day"
    assert bank_label("Unknown", "fa") is None


def test_cg3_not_recorded_until_confirmed():
    from app.services.ai_accountant.orchestrator import _not_recorded_yet
    card = [{"confirmation_token": "t", "status": "pending"}]
    assert _not_recorded_yet("حقوق ۳۰ میلیون تومان به حسابت ثبت شد. لطفاً کارت را تأیید کنید.", card) == \
        "حقوق ۳۰ میلیون تومان به حسابت آماده شد. لطفاً کارت را تأیید کنید."
    assert _not_recorded_yet("هزینه را ثبت کردم.", card) == "هزینه را آماده کردم."
    assert _not_recorded_yet("The lunch has been recorded.", card) == "The lunch is ready to confirm."
    # words about entries already in the books stay, and so does everything when no card was made
    assert _not_recorded_yet("موجودی‌های ثبت‌شده این‌هاست؛ سه هزینهٔ ثبت شده دیروز.", card) == \
        "موجودی‌های ثبت‌شده این‌هاست؛ سه هزینهٔ ثبت شده دیروز."
    assert _not_recorded_yet("دیروز ثبت شد.", []) == "دیروز ثبت شد."
