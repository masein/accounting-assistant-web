"""An account's codes in the books it came from.

A school's journal import gave its accounts the app's codes: their 1300
«مخارج مدرسه» became 6132, and «کدینگ مخارج مدرسه اشتباه ست باید ۱۳۰۰ میبوده»
found nothing (2026-10-19). ``accounts.source_codes`` keeps the file's codes
so a search by them finds the account and cards show both. The books imported
before now get theirs from the journal import's saved account map (its keys
are «code|name» in the file; its values the app's codes). Safe to run twice.

Revision ID: 080
Revises: 079
Create Date: 2026-10-19
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "080"
down_revision: Union[str, None] = "079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAP_KEY = "journal_import_account_map"


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("accounts")}
    if "source_codes" not in columns:
        op.add_column("accounts", sa.Column("source_codes", sa.String(255), nullable=True))
    tables = set(sa.inspect(bind).get_table_names())
    if "app_settings" not in tables:
        return
    for company_id, value in bind.execute(sa.text(
            "SELECT company_id, value FROM app_settings WHERE key = :k AND company_id IS NOT NULL"), {"k": _MAP_KEY}).fetchall():
        try:
            mapping = json.loads(value or "{}")
        except ValueError:
            continue
        codes: dict[str, list[str]] = {}
        for key, target in mapping.items():
            source = str(key).split("|", 1)[0].strip()
            target = str(target or "").strip()
            if source and target and not target.startswith("new:") and source != target:
                codes.setdefault(target, [])
                if source not in codes[target]:
                    codes[target].append(source)
        for target, found in codes.items():
            row = bind.execute(sa.text("SELECT id, source_codes FROM accounts WHERE company_id = :c AND code = :code"),
                               {"c": company_id, "code": target}).first()
            if row is None:
                continue
            have = [c for c in (row[1] or "").split(",") if c]
            merged = have + [c for c in found if c not in have]
            if merged != have:
                bind.execute(sa.text("UPDATE accounts SET source_codes = :v WHERE id = :id"),
                             {"v": ",".join(merged)[:255], "id": row[0]})


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "source_codes" in columns:
        op.drop_column("accounts", "source_codes")
