"""initial schema (7 tables multi-tenant)

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15

Schéma de référence (source de vérité Alembic). Reflète exactement app/models.py
(users, tenants, profiles, members + expires_at, approvals, dashboard_modules,
linkedin_connections). Le payload JSON utilise JSONB sous Postgres / Text sous
SQLite (identique au TypeDecorator JSONType du modèle).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("headline", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("initials", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_profiles_tenant"),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=True),
        sa.Column("invited_by", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_members_tenant_id", "members", ["tenant_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("submitter", sa.String(), nullable=True),
        sa.Column("submitted_ago", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("risk_flags", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_body", sa.Text(), nullable=True),
        sa.Column("type_label", sa.String(), nullable=True),
        sa.Column("type_color", sa.String(), nullable=True),
        sa.Column("priority_label", sa.String(), nullable=True),
        sa.Column("priority_color", sa.String(), nullable=True),
        sa.Column("status_label", sa.String(), nullable=True),
        sa.Column("status_color", sa.String(), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_tenant_id", "approvals", ["tenant_id"])

    op.create_table(
        "dashboard_modules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("module_key", sa.String(), nullable=False),
        sa.Column("payload", JSONB().with_variant(sa.Text(), "sqlite"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "module_key", name="uq_tenant_module"),
    )
    op.create_index("ix_dashboard_modules_tenant_id", "dashboard_modules", ["tenant_id"])

    op.create_table(
        "linkedin_connections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("member_id", sa.String(), nullable=True),
        sa.Column("member_name", sa.String(), nullable=True),
        sa.Column("member_headline", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_linkedin_tenant"),
    )


def downgrade() -> None:
    op.drop_table("linkedin_connections")
    op.drop_index("ix_dashboard_modules_tenant_id", table_name="dashboard_modules")
    op.drop_table("dashboard_modules")
    op.drop_index("ix_approvals_tenant_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_members_tenant_id", table_name="members")
    op.drop_table("members")
    op.drop_table("profiles")
    op.drop_table("tenants")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
