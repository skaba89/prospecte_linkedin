"""P18 Webhooks outbound : tables webhooks + webhook_deliveries.

Revision ID: 0008_p18_webhooks
Revises: 0007_p15_sequences
Create Date: 2026-11-16

Webhooks outbound (intégrations externes Slack/Zapier/CRM) : notifie un système externe sur
des événements RevPulse (contact.created, contact.scored, sequence.enrolled,
sequence.step_completed, email.sent, opportunity.created). webhooks = config par tenant (url,
secret HMAC, events souscrits, active). webhook_deliveries = log de livraison (payload, status
pending|success|failed, response_code, attempts, max_attempts, last_error). Le service
services/webhooks.py emit_event crée les deliveries + deliver les POST (HMAC-SHA256 signature
header X-RevPulse-Signature, retry backoff jusqu'à max_attempts).

Sécurité : 2 nouvelles tables, aucune modification de table existante (pas d'ALTER). CASCADE
au niveau DB sur webhook_deliveries.webhook_id (ondelete=CASCADE). Aucune donnée seedée (le
runtime crée les webhooks via l'API). Index sur webhook_deliveries.status pour le lookup du
worker retry.
"""
import sqlalchemy as sa

from alembic import op

revision = "0008_p18_webhooks"
down_revision = "0007_p15_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column("events", sa.Text(), nullable=False),  # JSON array (JSONType portable)
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("webhook_id", sa.String(), nullable=False, index=True),
        sa.Column("event", sa.String(), nullable=False, index=True),
        sa.Column("payload", sa.Text(), nullable=False),  # JSON (JSONType portable)
        sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
