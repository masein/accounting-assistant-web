"""Suggest an account for a bank-statement row.

Replaces the old keyword table in ``bank_statement_parser.classify_transaction``,
which mapped narrations to *hardcoded Iranian codes* — so a UK or personal chart
got suggestions pointing at accounts it does not have.

Two signals, strongest first:

1. **History** — what account did this tenant last use for a narration like this?
   Self-improving and chart-agnostic by construction: it can only ever return an
   account already in use. A bank narration repeats near-verbatim month to month
   ("POS-XXXX SNAPP", "TESCO STORES"), which is what makes this work.
2. **Keywords** — a bilingual merchant/narration map to a semantic category,
   resolved against *this tenant's* chart by account-name hints and statement
   nature (a debit row can only land on an expense account, a credit row on
   revenue). Nothing is returned unless the account actually exists here.

No LLM: this runs on every imported row, must be deterministic for the review
UI, and has to work offline in an air-gapped deployment.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code

# Persian and Arabic-Indic digits → ASCII. Defined here rather than imported
# from ocr_extract, which drags in the vision/PDF stack this module never needs.
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# How many past transactions to consider when learning from history.
HISTORY_LIMIT = 500
# Token overlap (Jaccard) above which two narrations are "the same merchant".
FUZZY_THRESHOLD = 0.6

CONFIDENCE_HISTORY_EXACT = 0.95
CONFIDENCE_HISTORY_FUZZY = 0.8
CONFIDENCE_KEYWORD = 0.6


@dataclass(frozen=True)
class CategorySuggestion:
    account_code: str
    account_name: str
    category: str
    confidence: float
    source: str  # "history" | "keyword"


# --- Narration → semantic category -----------------------------------------
# Keys are internal category ids; values are substrings matched against the
# normalized narration. Persian and English side by side because Iranian bank
# statements mix both, often with Latin merchant names inside Persian text.
_MERCHANT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "transport": (
        "snapp", "اسنپ", "tapsi", "تپسی", "taxi", "تاکسی", "uber", "metro", "مترو",
        "bus", "اتوبوس", "fuel", "petrol", "بنزین", "پمپ بنزین", "parking", "پارکینگ",
        "railway", "قطار", "airline", "هواپیمای", "پرواز", "flight",
        "ایاب", "ذهاب", "سفر",
    ),
    "groceries": (
        "supermarket", "سوپرمارکت", "سوپر", "hyperstar", "هایپر", "grocery", "بقالی",
        "نانوایی", "bakery", "قصابی", "butcher", "میوه", "tesco", "sainsbury", "aldi",
        "lidl", "asda", "waitrose", "فروشگاه",
    ),
    "dining": (
        "restaurant", "رستوران", "cafe", "کافه", "coffee", "قهوه", "fast food",
        "پیتزا", "pizza", "برگر", "burger", "starbucks", "snappfood", "اسنپ فود",
        "چلوکباب", "food",
    ),
    "utilities": (
        "قبض", "bill", "electric", "برق", "gas", "گاز", "water", "آب",
        "telecom", "مخابرات", "irancell", "ایرانسل", "همراه اول", "hamrah",
        "internet", "اینترنت", "شارژ", "top-up", "topup", "vodafone", "bt group",
    ),
    "rent": ("rent", "اجاره", "اجاره‌بها", "landlord", "موجر", "رهن"),
    "health": (
        "pharmacy", "داروخانه", "hospital", "بیمارستان", "clinic", "کلینیک",
        "doctor", "دکتر", "پزشک", "دندانپزشک", "dental", "بیمه درمان", "azmayeshgah",
        "آزمایشگاه", "nhs",
    ),
    "education": (
        "school", "مدرسه", "university", "دانشگاه", "آموزشگاه", "کلاس", "course",
        "tuition", "شهریه", "کتاب", "book shop", "bookstore",
    ),
    "clothing": ("clothing", "پوشاک", "لباس", "boutique", "بوتیک", "کفش", "shoe", "zara", "h&m"),
    "subscriptions": (
        "subscription", "اشتراک", "netflix", "spotify", "filimo", "فیلیمو",
        "namava", "نماوا", "google", "apple.com", "icloud", "microsoft", "adobe",
    ),
    "gifts": ("gift", "هدیه", "کادو", "گل فروشی", "florist", "charity", "خیریه", "کمک"),
    "bank_fee": (
        "fee", "کارمزد", "commission", "charge", "هزینه بانک", "service charge",
        "overdraft", "interest charged",
    ),
    "loan": ("loan", "وام", "قسط", "installment", "instalment", "تسهیلات", "mortgage"),
    # Credit-side (money in)
    "salary": ("salary", "حقوق", "payroll", "دستمزد", "wages", "مزد", "pay run"),
    "interest_income": ("interest", "سود سپرده", "سود بانکی", "profit paid", "dividend", "سود سهام"),
}

# Semantic category → (human label, account-name hints, statement nature).
# The hints are matched against the tenant's own account names, so the same
# category resolves to 6130 on a personal chart, 6130 (travel) on the Iranian
# SME chart, and 7400 (motor expenses) on a UK chart — without any of those
# codes being written down here.
_CATEGORY_ACCOUNTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "transport": ("Transport", ("حمل", "ایاب", "سفر", "transport", "motor", "travel", "vehicle", "fuel"), EXPENSE),
    "groceries": ("Food & groceries", ("خوراک", "سوپرمارکت", "غذا", "food", "grocer", "provisions"), EXPENSE),
    "dining": ("Leisure & dining out", ("تفریح", "رستوران", "leisure", "dining", "entertain"), EXPENSE),
    "utilities": ("Utilities & bills", ("قبوض", "خدمات", "utilit", "light", "heat", "power", "telephone"), EXPENSE),
    "rent": ("Housing & rent", ("مسکن", "اجاره", "rent", "rates", "housing"), EXPENSE),
    "health": ("Health", ("سلامت", "درمان", "health", "medical", "insurance"), EXPENSE),
    "education": ("Education", ("آموزش", "education", "training"), EXPENSE),
    "clothing": ("Clothing", ("پوشاک", "clothing", "uniform"), EXPENSE),
    "subscriptions": ("Subscriptions", ("اشتراک", "subscription", "software", "office"), EXPENSE),
    "gifts": ("Family & gifts", ("خانواده", "هدایا", "gift", "donation", "charit"), EXPENSE),
    "bank_fee": ("Bank fees", ("کارمزد", "مالی", "bank charge", "charges", "finance"), EXPENSE),
    "loan": ("Loan / installments", ("اقساط", "وام", "loan", "hire purchase"), "LIABILITY"),
    "salary": ("Salary", ("حقوق", "دستمزد", "salary", "wages", "turnover", "sales"), REVENUE),
    "interest_income": ("Interest & investment", ("سود", "interest", "investment", "other income"), REVENUE),
}

_PUNCT_RE = re.compile(r"[^\w؀-ۿ]+", re.UNICODE)
# Bank narrations are full of transient noise — card/reference/terminal numbers,
# dates — which would otherwise make every row look unique to the history match.
_NOISE_TOKEN_RE = re.compile(r"^(?:\d+|[a-z]?\d[\w]*)$")
_STOPWORDS = {
    "pos", "trx", "trn", "ref", "card", "purchase", "payment", "transfer", "to", "from",
    "خرید", "پرداخت", "انتقال", "کارت", "به", "از", "بابت", "شماره", "واریز", "برداشت",
}


def normalize_narration(text: str | None) -> str:
    """Lowercase, fold Persian digits, drop punctuation and reference numbers."""
    if not text:
        return ""
    s = str(text).translate(_DIGITS).lower()
    s = _PUNCT_RE.sub(" ", s)
    tokens = [
        t for t in s.split()
        if t not in _STOPWORDS and not _NOISE_TOKEN_RE.match(t) and len(t) > 1
    ]
    return " ".join(tokens)


def _tokens(text: str) -> set[str]:
    return set(text.split())


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _postable(db: Session) -> dict[str, Account]:
    """Leaf-ish accounts this tenant can post to, keyed by code."""
    rows = db.execute(
        select(Account).where(Account.level != AccountLevel.GROUP)
    ).scalars().all()
    return {a.code: a for a in rows}


def _nature(code: str) -> str:
    return classify_account_code(code)


# --- Signal 1: history ------------------------------------------------------
def _history_suggestion(
    db: Session, narration: str, *, want_nature: str
) -> CategorySuggestion | None:
    if not narration:
        return None
    target = _tokens(narration)
    txns = db.execute(
        select(Transaction)
        .where(Transaction.deleted_at.is_(None), Transaction.description.is_not(None))
        .order_by(Transaction.date.desc())
        .limit(HISTORY_LIMIT)
    ).scalars().all()

    exact: Counter[str] = Counter()
    fuzzy: Counter[str] = Counter()
    for txn in txns:
        past = normalize_narration(txn.description)
        if not past:
            continue
        if past == narration:
            bucket = exact
        elif _similarity(target, _tokens(past)) >= FUZZY_THRESHOLD:
            bucket = fuzzy
        else:
            continue
        for line in txn.lines:
            code = line.account.code
            # Skip the cash/bank leg — we want what the money was *for*.
            if _nature(code) == want_nature:
                bucket[code] += 1

    for bucket, confidence in ((exact, CONFIDENCE_HISTORY_EXACT), (fuzzy, CONFIDENCE_HISTORY_FUZZY)):
        if not bucket:
            continue
        code, _count = bucket.most_common(1)[0]
        acc = db.execute(select(Account).where(Account.code == code)).scalars().first()
        if acc is not None:
            return CategorySuggestion(
                account_code=acc.code, account_name=acc.name,
                category=acc.name, confidence=confidence, source="history",
            )
    return None


# --- Signal 2: keywords -----------------------------------------------------
def _match_category(narration: str) -> str | None:
    """Longest keyword wins, so 'اسنپ فود' beats 'اسنپ'."""
    best: tuple[int, str] | None = None
    for category, keywords in _MERCHANT_KEYWORDS.items():
        for kw in keywords:
            if kw in narration and (best is None or len(kw) > best[0]):
                best = (len(kw), category)
    return best[1] if best else None


def _resolve_in_chart(db: Session, category: str, *, want_nature: str) -> CategorySuggestion | None:
    label, hints, nature = _CATEGORY_ACCOUNTS[category]
    if nature != want_nature:
        return None
    candidates = [
        acc for code, acc in _postable(db).items() if _nature(code) == want_nature
    ]
    best: tuple[int, Account] | None = None
    for acc in candidates:
        name = (acc.name or "").lower()
        for hint in hints:
            if hint.lower() in name and (best is None or len(hint) > best[0]):
                best = (len(hint), acc)
    if best is None:
        return None
    acc = best[1]
    return CategorySuggestion(
        account_code=acc.code, account_name=acc.name,
        category=label, confidence=CONFIDENCE_KEYWORD, source="keyword",
    )


# --- Public API -------------------------------------------------------------
def suggest_for_row(db: Session, description: str | None, *, is_debit: bool) -> CategorySuggestion | None:
    """Suggest the non-bank leg for a statement row, or None if nothing fits.

    ``is_debit`` means money left the account, so the counter leg is an expense
    (or a liability repayment); a credit row is income.
    """
    narration = normalize_narration(description)
    if not narration:
        return None
    want_nature = EXPENSE if is_debit else REVENUE

    hit = _history_suggestion(db, narration, want_nature=want_nature)
    if hit is not None:
        return hit

    category = _match_category(narration)
    if category is None:
        return None
    # A loan repayment is a liability, not an expense — only offer it on a debit
    # row, and only if the chart actually carries such an account.
    if category == "loan" and is_debit:
        for code, acc in _postable(db).items():
            name = (acc.name or "").lower()
            if _nature(code) == "LIABILITY" and any(h in name for h in ("اقساط", "وام", "loan")):
                return CategorySuggestion(
                    account_code=acc.code, account_name=acc.name,
                    category=_CATEGORY_ACCOUNTS["loan"][0],
                    confidence=CONFIDENCE_KEYWORD, source="keyword",
                )
        return None
    return _resolve_in_chart(db, category, want_nature=want_nature)
