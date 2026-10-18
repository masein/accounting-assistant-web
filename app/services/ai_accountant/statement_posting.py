"""A bank statement posted by the user's own rules (scenarios AF2–AF6).

«مبالغی که علامت + دارند در بدهکار حساب بانک دی و بستانکار درآمد شهریه، و مبالغی که
علامت − دارند در بستانکار بانک و بدهکار مخارج مدرسه، کارمزد بانکی … به ترتیب تاریخ»
(the owner's father, 2026-10-18): which account each row's other side goes to, by
direction and by the words in its description, and a voucher a day. One card for the
whole statement; confirmed, one voucher per day (or per row), oldest first, each row
two lines with its own description, every row marked recorded on its statement. Undo
inside its window takes every voucher back and opens the rows again.

Only rows not yet in the books are posted: none already posted, matched or a
duplicate, none in a closed period, none that no rule covers. The card says what was
left out and why.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date as _date
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import plain
from app.models.bank_statement import BankStatement, BankStatementRow
from app.services.ai_accountant.base import BaseTool, Replaceable, ToolContext, ToolError
from app.services.ai_accountant.time_tools import _register

_CLOSED_STATUSES = ("matched", "duplicate", "skipped")


class StatementRule(BaseModel):
    contains: str = Field(..., min_length=1, max_length=80,
                          description="Words in the row's description that pick it, e.g. «کارمزد» for the bank's fees, "
                                      "«شهریه», 'interest'. Letters match in either Arabic or Persian form.")
    account_code: str = Field(..., min_length=1, max_length=32,
                              description="The account those rows' other side goes to (a code from search_accounts).")
    direction: Literal["in", "out", "any"] = Field(
        "any", description="in: money coming in (واریز, «+»); out: money going out (برداشت, «−»); any: both.")


class ProposePostStatementInput(Replaceable):
    statement_id: str = Field(..., description="The imported statement (the statement_id the import or review gave).")
    bank_account_code: str | None = Field(
        None, description="The bank side in the books, when the user names one («بانک دی»); "
                          "otherwise the statement's own bank account.")
    new_bank_name: str | None = Field(
        None, max_length=120,
        description="Instead of bank_account_code: the statement's bank has no account in the books and the user "
                    "wants one («بانک پارسیان»). It is made on confirm, a bank party with its own account, and the "
                    "statement's next imports find it. Never when the books have an account for that bank already.")
    money_in_account_code: str | None = Field(
        None, description="The other side for money coming in («+», واریز) that no rule picks: credited.")
    money_out_account_code: str | None = Field(
        None, description="The other side for money going out («−», برداشت) that no rule picks: debited.")
    rules: list[StatementRule] = Field(
        default_factory=list, description="Rows picked by the words in their description, before the in/out accounts: "
                                          "«کارمزد» to the bank-fees account.")
    per: Literal["day", "row"] = Field(
        "day", description="day: one voucher for each date, its rows in it («به تفکیک تاریخ»), the default; "
                           "row: a voucher for each row.")


def _fold(text: str | None) -> str:
    return (text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ").lower()


_FEE_WORDS = ("کارمزد", "fee", "commission", "charge")
_CARRIES_A_FEE = ("به همراه کارمزد", "همراه با کارمزد", "با احتساب کارمزد", "with fee", "including fee")


def is_fee(description: str | None) -> bool:
    """The bank's own fee: «کارمزد پایا …», «کارمزد پذیرنده شاپرک», «برداشت کارمزد دستور پرداخت ساتنا». Not a
    payment that carries one: «تراکنش به همراه کارمزد …» moved 2,000,000,000 in the school's statement, its fee a
    row of its own; a «کارمزد» rule took them all, 11.5 billion of fees (real model, 2026-10-18)."""
    d = _fold(description)
    return any(w in d for w in _FEE_WORDS) and not any(w in d for w in _CARRIES_A_FEE)


def _picks(rule: dict, description: str | None) -> bool:
    """A rule's words in a row's description; a fee word picks the bank's own fees only."""
    words = _fold(rule["contains"]).strip()
    if words in _FEE_WORDS:
        return is_fee(description)
    return words in _fold(description)


def _open(row: BankStatementRow) -> bool:
    return (row.created_transaction_id is None and row.matched_transaction_id is None
            and (row.recon_status or "unmatched") not in _CLOSED_STATUSES)


def _postable(db: Session, code: str | None) -> tuple[str, str] | None:
    """(code, name) of an account entries can be posted to, or None."""
    if not code:
        return None
    from app.models.account import Account, AccountLevel
    acc = db.execute(select(Account).where(Account.code == str(code).strip(), Account.is_active.is_(True))).scalars().first()
    if acc is None or acc.level == AccountLevel.GROUP:
        return None
    if db.execute(select(Account.id).where(Account.parent_id == acc.id).limit(1)).first() is not None:
        return None
    return acc.code, acc.name


def _bank_named(db: Session, name: str) -> tuple[str, str] | None:
    """The books' own account for the bank ``name`` means («بانک پارسیان» → «تنخواه گردان حساب بانک پارسیان», a
    Parsian bank party), or None: a second account for the same bank is never made (AF10)."""
    from app.services.statement_import import bank_account_choices, canonical_bank
    want = canonical_bank(name)
    folded = _fold(name).strip()
    for c in bank_account_choices(db):
        label = c.get("bank") or c.get("name") or ""
        if (want and canonical_bank(label) == want) or (_fold(c.get("name")).strip() == folded):
            found = _postable(db, c["code"])
            if found:
                return found
    return None


def _predicted_bank_code(db: Session) -> str:
    """The code a new bank account will take, for the card; the real one is made on confirm."""
    from app.services.ai_accountant.entity_create import predicted_bank_account_code
    return predicted_bank_account_code(db) or "NEW"      # a migrated chart's sub-ledger numbers it then


def plan(db: Session, stmt: BankStatement, *, bank: str, money_in: str | None, money_out: str | None,
         rules: list[dict], per: str, row_ids: list[str] | None = None) -> dict[str, Any]:
    """The vouchers the rules make of the statement's open rows, oldest first, and what is left out."""
    from app.services.period_service import get_closed_period
    locked = get_closed_period(db)
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)).scalars().all()
    if row_ids is not None:
        wanted = set(row_ids)
        rows = [r for r in rows if str(r.id) in wanted]
    left = {"in_the_books": 0, "closed_period": 0, "no_rule": 0}
    picked: list[tuple[BankStatementRow, str]] = []
    for r in rows:
        if not _open(r):
            left["in_the_books"] += 1
            continue
        if locked is not None and r.tx_date <= locked:
            left["closed_period"] += 1
            continue
        incoming = (r.credit or 0) > 0          # a bank statement's credit is money coming into the account
        other = None
        for rule in rules:
            if rule.get("direction", "any") in ("any", "in" if incoming else "out") and _picks(rule, r.description):
                other = rule["account_code"]
                break
        other = other or (money_in if incoming else money_out)
        if not other or other == bank:
            left["no_rule"] += 1
            continue
        picked.append((r, other))
    # oldest first; within a day in the bank's own order (a statement lists the newest first)
    picked.sort(key=lambda x: (x[0].tx_date, -x[0].row_index))
    groups: dict[Any, list] = defaultdict(list)
    for r, other in picked:
        groups[r.tx_date if per == "day" else r.id].append((r, other))
    vouchers = []
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for key in sorted(groups, key=lambda k: (groups[k][0][0].tx_date, -groups[k][0][0].row_index)):
        lines = []
        for r, other in groups[key]:
            amount = plain(r.credit if (r.credit or 0) > 0 else r.debit)
            desc = (r.description or "").strip()[:240] or None
            if (r.credit or 0) > 0:
                lines += [(bank, amount, 0, desc), (other, 0, amount, desc)]
            else:
                lines += [(other, amount, 0, desc), (bank, 0, amount, desc)]
            for code, dr, cr, _d in lines[-2:]:
                if dr:
                    totals[(code, "debit")] += dr
                if cr:
                    totals[(code, "credit")] += cr
        vouchers.append({"date": groups[key][0][0].tx_date.isoformat(),
                         "rows": [str(r.id) for r, _o in groups[key]], "lines": lines})
    return {"vouchers": vouchers, "left": left, "totals": dict(totals), "rows": len(picked)}


class ProposePostStatement(BaseTool):
    name = "propose_post_statement"
    category = "proposal"
    description = (
        "Post a bank statement's rows by the user's rules, all in one card: the bank side, the account money coming "
        "in goes to, the account money going out goes to, and rules by words in the description («کارمزد» to bank "
        "fees). One voucher per day by default («به تفکیک تاریخ»), oldest first; per='row' makes one per row. Only "
        "rows not yet in the books are posted; those already in them, in a closed period or covered by no rule are "
        "left out and the card says so. Use when the user attaches a statement (or names one already imported) and "
        "says how its rows go: «+ ها بدهکار بانک دی و بستانکار درآمد شهریه، − ها بدهکار مخارج و کارمزدها کارمزد "
        "بانکی». Resolve every account the user names with search_accounts first; if one isn't in the chart, ask "
        "with ask_with_choices among the closest, never guess a code."
    )
    InputSchema = ProposePostStatementInput

    async def run(self, ctx: ToolContext, args: ProposePostStatementInput) -> dict[str, Any]:
        from app.core.money import grouped
        from app.services.statement_import import bank_account_for_statement, statement_currency
        try:
            stmt = ctx.db.get(BankStatement, uuid.UUID(str(args.statement_id).strip()))
        except (ValueError, TypeError):
            stmt = None
        if stmt is None:
            raise ToolError("No bank statement with that id in these books.", code="not_found")
        new_bank = (args.new_bank_name or "").strip() or None
        if new_bank:
            same = _bank_named(ctx.db, new_bank)
            if same is not None:           # the books have it already: post there, never a second one (AF10)
                bank, new_bank = same, None
            else:
                bank = (_predicted_bank_code(ctx.db), f"{new_bank} (new)")
        else:
            bank = _postable(ctx.db, args.bank_account_code or bank_account_for_statement(ctx.db, stmt))
        if bank is None:
            raise ToolError("Say which bank account in the books this statement is (search_accounts for it): "
                            f"{args.bank_account_code or 'none is set for this statement'} can't take entries.",
                            code="no_bank_account")
        accounts = {"money_in": args.money_in_account_code, "money_out": args.money_out_account_code,
                    **{f"rule «{r.contains}»": r.account_code for r in args.rules}}
        names: dict[str, str] = {bank[0]: bank[1]}
        for what, code in accounts.items():
            if code is None:
                continue
            found = _postable(ctx.db, code)
            if found is None:
                raise ToolError(f"{code} ({what}) isn't an account entries can be posted to in this chart. Find the "
                                "right one with search_accounts, or ask the user with ask_with_choices.",
                                code="unknown_account")
            names[found[0]] = found[1]
        if not (args.money_in_account_code or args.money_out_account_code or args.rules):
            raise ToolError("Say where the rows go: money_in_account_code, money_out_account_code or rules.",
                            code="no_rules")
        rules = [r.model_dump() for r in args.rules]
        made = plan(ctx.db, stmt, bank=bank[0], money_in=args.money_in_account_code,
                    money_out=args.money_out_account_code, rules=rules, per=args.per)
        if not made["vouchers"]:
            left = made["left"]
            raise ToolError(f"Nothing to post: {left['in_the_books']} rows are already in the books, "
                            f"{left['closed_period']} in a closed period, {left['no_rule']} covered by no rule.",
                            code="nothing_to_post")
        currency = statement_currency(ctx.db, stmt)
        first, last = made["vouchers"][0]["date"], made["vouchers"][-1]["date"]
        persian = any("؀" <= ch <= "ۿ" for ch in (ctx.user_message or "") + (stmt.bank_name or ""))
        bank_label = _bank_label(stmt, persian)
        if persian:
            from app.utils.jalali import to_persian_digits
            description = f"صورتحساب {bank_label}: {to_persian_digits(made['rows'])} ردیف در {to_persian_digits(len(made['vouchers']))} سند"
        else:
            description = f"{bank_label} statement: {made['rows']} rows in {len(made['vouchers'])} voucher(s)"
        # the card's lines: what each account receives in all, debits first
        lines = sorted(({"account_code": code, "debit": amt if side == "debit" else 0,
                         "credit": amt if side == "credit" else 0}
                        for (code, side), amt in made["totals"].items()),
                       key=lambda ln: (ln["debit"] == 0, ln["account_code"]))
        total = sum(ln["debit"] for ln in lines)
        payload = {"statement_id": str(stmt.id), "bank_account_code": bank[0],
                   # a bank made on confirm (AF9): the card names it by the words the user gave
                   **({"new_bank_name": new_bank, "new_account": True, "account_code": bank[0], "place": f"{new_bank} (تازه)"
                       if persian else f"{new_bank} (new)"} if new_bank else {}),
                   "money_in_account_code": args.money_in_account_code,
                   "money_out_account_code": args.money_out_account_code, "rules": rules, "per": args.per,
                   "row_ids": [rid for v in made["vouchers"] for rid in v["rows"]],
                   "vouchers": len(made["vouchers"]), "left": made["left"], "first_date": first, "last_date": last,
                   "date": first, "currency": currency, "amount": total, "description": description, "lines": lines}
        token = _register(ctx, self.name, payload)
        per_account = "; ".join(f"{'Dr' if ln['debit'] else 'Cr'} {ln['account_code']} {names.get(ln['account_code'], '')}: "
                                f"{grouped(ln['debit'] or ln['credit'])}" for ln in lines)
        left = made["left"]
        summary = (f"Post {made['rows']} rows of the {bank_label} statement as {len(made['vouchers'])} voucher(s) "
                   f"({'one per day' if args.per == 'day' else 'one per row'}), {first} to {last}, {currency}: "
                   f"{per_account}. Left out: {left['in_the_books']} already in the books, {left['closed_period']} in a "
                   f"closed period, {left['no_rule']} covered by no rule.")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


def _bank_label(stmt: BankStatement, persian: bool) -> str:
    from app.services.ai_accountant.statement_intake import bank_label
    return bank_label(stmt.bank_name, "fa" if persian else "en") or ("بانک" if persian else "bank")


def execute_statement_posting(db: Session, proposal, *, actor_user_id: str, actor_username: str | None,
                              ip_address: str | None) -> tuple[str | None, str]:
    """The card confirmed: its vouchers posted, oldest first, every row marked recorded. One database
    transaction: a voucher that can't post leaves none posted."""
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ai_accountant.time_execute import _audit
    from app.services.book_text import bt
    from app.services.ledger_posting import create_transaction_from_payload
    from app.services.locale_service import get_display_calendar
    from app.services.ai_accountant.blocks import display_date

    p = dict(proposal.tool_input or {})
    stmt = db.get(BankStatement, uuid.UUID(p["statement_id"]))
    if stmt is None:
        raise ValueError("that statement is no longer in these books")
    bank_code = p["bank_account_code"]
    new_bank = None
    if p.get("new_bank_name"):
        # the statement's bank, made now: a bank party with its own account; its next statements find it (AF9)
        from app.services.ai_accountant.entity_create import create_entity
        made_bank = create_entity(db, name=p["new_bank_name"], type_="bank")
        bank_code, new_bank = made_bank.account_code, {"entity_id": str(made_bank.entity.id), "code": made_bank.account_code,
                                                       "created": made_bank.created}
        stmt.bank_account_code = bank_code
    made = plan(db, stmt, bank=bank_code, money_in=p.get("money_in_account_code"),
                money_out=p.get("money_out_account_code"), rules=p.get("rules") or [], per=p.get("per") or "day",
                row_ids=p.get("row_ids"))
    if not made["vouchers"]:
        raise ValueError("every row on this card is in the books already")
    from app.services.book_text import book_language
    lang = book_language(db)
    calendar = get_display_calendar(db)
    bank_label = _bank_label(stmt, lang == "fa")
    rows = {str(r.id): r for r in db.execute(select(BankStatementRow).where(
        BankStatementRow.id.in_([uuid.UUID(x) for v in made["vouchers"] for x in v["rows"]]))).scalars()}
    txn_ids = []
    for v in made["vouchers"]:
        on = _date.fromisoformat(v["date"])
        txn = create_transaction_from_payload(db, TransactionCreate(
            date=on,
            reference=f"BS-{str(stmt.id)[:6]}-{on:%Y%m%d}" + ("" if (p.get("per") or "day") == "day" else f"-{len(txn_ids) + 1}"),
            description=bt(db, "stmt_day", bank=bank_label, date=display_date(v["date"], calendar, lang) or v["date"]),
            currency=p.get("currency") or stmt.currency,
            lines=[TransactionLineCreate(account_code=code, debit=dr, credit=cr, line_description=desc)
                   for code, dr, cr, desc in v["lines"]],
        ))
        for rid in v["rows"]:
            row = rows[rid]
            row.created_transaction_id = txn.id
            row.recon_status = "matched"
            row.user_approved = True
        txn_ids.append(str(txn.id))
    stmt.status = "approved" if all(
        r.recon_status in _CLOSED_STATUSES for r in db.execute(
            select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)).scalars()) else "reviewing"
    audit_id = _audit(db, proposal, entity_type="statement_posting", entity_id=str(stmt.id),
                      detail={"transaction_ids": txn_ids, "transaction_id": txn_ids[0], "rows": made["rows"],
                              "vouchers": len(txn_ids), **({"new_bank": new_bank} if new_bank else {})},
                      actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)
    return txn_ids[0], audit_id


def undo_statement_posting(db: Session, audit, *, actor_user_id: str, actor_username: str | None,
                           ip_address: str | None, delete: bool, tool_name: str):
    """Every voucher of a statement posting taken back: soft-deleted inside the undo window and an open
    period, else reversed; their rows open again on the statement."""
    import json
    from datetime import datetime, timezone

    from app.models.audit_log import AuditLog
    from app.models.transaction import Transaction
    from app.services.ai_accountant.execute_service import UndoNotApplicable, UndoResult, _already_reversed
    from app.services.book_text import bt
    from app.services.period_service import get_closed_period
    from app.services.reporting.ledger_service import LedgerService
    from app.services.statement_import import release_statement_rows

    ids = (json.loads(audit.detail or "{}") or {}).get("transaction_ids") or []
    live = [t for t in (db.get(Transaction, uuid.UUID(i)) for i in ids)
            if t is not None and t.deleted_at is None and not _already_reversed(db, str(t.id))]
    if not live:
        raise UndoNotApplicable("These vouchers have already been undone or reversed.")
    locked = get_closed_period(db)
    mode = "deleted" if delete else "reversed"
    last_reversal = None
    for txn in live:
        release_statement_rows(db, txn.id)
        if delete and not (locked is not None and txn.date <= locked):
            txn.deleted_at = datetime.now(timezone.utc)
        else:
            mode = "reversed"
            last_reversal = LedgerService(db).reverse_journal_entry(
                transaction_id=txn.id, reverse_date=None, reference=f"REVERSAL of {txn.reference or txn.id}",
                description=bt(db, "reversed_statement_day", reference=txn.reference or ""), mark_undo=False,
            ).transaction_id
        db.add(AuditLog(action="undo", entity_type="transaction", entity_id=str(txn.id), user_id=actor_user_id,
                        username=actor_username, ip_address=ip_address, actor_source="ai-assistant",
                        session_id=audit.session_id, tool_name=tool_name, confirmation_token=audit.confirmation_token,
                        user_message=audit.user_message, detail=json.dumps({"statement_posting": str(audit.id)})))
    stmt = db.get(BankStatement, uuid.UUID(str(audit.entity_id)))
    if stmt is not None:
        stmt.status = "reviewing"
    db.commit()
    return UndoResult(original_transaction_id=str(live[0].id),
                      reversal_transaction_id=str(last_reversal) if last_reversal else None,
                      audit_log_id=str(audit.id), mode=mode)


def context_for_model(db: Session, statement_id: str) -> str:
    """What the model reads about a statement just imported from the turn's file, so the user's rules
    («+ ها بدهکار بانک دی …») can become one propose_post_statement card."""
    from app.services.statement_import import bank_account_for_statement, statement_currency
    try:
        stmt = db.get(BankStatement, uuid.UUID(str(statement_id)))
    except (ValueError, TypeError):
        stmt = None
    if stmt is None:
        return ""
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)).scalars().all()
    open_rows = [r for r in rows if _open(r)]
    ins = [r for r in open_rows if (r.credit or 0) > 0]
    outs = [r for r in open_rows if (r.debit or 0) > 0]
    fees = [r for r in open_rows if (r.debit or 0) > 0 and is_fee(r.description)]
    from app.services.statement_import import resolve_bank_account
    bank, how = resolve_bank_account(db, stmt)
    bank_name = (_postable(db, bank) or (bank, ""))[1] if bank else ""
    own = _bank_named(db, stmt.bank_name or "") if (stmt.bank_name or "Unknown") != "Unknown" else None
    from app.services.ai_accountant.statement_intake import bank_label
    fa_bank = bank_label(stmt.bank_name, "fa") or stmt.bank_name
    if own is not None:
        whose = f" The books' own account for this bank: {own[0]} {own[1]}."
    elif how == "default" and (stmt.bank_name or "Unknown") != "Unknown":
        whose = (f" The books have NO account for {stmt.bank_name} («{fa_bank}»): the statement fell back to the chart's "
                 f"bank account. If the rows are that bank's, offer to make one (new_bank_name «بانک {fa_bank}»), and use "
                 "an account the user names otherwise.")
    else:
        whose = ""
    dates = sorted(r.tx_date for r in rows)
    from app.utils.jalali import format_jalali_long
    span = (f"from {dates[0]} ({format_jalali_long(dates[0])}) to {dates[-1]} ({format_jalali_long(dates[-1])})"
            if dates else "")
    return (f"[The attached file was imported as a bank statement: statement_id={stmt.id}, bank "
            f"{stmt.bank_name or 'unknown'}, {len(rows)} rows {span} (quote these dates, don't convert), currency "
            f"{statement_currency(db, stmt)}. Not yet in the books: "
            f"{len(ins)} rows of money in totalling {sum(plain(r.credit) for r in ins)}, {len(outs)} rows of money "
            f"out totalling {sum(plain(r.debit) for r in outs)}; {len(fees)} of the money-out rows are the bank's own "
            f"fees, totalling {sum(plain(r.debit) for r in fees)} (a rule on «کارمزد» picks those, never «تراکنش به همراه "
            "کارمزد», a payment that carries a fee). "
            f"The statement's own bank account in the books: {bank or 'none set'} {bank_name}.{whose} To post its rows by "
            "the user's rules, call propose_post_statement with this statement_id, after search_accounts for every "
            "account the user names.]")
