"""P55 audit webhook alerts config

Revision ID: 0032_p55_audit_webhook
Revises: 0031_p53_audit_retention
Create Date: 2026-11-18

Ajoute 2 colonnes à `tenants` pour la notification webhook des alertes compliance (P55) :
  - audit_alert_webhook_url (String, nullable) — URL Slack/email/custom pour POST des alerts.
  - audit_alert_min_severity (String(8), default 'high', nullable=False) — seuil min de notification.
Rétro-compatible (webhook_url nullable, min_severity server_default 'high').
"""
import sqlalchemy as sa
from alembic import op

revision = "0032_p55_audit_webhook"
down_revision = "0031_p53_audit_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("audit_alert_webhook_url", sa.String(), nullable=True))
    op.add_column("tenants", sa.Column("audit_alert_min_severity", sa.String(length=8),
                                       nullable=False, server_default="high"))


def downgrade() -> None:
    op.drop_column("tenants", "audit_alert_min_severity")
    op.drop_column("tenants", "audit_alert_webhook_url")
