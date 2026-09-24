"""audit_logs is append-only: a trigger refuses UPDATE/DELETE, and deleting a
company no longer cascades into its audit trail (security review 2026-09-24,
H6 — the table was "immutable" only in a code comment).

Revision ID: 036
Revises: 035
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return  # SQLite dev/test databases rely on the ORM guard in app/models/audit_log.py
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (% refused)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()
    """))
    # A company's trail outlives any attempt to delete the company.
    conn.execute(sa.text("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS fk_audit_logs_company"))
    conn.execute(sa.text(
        "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_logs_company "
        "FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS audit_logs_append_only()"))
    conn.execute(sa.text("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS fk_audit_logs_company"))
    conn.execute(sa.text(
        "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_logs_company "
        "FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE"
    ))
