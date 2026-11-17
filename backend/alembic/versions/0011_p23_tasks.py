"""P23 Tasks & reminders avancés : ALTER TABLE tasks (+ reminder_at, reminder_sent, completed_at).

Revision ID: 0011_p23_tasks
Revises: 0010_p22_tags
Create Date: 2026-11-17

Étend le modèle Task (P11) existant avec 3 colonnes pour la gestion avancée des tâches :
  - reminder_at (DateTime, nullable, indexé) : instant où le scheduler (services/task_reminders.py
    process_due_task_reminders) déclenche une Notification in-app (feed) vers l'assignee.
  - reminder_sent (Boolean, default false) : flip True après envoi → évite les doublons de rappel
    (idempotence du scheduler : un reminder n'est envoyé qu'une fois par tâche).
  - completed_at (DateTime, nullable) : auto-set quand status→done (analytics de productivité,
    temps de résolution). Le router update_task le populate automatiquement sur la transition.

ALTER TABLE ADD COLUMN uniquement (pas de DROP/rename/type-change → compatible SQLite sans
batch_alter). Aucune nouvelle table, aucune donnée seedée (les colonnes sont nullable ou ont un
server_default). Index sur reminder_at pour la query du scheduler (WHERE reminder_at <= now AND
NOT reminder_sent).
"""
import sqlalchemy as sa

from alembic import op

revision = "0011_p23_tasks"
down_revision = "0010_p22_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("reminder_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("reminder_sent", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_reminder_at", "tasks", ["reminder_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_reminder_at", table_name="tasks")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "reminder_sent")
    op.drop_column("tasks", "reminder_at")
