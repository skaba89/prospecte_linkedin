"""P13 Notifications : table notifications (e-mails de prospection + in-app feed).

Revision ID: 0006_p13_notifications
Revises: 0005_p12_linkedin_sync
Create Date: 2026-11-16

Table dédiée au log durable des envois d'e-mails (prospection/suivi/invitation) + des
notifications in-app (feed cloche dashboard). Multi-canal (email | feed), statut durable
(pending|sent|failed|dry_run), read_at pour le feed (null = non lu). Scopée par tenant +
user (expéditeur) + contact (rattachement CRM). Pont P12 (contacts syncés) → P13 (outreach).

Sécurité : création seule (aucune modification de table existante). Aucune donnée seedée
par la migration (le runtime crée les lignes à chaque envoi/événement).
"""
import sqlalchemy as sa

from alembic import op

revision = "0006_p13_notifications"
down_revision = "0005_p12_linkedin_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False, server_default="email"),
        sa.Column("recipient_email", sa.String(), nullable=True, index=True),
        sa.Column("recipient_contact_id", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # Index composé : feed + historique par tenant (chrono desc).
    op.create_index("ix_notifications_tenant_created", "notifications", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_tenant_created", table_name="notifications")
    op.drop_table("notifications")
