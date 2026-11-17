"""P24 Analytics v2 : ALTER TABLE opportunities (+ closed_at pour time-to-close).

Revision ID: 0012_p24_analytics
Revises: 0011_p23_tasks
Create Date: 2026-11-17

Étend le modèle Opportunity (P10) avec closed_at (DateTime, nullable) : auto-set quand stage→
closed_won par le router update_opportunity (pattern identique à Task.completed_at en P23).
Sert au calcul du time-to-close (avg jours entre created_at et closed_at) dans l'endpoint
analytics/cohorts (P24). Clear sur retour arrière (closed_won→autre stage).

ALTER TABLE ADD COLUMN uniquement (nullable, pas de server_default → compatible SQLite sans
batch_alter). Aucune nouvelle table, aucune donnée seedée. Les opportunités closed_won existantes
auront closed_at=NULL (le router ne backfill pas rétroactivement — l'audit log reste la source de
vérité historique si besoin de reconstruire).
"""
import sqlalchemy as sa

from alembic import op

revision = "0012_p24_analytics"
down_revision = "0011_p23_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("closed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("opportunities", "closed_at")
