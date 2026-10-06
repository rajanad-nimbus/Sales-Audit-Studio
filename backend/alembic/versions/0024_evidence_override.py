"""governed evidence rejection and override
Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa
revision="0024"; down_revision="0023"; branch_labels=None; depends_on=None
def upgrade():
 for name, col in [("rejection_reason",sa.Text()),("override_reason",sa.Text()),("override_financial_impact",sa.Numeric(15,2)),("override_requested_by",sa.String(255)),("override_requested_at",sa.DateTime(timezone=True)),("override_approved_by",sa.String(255)),("override_approved_at",sa.DateTime(timezone=True))]: op.add_column("evidence_requests",sa.Column(name,col,nullable=True))
def downgrade():
 for name in ["override_approved_at","override_approved_by","override_requested_at","override_requested_by","override_financial_impact","override_reason","rejection_reason"]: op.drop_column("evidence_requests",name)
