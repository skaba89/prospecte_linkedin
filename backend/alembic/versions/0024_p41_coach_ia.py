"""P41 Career : Coach IA (simulation entretien tech + RH + négociation + skills manquantes + formations).

Revision ID: 0024_p41_coach_ia
Revises: 0023_p40_notifications
Create Date: 2026-11-17

Nouvelle table (1) :
  - interview_sessions : sessions de simulation d'entretien Career. career_profile_id FK CASCADE,
    application_id FK SET NULL (nullable — lien optionnel à une candidature dont l'entretien est préparé).
    session_type (tech|rhmixed), status (in_progress|completed), target_job_title, category,
    questions JSON [{id, text, type, topic, expected_keywords}], answers JSON [{question_id, text,
    score, feedback, matched, missing}], feedback JSON (récap global), score (0-100, nullable),
    created_at, completed_at (nullable).

Service interview_coach.py : generate_questions (par métier/catégorie depuis référentiel P34),
analyze_answer (heuristic keywords + feedback), compute_session_score, recommend_missing_skills
(cross-référence CV P34 vs métier), recommend_formations, negotiation_prep (salaire/TJM depuis profil).
Anti-invention : questions + feedback basés sur referential + CV — aucune affirmation non vérifiable.
"""
import sqlalchemy as sa

from alembic import op

revision = "0024_p41_coach_ia"
down_revision = "0023_p40_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("target_job_title", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),  # data|product|finance|cyber|rh|...
        sa.Column("session_type", sa.String(), nullable=False),  # tech|rhmixed
        sa.Column("status", sa.String(), nullable=False),  # in_progress|completed
        sa.Column("questions", sa.JSON(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("feedback", sa.JSON(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interview_sessions_career_profile_id", "interview_sessions", ["career_profile_id"])
    op.create_index("ix_interview_sessions_status", "interview_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_status", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_career_profile_id", table_name="interview_sessions")
    op.drop_table("interview_sessions")
