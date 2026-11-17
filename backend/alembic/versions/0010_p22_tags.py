"""P22 Tags & catégorisation de contacts : tables tags + contact_tags.

Revision ID: 0010_p22_tags
Revises: 0009_p20_inbound_webhooks
Create Date: 2026-11-17

Tags : labels explicites apposés sur des contacts (ex. « VIP », « Cold », « Webinaire 2026 »).
Complète les Segments (P14) qui sont des filtres dynamiques par requête — un Tag est une
catégorisation manuelle, mutuellement compatible avec les segments. UniqueConstraint(tenant_id,
name) → pas de doublon de nom dans un tenant. color = hex pour le badge frontend.

contact_tags : association many-to-many Contact ↔ Tag, PK composite (contact_id, tag_id) → pas de
doublon d'assignation. CASCADE sur les deux FK (suppression d'un contact OU d'un tag nettoie les
associations automatiquement).

Sécurité : 2 nouvelles tables, aucune modification de table existante (pas d'ALTER). CASCADE au
niveau DB sur tenant_id + contact_id + tag_id (ondelete=CASCADE). Aucune donnée seedée (le runtime
crée les tags via l'API crm.manage).
"""
import sqlalchemy as sa

from alembic import op

revision = "0010_p22_tags"
down_revision = "0009_p20_inbound_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False, server_default="#635bff"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_tag_name"),
    )
    op.create_table(
        "contact_tags",
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("tag_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("contact_id", "tag_id", name="pk_contact_tag"),
    )


def downgrade() -> None:
    op.drop_table("contact_tags")
    op.drop_table("tags")
