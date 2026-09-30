"""Bank statements in their company's currency (roadmap 2026-09 §4.1). Data only; idempotent.

Every uploaded statement was stored as IRR — the parser's default — so a UK
company's statements matched none of its GBP entries. New ones take the
bank's currency, else the company's (statement_import.statement_currency);
this moves the existing ones that are still open to their company's base
currency. Left alone: companies whose currency IS IRR, SMS statements (the
Iranian bank SMS feed is in rials), and any statement with a row already
posted or approved (those journals were written in IRR; POST /fx/relabel
fixes such entries one by one).

Revision ID: 067
Revises: 066
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FIX = sa.text("""
UPDATE bank_statements
   SET currency = (SELECT UPPER(TRIM(c.base_currency)) FROM companies c WHERE c.id = bank_statements.company_id)
 WHERE currency = 'IRR'
   AND source_type <> 'sms'
   AND company_id IN (SELECT id FROM companies
                       WHERE base_currency IS NOT NULL AND TRIM(base_currency) <> ''
                         AND UPPER(TRIM(base_currency)) <> 'IRR')
   AND NOT EXISTS (SELECT 1 FROM bank_statement_rows r
                    WHERE r.statement_id = bank_statements.id
                      AND (r.created_transaction_id IS NOT NULL OR r.user_approved = :yes))
""")


def fix(bind) -> int:
    return bind.execute(FIX, {"yes": True}).rowcount or 0


def upgrade() -> None:
    fix(op.get_bind())


def downgrade() -> None:
    pass                       # which statements were IRR on purpose can't be known
