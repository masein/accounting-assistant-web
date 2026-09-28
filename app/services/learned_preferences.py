"""Correction memory (roadmap 2026-09 §5.4).

``remember`` is called wherever the user overrules a suggestion — a statement
row approved with another account than the one suggested, an entry whose
account or party is edited afterwards, the assistant told "always put Snapp
under travel". ``lookup`` answers "what did the user choose for text like
this?": the same normalised narration, else one whose words mostly overlap,
else one whose words all appear in it ("snapp" inside "pos snapp tehran").

The latest correction wins; ``times_chosen`` counts how often the user made
it, ``times_used`` how often it was applied since. Nothing here posts
anything: it only changes what is suggested.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.learned_preference import LearnedPreference

FUZZY = 0.6
MIN_PATTERN_CHARS = 3
SOURCES = ("statement", "edit", "chat", "manual")


def _norm(text: str | None) -> str:
    from app.services.statement_categorizer import normalize_narration
    return normalize_narration(text)[:256]


def _tokens(pattern: str) -> set[str]:
    return set(pattern.split())


def remember(db: Session, text: str | None, *, account_code: str | None = None,
             entity_id: uuid.UUID | str | None = None, source: str = "edit") -> LearnedPreference | None:
    """Keep the user's choice for ``text``. Returns None when the text is too
    generic to learn from (empty after dropping numbers and noise words)."""
    pattern = _norm(text)
    if len(pattern) < MIN_PATTERN_CHARS or (account_code is None and entity_id is None):
        return None
    if entity_id is not None and not isinstance(entity_id, uuid.UUID):
        entity_id = uuid.UUID(str(entity_id))
    pref = db.execute(select(LearnedPreference).where(LearnedPreference.pattern == pattern)).scalars().first()
    if pref is None:
        pref = LearnedPreference(pattern=pattern, label=(text or pattern).strip()[:256], source=source,
                                 account_code=account_code, entity_id=entity_id, times_chosen=1, times_used=0)
        db.add(pref)
    else:
        if account_code is not None:
            pref.account_code = account_code
        if entity_id is not None:
            pref.entity_id = entity_id
        pref.source = source
        pref.label = (text or pref.label).strip()[:256]
        pref.times_chosen = (pref.times_chosen or 0) + 1
    db.flush()
    return pref


@dataclass(frozen=True)
class Match:
    preference: LearnedPreference
    score: float                 # 1.0 same narration, else word overlap


def lookup(db: Session, text: str | None, *, want: str = "account") -> Match | None:
    """The preference that fits ``text`` best, having a value of the kind
    wanted ("account" or "entity")."""
    pattern = _norm(text)
    if not pattern:
        return None
    col = LearnedPreference.account_code if want == "account" else LearnedPreference.entity_id
    rows = db.execute(select(LearnedPreference).where(col.is_not(None))).scalars().all()
    target = _tokens(pattern)
    best: Match | None = None
    for pref in rows:
        if pref.pattern == pattern:
            score = 1.0
        else:
            words = _tokens(pref.pattern)
            if not words:
                continue
            overlap = len(target & words) / len(target | words)
            # every word of the learned pattern appears in the new text
            contained = 0.9 if words <= target else 0.0
            score = max(overlap, contained)
            if score < FUZZY:
                continue
        if best is None or (score, pref.times_chosen or 0) > (best.score, best.preference.times_chosen or 0):
            best = Match(pref, score)
    return best


def snapshot(txn) -> tuple[dict[str, int], dict[str, str]]:
    """(account code → net amount, role → entity id) of an entry, before an edit."""
    nets: dict[str, int] = {}
    for ln in txn.lines or []:
        code = ln.account.code if ln.account is not None else None
        if code:
            nets[code] = nets.get(code, 0) + int(ln.debit or 0) - int(ln.credit or 0)
    links = {(lk.role or "").lower(): str(lk.entity_id) for lk in (txn.entity_links or []) if lk.entity_id}
    return nets, links


def learn_from_edit(db: Session, description: str | None, before: tuple[dict, dict],
                    after: tuple[dict, dict]) -> list[LearnedPreference]:
    """An edited entry teaches something when one account was swapped for
    another at the same amount (a recategorisation, not a restructuring), or
    when its party changed."""
    (nets_b, links_b), (nets_a, links_a) = before, after
    learned: list[LearnedPreference] = []
    removed = {c: v for c, v in nets_b.items() if c not in nets_a and v}
    added = {c: v for c, v in nets_a.items() if c not in nets_b and v}
    if len(removed) == 1 and len(added) == 1 and next(iter(removed.values())) == next(iter(added.values())):
        pref = remember(db, description, account_code=next(iter(added)), source="edit")
        if pref:
            learned.append(pref)
    for role, eid in links_a.items():
        if links_b.get(role) != eid:
            pref = remember(db, description, entity_id=eid, source="edit")
            if pref:
                learned.append(pref)
    return learned


def mark_used(db: Session, pref: LearnedPreference) -> None:
    pref.times_used = (pref.times_used or 0) + 1
    pref.last_used_at = datetime.now(timezone.utc)


def list_all(db: Session) -> list[LearnedPreference]:
    return list(db.execute(select(LearnedPreference).order_by(
        LearnedPreference.times_chosen.desc(), LearnedPreference.updated_at.desc())).scalars())


def forget(db: Session, pref_id: uuid.UUID) -> bool:
    pref = db.get(LearnedPreference, pref_id)
    if pref is None:
        return False
    db.delete(pref)
    db.flush()
    return True


def describe(db: Session, pref: LearnedPreference) -> dict:
    """For the API, the tools and the prompt: names instead of ids."""
    from app.models.account import Account
    from app.models.entity import Entity
    acc = (db.execute(select(Account).where(Account.code == pref.account_code)).scalars().first()
           if pref.account_code else None)
    ent = db.get(Entity, pref.entity_id) if pref.entity_id else None
    return {
        "id": str(pref.id), "pattern": pref.pattern, "label": pref.label,
        "account_code": pref.account_code, "account_name": acc.name if acc else None,
        "entity_id": str(pref.entity_id) if pref.entity_id else None,
        "entity_name": ent.name if ent else None, "entity_type": ent.type if ent else None,
        "source": pref.source, "times_chosen": pref.times_chosen or 0, "times_used": pref.times_used or 0,
        "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
    }


def prompt_block(db: Session, limit: int = 15) -> str:
    """A few lines for the assistant's system prompt: the user's standing
    choices, most-made first. Empty when there are none."""
    rows = list_all(db)[:limit]
    if not rows:
        return ""
    out = []
    for pref in rows:
        d = describe(db, pref)
        parts = []
        if d["account_code"]:
            parts.append(f"account {d['account_code']}" + (f" ({d['account_name']})" if d["account_name"] else ""))
        if d["entity_name"]:
            parts.append(f"{d['entity_type'] or 'party'} {d['entity_name']} (entity_id {d['entity_id']})")
        if parts:
            out.append(f'- "{d["label"]}" → ' + ", ".join(parts))
    if not out:
        return ""
    return ("\n\n## This company's own choices (learned from the user's corrections — use them)\n"
            "When a transaction's description matches one of these, use that account/party unless the user "
            "says otherwise now:\n" + "\n".join(out))
