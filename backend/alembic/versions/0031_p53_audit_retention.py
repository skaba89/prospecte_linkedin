"""P53 audit log retention policy

Revision ID: 0031_p53_audit_retention
Revises: 0030_p48_audit_log_ip
Create Date: 2026-11-18

Ajoute la colonne `retention_days` à `tenants` (default 2555 = 7 ans SOC2).
POST /api/tenants/{tid}/audit/purge supprime les entries > retention_days (avec log meta de la purge).
Rétro-compatible (default 2555 nullable=False).
"""
import sqlalchemy as sa
from alembic import op

revision = "0031_p53_audit_retention"
down_revision = "0030_p48_audit_log_ip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("retention_days", sa.Integer(), nullable=False, server_default="2555"))


def downgrade() -> None:
    op.drop_column("tenants", "retention_days")
