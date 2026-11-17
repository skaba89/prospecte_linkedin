"""P26 LinkedIn Content Studio professionnel : tables content_templates + content_posts.

Revision ID: 0016_p26_content_studio
Revises: 0015_p25_ai_agents
Create Date: 2026-11-17

content_templates : modèles de posts réutilisables (storytelling, insight, announcement, carousel,
question, behind_the_scenes). is_system=True pour les modèles seedés (immutable). structure = template
avec {{variables}}.

content_posts : posts LinkedIn avec workflow validation collaborative (draft → pending_review →
approved|rejected → scheduled → published). carousel = JSON (slides). ai_generation_id FK →
ai_generations (audit trail P24-B → P25 → P26). linkedin_account_id FK → linkedin_accounts (P12).
stats = JSON (impressions, likes, comments, shares — NULL jusqu'à sync LinkedIn API réelle).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0016_p26_content_studio"
down_revision = "0015_p25_ai_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── content_templates ──
    op.create_table(
        "content_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("structure", sa.Text(), nullable=False),
        sa.Column("variables", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("style", sa.String(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_templates_tenant_id", "content_templates", ["tenant_id"])
    op.create_index("ix_content_templates_category", "content_templates", ["category"])

    # ── content_posts ──
    op.create_table(
        "content_posts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("style", sa.String(), nullable=True),
        sa.Column("hashtags", sa.String(), nullable=True),
        sa.Column("carousel", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("media_urls", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("linkedin_urn", sa.String(), nullable=True),
        sa.Column("ai_generation_id", sa.String(), nullable=True),
        sa.Column("validation_requested_by", sa.String(), nullable=True),
        sa.Column("validation_requested_at", sa.DateTime(), nullable=True),
        sa.Column("validated_by", sa.String(), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("linkedin_account_id", sa.String(), nullable=True),
        sa.Column("stats", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["content_templates.id"]),
        sa.ForeignKeyConstraint(["ai_generation_id"], ["ai_generations.id"]),
        sa.ForeignKeyConstraint(["validation_requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["validated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_posts_tenant_id", "content_posts", ["tenant_id"])
    op.create_index("ix_content_posts_status", "content_posts", ["status"])
    op.create_index("ix_content_posts_created_at", "content_posts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_content_posts_created_at", table_name="content_posts")
    op.drop_index("ix_content_posts_status", table_name="content_posts")
    op.drop_index("ix_content_posts_tenant_id", table_name="content_posts")
    op.drop_table("content_posts")
    op.drop_index("ix_content_templates_category", table_name="content_templates")
    op.drop_index("ix_content_templates_tenant_id", table_name="content_templates")
    op.drop_table("content_templates")
