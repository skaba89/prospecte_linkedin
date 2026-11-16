"""audit_logs table (P4 — trace immuable des actions mutantes par tenant)

Revision ID: 0002_audit_log
Revises: 0001_initial
Create Date: 2026-11-15

tenant_id est un String indexé SANS ForeignKey : la trace d'audit doit survivre
à la suppression du tenant (une audit log outlive l'entité auditée — sinon la FK
casserait au DELETE). detail utilise JSONB sous Postgres / Text sous SQLite
(identique au TypeDecorator JSONType du modèle).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0002_audit_log"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("detail", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_table("audit_logs")
