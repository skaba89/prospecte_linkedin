"""P60 GDPR — add User.gdpr_erased_at (timestamp du right-to-be-forgotten, soft-delete audit trail).

Revision ID: 0033_p60_gdpr_erased_at
Revises: 0032_p55_audit_webhook
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_p60_gdpr_erased_at"
down_revision = "0032_p55_audit_webhook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # User.gdpr_erased_at : null = actif ; timestamp = right-to-be-forgotten exercised (GDPR Art. 17).
    # L'utilisateur est anonymisé mais la ligne User reste (FK integrity + audit retention SOC2 7 ans).
    op.add_column("users", sa.Column("gdpr_erased_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "gdpr_erased_at")
