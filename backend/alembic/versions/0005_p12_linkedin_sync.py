"""P12 LinkedIn sync : table linkedin_sync_runs (trace des syncs LinkedIn → CRM).

Revision ID: 0005_p12_linkedin_sync
Revises: 0004_p11_crm
Create Date: 2026-11-16

Table dédiée à l'audit + métriques des syncs LinkedIn (pont P10 OAuth ↔ P11 CRM) :
  - status (running|completed|failed), compteurs (total/imported/updated/skipped)
  - started_at/completed_at + error_detail (échec)
  - scopée par tenant_id (indexé) + actor_user_id (qui a déclenché)

Sécurité : création seule (aucune modification de table existante, aucune suppression).
Aucune donnée seedée par la migration (le sync runtime crée les lignes).
"""
import sqlalchemy as sa

from alembic import op

revision = "0005_p12_linkedin_sync"
down_revision = "0004_p11_crm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linkedin_sync_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
    )
    # Index composé : historique des syncs par tenant (ordre chrono).
    op.create_index("ix_linkedin_sync_runs_tenant_started",
                    "linkedin_sync_runs", ["tenant_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_linkedin_sync_runs_tenant_started", table_name="linkedin_sync_runs")
    op.drop_table("linkedin_sync_runs")
