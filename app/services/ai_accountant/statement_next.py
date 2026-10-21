"""A bank statement, one difference at a time, without the model: the phone's Next (roadmap
ROADMAP_ANDROID_CHAT P2.3, scenario N31) and the web's «رفع قدم‌به‌قدم» (scenario AJ13).

The web's button used to send the model a message; it raised a card for the first row and stopped
there, and once that card was confirmed nothing came next (the owner, 2026-10-21). Both now take the
next row the books don't have through ``ensure_statement_row_proposal`` — the same proposal tool,
checks and Confirm gate as the accountant's — and say how many are left.

When the statement's account is only the chart's default while the books hold other banks («Day» once
found no «بانک دی» and was compared with 1110), nothing is proposed: the account is chosen first, so no
row is posted to a guess (scenario AJ12)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

LINES = {
    "left": {"en": lambda n: f"{n} more to record after this one." if n else "This is the last one to record.",
             "fa": lambda n: f"پس از این، {n} ردیف دیگر مانده." if n else "این آخرین ردیفی است که باید ثبت شود.",
             "es": lambda n: f"Quedan {n} por registrar después de esta." if n else "Es la última por registrar.",
             "ar": lambda n: f"يبقى {n} بعد هذا." if n else "هذا آخر ما يجب تسجيله."},
    "done": {"en": "Every row of this statement is in the books, or on a card above waiting for you.",
             "fa": "همهٔ ردیف‌های این صورتحساب در دفاتر است، یا روی کارتی بالاتر منتظر شماست.",
             "es": "Todas las filas de este extracto están en los libros, o en una tarjeta arriba esperándote.",
             "ar": "كل أسطر هذا الكشف في الدفاتر، أو على بطاقة أعلاه بانتظارك."},
    "choose": {"en": "Which of your bank accounts is this statement's? It was compared with {code} {name}, the chart's "
                     "own bank account, because none of your banks matched it. Choose its account before any row is recorded.",
               "fa": "این صورتحساب مال کدام حساب بانکی شماست؟ هیچ‌کدام از بانک‌های دفاتر با آن جور نشد و با {code} {name}، "
                     "حساب بانکی پیش‌فرض، مقایسه شد. پیش از ثبت هر ردیف، حسابش را انتخاب کنید.",
               "es": "¿De cuál de tus cuentas bancarias es este extracto? Se comparó con {code} {name}, la cuenta bancaria "
                     "por defecto, porque ninguno de tus bancos coincidió. Elige su cuenta antes de registrar ninguna fila.",
               "ar": "لأيّ من حساباتك البنكية هذا الكشف؟ قورن بالحساب {code} {name}، الحساب البنكي الافتراضي، لأن أيًّا من "
                     "بنوكك لم يطابقه. اختر حسابه قبل تسجيل أي سطر."},
}


def said(key: str, lang: str, *args, **kw) -> str:
    table = LINES[key]
    text = table.get(lang, table["en"])
    text = text(*args) if callable(text) else text.format(**kw)
    if lang == "fa":
        from app.services.documents.formatting import to_persian_digits
        text = to_persian_digits(text)
    return text


@dataclass
class NextDifference:
    statement_id: str
    session_id: str
    text: str
    proposal: dict[str, Any] | None = None       # the card, as the proposal tool made it
    refused: bool = False                        # the guardrails refused the card: ``text`` says why
    left: int = 0                                # rows still to record after this one
    choose: bool = False                         # the statement's account must be chosen first
    choices: list[dict] = field(default_factory=list)


async def next_difference(db: Session, *, stmt, session, user_id: str, username: str | None, lang: str) -> NextDifference:
    """The next row of ``stmt`` the books don't have, as a card to confirm, in ``session``."""
    from app.services.ai_accountant import guardrails
    from app.services.ai_accountant.statement_intake import _pending_row_ids, ensure_statement_row_proposal
    from app.services.statement_import import bank_account_choices, bank_account_guessed, resolve_bank_account
    from app.services.statement_review import build_statement_review

    base = dict(statement_id=str(stmt.id), session_id=str(session.id))
    if bank_account_guessed(db, stmt):
        from app.models.account import Account
        from sqlalchemy import select
        code, _how = resolve_bank_account(db, stmt)
        name = db.execute(select(Account.name).where(Account.code == code)).scalar() if code else ""
        return NextDifference(**base, text=said("choose", lang, code=code or "", name=name or ""), choose=True,
                              choices=bank_account_choices(db))
    result = await ensure_statement_row_proposal(
        db, user_id=user_id, username=username, session_id=str(session.id), user_message="next",
        tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(stmt.id)}}])
    if result is None:
        return NextDifference(**base, text=said("done", lang))
    try:
        result.update(guardrails.review(db, result))
    except guardrails.ProposalRefused as e:
        from app.core.messages import localize_detail
        return NextDifference(**base, text=localize_detail(str(e), lang), refused=True)
    pending = _pending_row_ids(db, user_id, str(session.id))
    left = sum(1 for f in build_statement_review(db, stmt).findings
               if f.kind == "unrecorded" and f.suggested_fix == "post_row" and f.row_id and str(f.row_id) not in pending)
    return NextDifference(**base, text=said("left", lang, left), proposal=result, left=left)
