"""P48 audit log IP + user_agent

Revision ID: 0030_p48_audit_log_ip
Revises: 0029_p45_sso_i18n
Create Date: 2026-11-18

Ajoute les colonnes `ip` + `user_agent` à `audit_logs` pour la traçabilité SOC2/GDPR.
Capturées via context vars (RequestLoggingMiddleware) → pick automatique par audit.log_audit()
sans modifier les 17 routers déjà instrumentés. Rétro-compatible (default "" nullable=False).
"""
import sqlalchemy as sa

from alembic import op

revision = "0030_p48_audit_log_ip"
down_revision = "0029_p45_sso_i18n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("ip", sa.String(), nullable=False, server_default=""))
    op.add_column("audit_logs", sa.Column("user_agent", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip")
