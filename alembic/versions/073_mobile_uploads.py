"""Files the phone sends in pieces (roadmap ROADMAP_ANDROID_CHAT P0.7).

One row per upload: the file's hash, its size and how much has arrived, so
a phone whose connection dropped goes on from there; the attachment it became
once the last piece matched the hash. Tenant-scoped (the guard adds the
company foreign key). Safe to run twice.

Revision ID: 073
Revises: 072
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("mobile_uploads"):
        return
    op.create_table(
        "mobile_uploads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attachment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_mobile_uploads_user"),
        sa.ForeignKeyConstraint(["attachment_id"], ["transaction_attachments.id"], ondelete="SET NULL",
                                name="fk_mobile_uploads_attachment"),
    )
    op.create_index("ix_mobile_uploads_company_id", "mobile_uploads", ["company_id"])
    op.create_index("ix_mobile_uploads_user_sha256", "mobile_uploads", ["user_id", "sha256"])
    op.create_index("ix_mobile_uploads_updated_at", "mobile_uploads", ["updated_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("mobile_uploads"):
        op.drop_table("mobile_uploads")
