"""P42 Career : Agent autonome (recherche + matching + adapt + candidature prep + suivi + rapport).

Revision ID: 0025_p42_agent
Revises: 0024_p41_coach_ia
Create Date: 2026-11-17

Nouvelles tables (2) :
  - career_agents : config + état de l'agent Career (1 par profil, unique career_profile_id).
    enabled (Bool), auto_apply (Bool défaut False — v1 : prépare sans envoyer), consent_given (Bool
    défaut False — consentement explicite requis pour auto_apply), max_applications_per_day (Int 5),
    daily_sent_count (Int 0), daily_reset_at (DateTime), last_run_at (DateTime), status (idle|running|stopped).
    Bouton d'arrêt immédiat : status=stopped.
  - career_agent_runs : historique + rapport des runs. career_profile_id FK CASCADE, agent_id FK CASCADE,
    started_at, completed_at, status (running|success|stopped|failed), steps JSON [{step, status, count,
    detail}], summary JSON (offres trouvées, compatibles, candidatures préparées, candidatures envoyées,
    notifications, interview_sessions, analytics snapshot), applications_prepared, applications_sent.

Anti-spam + consent (P42 invariant) : auto_apply=False par défaut → l'agent prépare mais n'envoie JAMAIS
sans consent_given=True + auto_apply=True + validation humaine. max_applications_per_day cap anti-abus.
Bouton d'arrêt immédiat (status=stopped) interrompable à tout moment. Journalisation complète (chaque run
persisté + steps détaillés). v2 : scheduler quotidien (celery/APScheduler).
"""
import sqlalchemy as sa

from alembic import op

revision = "0025_p42_agent"
down_revision = "0024_p41_coach_ia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_agents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("consent_given", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_applications_per_day", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("daily_sent_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("daily_reset_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="idle"),  # idle|running|stopped
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("career_profile_id", name="uq_career_agents_profile"),
    )
    op.create_table(
        "career_agent_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),  # running|success|stopped|failed
        sa.Column("steps", sa.JSON(), nullable=True),  # [{step, status, count, detail}]
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("applications_prepared", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("applications_sent", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["career_agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_career_agent_runs_profile", "career_agent_runs", ["career_profile_id"])
    op.create_index("ix_career_agent_runs_status", "career_agent_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_career_agent_runs_status", table_name="career_agent_runs")
    op.drop_index("ix_career_agent_runs_profile", table_name="career_agent_runs")
    op.drop_table("career_agent_runs")
    op.drop_table("career_agents")
