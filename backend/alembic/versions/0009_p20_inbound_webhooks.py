"""P20 Webhooks inbound : table inbound_webhooks.

Revision ID: 0009_p20_inbound_webhooks
Revises: 0008_p18_webhooks
Create Date: 2026-11-17

Webhooks inbound (réception depuis systèmes externes) : un formulaire site, Zapier ou un CRM
externe POST sur /api/inbound/webhook/{token} → RevPulse crée ou met à jour un Contact. C'est le
pendent symétrique du Webhook outbound (P18) : P18 = emit vers l'extérieur, P20 = receive depuis
l'extérieur. inbound_webhooks = config par tenant (token public 32 hex dans l'URL, source_name
label, field_map dict optionnel pour mapper un payload non-standard, active). Le service
services/inbound_webhooks.py process_inbound parse le payload, dédup par email/linkedin_url,
crée/met à jour le Contact (source="inbound"), emit contact.created pour les nouveaux (→ déclenche
les webhooks outbound P18 : la chaîne in→out est complète).

Sécurité : 1 nouvelle table, aucune modification de table existante (pas d'ALTER). CASCADE au
niveau DB sur tenant_id (ondelete=CASCADE). token = unique index (lookup public endpoint).
Aucune donnée seedée (le runtime crée les inbound webhooks via l'API admin).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0009_p20_inbound_webhooks"
down_revision = "0008_p18_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_webhooks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("field_map", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),  # JSON dict — JSONB PG / Text sqlite (aligné JSONType modèle, pattern 0008)
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_inbound_webhooks_token", "inbound_webhooks", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_inbound_webhooks_token", table_name="inbound_webhooks")
    op.drop_table("inbound_webhooks")
