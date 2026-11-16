"""P15 Séquences : tables email_sequences + sequence_steps + sequence_enrollments.

Revision ID: 0007_p15_sequences
Revises: 0006_p13_notifications
Create Date: 2026-11-16

Séquences d'e-mails multi-steps (drip campaigns automatisées). email_sequences = conteneur
(name, description, status draft|active|paused). sequence_steps = étapes ordonnées (template_id
P13 + delay_days). sequence_enrollments = enrollment d'un contact (current_step_order, next_step_at,
status active|completed|paused|failed). Le scheduler (services/sequences.py process_due_enrollments)
traite les enrollments dus (next_step_at <= now) — envoie le step via send_email_to_contact (P13),
avance, calcule le next_step_at. Scopé par tenant. Index composé (status, next_step_at) pour le
lookup scheduler.

Sécurité : 3 nouvelles tables, aucune modification de table existante (pas de ALTER). Aucune
donnée seedée (le runtime crée séquences/steps/enrollments via l'API). CASCADE logique au niveau
app (le router delete supprime steps + enrollments avant la séquence).
"""
import sqlalchemy as sa

from alembic import op

revision = "0007_p15_sequences"
down_revision = "0006_p13_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_sequences",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sequence_steps",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("sequence_id", sa.String(), nullable=False, index=True),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("delay_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "sequence_enrollments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("sequence_id", sa.String(), nullable=False, index=True),
        sa.Column("contact_id", sa.String(), nullable=False, index=True),
        sa.Column("current_step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("enrolled_by_user_id", sa.String(), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(), nullable=False),
        sa.Column("next_step_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
    )
    # Index composé : requête scheduler (status + next_step_at) — lookup rapide des enrollments dus.
    op.create_index("ix_enrollments_status_next", "sequence_enrollments", ["status", "next_step_at"])


def downgrade() -> None:
    op.drop_index("ix_enrollments_status_next", table_name="sequence_enrollments")
    op.drop_table("sequence_enrollments")
    op.drop_table("sequence_steps")
    op.drop_table("email_sequences")
