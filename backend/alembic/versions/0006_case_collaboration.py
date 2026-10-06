"""case collaboration

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("case_notes", sa.Column("id", sa.String(36), primary_key=True), sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False), sa.Column("author", sa.String(255), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_case_notes_case_id", "case_notes", ["case_id"])
    op.create_table("evidence_requests", sa.Column("id", sa.String(36), primary_key=True), sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id"), nullable=False), sa.Column("requirement", sa.String(100), nullable=False), sa.Column("requested_from", sa.String(255), nullable=False), sa.Column("requested_by", sa.String(255), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_evidence_requests_case_id", "evidence_requests", ["case_id"])
def downgrade():
    op.drop_table("evidence_requests"); op.drop_table("case_notes")
