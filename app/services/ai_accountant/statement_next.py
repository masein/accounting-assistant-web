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

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LINES = {
    "left": {"en": lambda n: f"{n} more to record after this one." if n else "This is the last one to record.",
             "fa": lambda n: f"پس از این، {n} ردیف دیگر مانده." if n else "این آخرین ردیفی است که باید ثبت شود.",
             "es": lambda n: f"Quedan {n} por registrar después de esta." if n else "Es la última por registrar.",
             "ar": lambda n: f"يبقى {n} بعد هذا." if n else "هذا آخر ما يجب تسجيله."},
    # after a card of many rows: «this is the last row» read as if the card were one row (CD1)
    "left_group": {"en": lambda n: f"{n} more to record after this card." if n else "Nothing more to record after this card.",
                   "fa": lambda n: f"پس از این کارت، {n} ردیف دیگر مانده." if n else "پس از این کارت، ردیف دیگری نمی‌ماند.",
                   "es": lambda n: f"Quedan {n} por registrar después de esta tarjeta." if n else "No queda nada más tras esta tarjeta.",
                   "ar": lambda n: f"يبقى {n} بعد هذه البطاقة." if n else "لا يبقى شيء بعد هذه البطاقة."},
    "done": {"en": "Every row of this statement is in the books, or on a card above waiting for you.",
             "fa": "همهٔ ردیف‌های این صورت‌حساب در دفاتر است، یا روی کارتی بالاتر منتظر شماست.",
             "es": "Todas las filas de este extracto están en los libros, o en una tarjeta arriba esperándote.",
             "ar": "كل أسطر هذا الكشف في الدفاتر، أو على بطاقة أعلاه بانتظارك."},
    # the last word when not everything is in the books (BN4): «همهٔ ردیف‌ها در دفاتر است» was said of a
    # statement with dozens of deposits missing and the bank short of it (chat review 2026-11-10)
    "look": {"en": lambda n: f"{n} rows still need a look: each resembles an entry already in the books, but its date "
                             f"or narration differs. Confirm or record them on the bank statement page.",
             "fa": lambda n: f"{n} ردیف هنوز نیاز به بررسی دارد: هر کدام شبیه سندی در دفاتر است، ولی تاریخ یا شرحش فرق "
                             f"دارد. آن‌ها را در صفحهٔ صورت‌حساب بانکی تأیید یا ثبت کنید.",
             "es": lambda n: f"Quedan {n} filas por revisar: cada una se parece a un asiento ya en los libros, pero su fecha "
                             f"o concepto difiere. Confírmalas o regístralas en la página del extracto.",
             "ar": lambda n: f"يبقى {n} من الأسطر للمراجعة: كلٌّ منها يشبه قيدًا في الدفاتر لكن تاريخه أو بيانه مختلف. "
                             f"أكّدها أو سجّلها في صفحة كشف الحساب."},
    "gap": {"en": "The statement closes at {closing}; the books show {book} on {code} {name}.",
            "fa": "ماندهٔ پایانی صورت‌حساب {closing} است و دفاتر برای {code} {name} مانده {book} را نشان می‌دهند.",
            "es": "El extracto cierra en {closing}; los libros muestran {book} en {code} {name}.",
            "ar": "يُقفل الكشف عند {closing}، والدفاتر تُظهر {book} في {code} {name}."},
    # Next pressed again before the card above was confirmed: a second card was made, confirming the first moved on
    # past it, and its row was never posted (chat review 2026-11-10, scenario CA1)
    "waiting": {"en": "The card above for row {row} is still waiting: confirm or cancel it, then go on.",
                "fa": "کارت ردیف {row} بالاتر هنوز منتظر است: تأییدش کنید یا لغوش کنید، بعد ادامه دهید.",
                "es": "La tarjeta de la fila {row}, arriba, sigue esperando: confírmala o cancélala y luego sigue.",
                "ar": "بطاقة السطر {row} أعلاه ما زالت بانتظارك: أكّدها أو ألغها ثم تابع."},
    # rows like the one just recorded, in one card (chat review 2026-11-10, scenario CD1): 26 card-to-card tuition
    # rows of one day were 26 confirms
    "group": {"en": "{n} more rows are like the last one («{words}»): all to «{account}», one voucher per day, on one card. "
                    "If one of them is different, cancel this card and they come one at a time.",
              "fa": "{n} ردیف دیگر مثل ردیف قبل است («{words}»): همه با «{account}»، یک سند برای هر روز، در یک کارت. "
                    "اگر یکی فرق دارد، این کارت را لغو کنید تا ردیف‌ها تک‌تک بیایند.",
              "es": "{n} filas más son como la anterior («{words}»): todas a «{account}», un asiento por día, en una tarjeta. "
                    "Si alguna es distinta, cancela esta tarjeta y vendrán de una en una.",
              "ar": "{n} أسطر أخرى مثل السطر السابق («{words}»): كلها إلى «{account}»، قيد لكل يوم، في بطاقة واحدة. "
                    "إن كان أحدها مختلفًا فألغِ هذه البطاقة لتأتي واحدًا واحدًا."},
    "choose": {"en": "Which of your bank accounts is this statement's? It was compared with {code} {name}, the chart's "
                     "own bank account, because none of your banks matched it. Choose its account before any row is recorded.",
               "fa": "این صورت‌حساب مربوط به کدام حساب بانکی شماست؟ هیچ‌کدام از بانک‌های دفاتر با آن جور نشد و با {code} {name}، "
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
    waiting = _waiting_row(db, stmt, user_id, str(session.id))
    if waiting is not None:
        return NextDifference(**base, text=said("waiting", lang, row=waiting))
    grouped_card = await _group_card(db, stmt, session_id=str(session.id), user_id=user_id, username=username, lang=lang)
    if grouped_card is not None:
        return NextDifference(**base, **grouped_card)
    result = await ensure_statement_row_proposal(
        db, user_id=user_id, username=username, session_id=str(session.id), user_message="next",
        tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(stmt.id)}}])
    if result is None:
        return NextDifference(**base, text=_last_word(db, stmt, user_id, str(session.id), lang))
    try:
        result.update(guardrails.review(db, result))
    except guardrails.ProposalRefused as e:
        from app.core.messages import localize_detail
        return NextDifference(**base, text=localize_detail(str(e), lang), refused=True)
    pending = _pending_row_ids(db, user_id, str(session.id))
    left = sum(1 for f in build_statement_review(db, stmt).findings
               if f.kind == "unrecorded" and f.suggested_fix == "post_row" and f.row_id and str(f.row_id) not in pending)
    return NextDifference(**base, text=said("left", lang, left), proposal=result, left=left)


GROUP_MIN = 2                                    # rows besides the one just recorded


def narration_pattern(text: str | None) -> str:
    """What rows alike share: the first three words of the narration, its numbers (card numbers, references,
    dates) gone — «انتقال کارت به کارت از ۶۰۳۷…» is «انتقال کارت به». Empty when fewer than two words are left."""
    import re
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").lower()
    t = re.sub(r"[0-9۰-۹٠-٩]+", " ", t)
    t = re.sub(r"[^\w\s]|_", " ", t)
    words = [w for w in t.split() if len(w) > 1]
    return " ".join(words[:3]) if len(words) >= 2 else ""


async def _group_card(db: Session, stmt, *, session_id: str, user_id: str, username: str | None,
                      lang: str) -> dict | None:
    """After a row's card was confirmed in this conversation: one card for the open rows like it (the same direction
    and narration pattern), with the account that card had, a voucher per day (CD1). Rows of a group card the user
    cancelled come one at a time (CD2)."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.account import Account
    from app.models.ai_accountant import AIProposal
    from app.models.bank_statement import BankStatementRow
    from app.services.ai_accountant import guardrails
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.statement_intake import _pending_row_ids
    from app.services.ai_accountant.statement_posting import ProposePostStatement, ProposePostStatementInput
    from app.services.statement_import import bank_account_for_statement
    from app.services.statement_review import build_statement_review

    cards = db.execute(select(AIProposal).where(
        AIProposal.session_id == session_id, AIProposal.user_id == str(user_id),
        AIProposal.tool_name.in_(("propose_create_transaction", "propose_post_statement")))
        .order_by(AIProposal.created_at.desc()).limit(200)).scalars().all()
    rows = {str(r.id): r for r in db.execute(select(BankStatementRow).where(
        BankStatementRow.statement_id == stmt.id)).scalars()}
    declined = {str(r) for c in cards if c.tool_name == "propose_post_statement" and c.status == "cancelled"
                for r in (c.tool_input or {}).get("row_ids") or []}
    last = next((c for c in cards if c.tool_name == "propose_create_transaction" and c.status == "executed"
                 and str((c.tool_input or {}).get("bank_statement_row_id")) in rows), None)
    if last is None:
        logger.info("no group: no confirmed row card in this conversation")
        return None
    row = rows[str(last.tool_input["bank_statement_row_id"])]
    pattern = narration_pattern(row.description)
    bank = bank_account_for_statement(db, stmt)
    others = [ln.get("account_code") for ln in (last.tool_input or {}).get("lines") or [] if ln.get("account_code") != bank]
    if not pattern or not bank or len(set(others)) != 1:
        logger.info("no group: pattern %r, bank %r, other accounts %r", pattern, bank, others)
        return None
    account = others[0]
    incoming = (row.credit or 0) > 0
    pending = _pending_row_ids(db, user_id, session_id)
    open_ids = {str(f.row_id) for f in build_statement_review(db, stmt).findings
                if f.kind == "unrecorded" and f.suggested_fix == "post_row" and f.row_id}
    alike = [r for rid, r in rows.items() if rid in open_ids and rid not in pending and rid not in declined
             and rid != str(row.id) and ((r.credit or 0) > 0) == incoming and narration_pattern(r.description) == pattern]
    if len(alike) < GROUP_MIN:
        logger.info("no group: %d alike (open %d, pending %d, declined %d)", len(alike), len(open_ids), len(pending),
                    len(declined))
        return None
    alike.sort(key=lambda r: (r.tx_date, -r.row_index))
    ctx = ToolContext(db=db, user_id=user_id, username=username, chat_session_id=session_id,
                      user_message="ردیف‌های مشابه" if lang == "fa" else "rows alike")
    try:
        result = await ProposePostStatement().run(ctx, ProposePostStatementInput(
            statement_id=str(stmt.id), bank_account_code=bank, per="day", row_ids=[str(r.id) for r in alike],
            **({"money_in_account_code": account} if incoming else {"money_out_account_code": account})))
        result.update(guardrails.review(db, result))
    except (ToolError, guardrails.ProposalRefused, ValueError) as e:
        logger.info("no group: %s %s", type(e).__name__, getattr(e, "code", "") or str(e)[:200])
        return None
    name = db.execute(select(Account.name).where(Account.code == account)).scalar() or account
    pending = _pending_row_ids(db, user_id, session_id)
    left = sum(1 for rid in open_ids if rid not in pending)
    text = said("group", lang, n=len(alike), words=pattern, account=name)
    return {"text": text + " " + said("left_group", lang, left), "proposal": result, "left": left}


def _waiting_row(db: Session, stmt, user_id: str, session_id: str) -> int | None:
    """The row number of this statement's card still waiting in this conversation, if any."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.bank_statement import BankStatementRow
    from app.services.ai_accountant.statement_intake import _pending_row_ids
    ids = []
    for rid in _pending_row_ids(db, user_id, session_id):
        try:
            ids.append(_uuid.UUID(rid))
        except ValueError:
            continue
    if not ids:
        return None
    return db.execute(select(BankStatementRow.row_index).where(
        BankStatementRow.statement_id == stmt.id, BankStatementRow.id.in_(ids),
        BankStatementRow.created_transaction_id.is_(None)).order_by(BankStatementRow.row_index).limit(1)).scalar()


def _last_word(db: Session, stmt, user_id: str, session_id: str, lang: str) -> str:
    """Nothing left to card: «every row is in the books» only when it is so (BN4) — rows that merely resemble
    an entry, and a closing balance the books don't reach, are said instead."""
    from app.core.money import grouped
    from app.services.ai_accountant.statement_intake import _pending_row_ids
    from app.services.statement_review import build_statement_review

    review = build_statement_review(db, stmt)
    pending = _pending_row_ids(db, user_id, session_id)
    if any(f.kind == "unrecorded" and f.row_id and str(f.row_id) in pending for f in review.findings):
        return said("done", lang)                       # cards above are waiting: the gap is theirs until then
    parts = []
    look = sum(1 for f in review.findings if f.kind in ("needs_confirmation", "amount_mismatch"))
    if look:
        parts.append(said("look", lang, look))
    bal = review.balance
    if bal is not None and bal.gap:
        parts.append(said("gap", lang, closing=grouped(bal.statement_closing), book=grouped(bal.book_balance),
                          code=bal.bank_account_code or "", name=review.bank_account_name or ""))
    return " ".join(parts) if parts else said("done", lang)
