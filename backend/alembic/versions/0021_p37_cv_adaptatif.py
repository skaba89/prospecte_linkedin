"""P37 Career : CV adaptatif (versions ciblées par offre, anti-invention).

Revision ID: 0021_p37_cv_adaptatif
Revises: 0020_p35_job_search
Create Date: 2026-11-17

Nouvelle table (1) :
  - adapted_cvs : CV adaptés à une offre cible. cv_source_id FK CASCADE (CV source), career_profile_id
    FK CASCADE (isolation candidat), posting_id FK SET NULL (offre cible, nullable pour critères libres).
    adapted_data = JSON (structure adaptée : targeted_title/summary/reordered_skills/selected_experiences/
    matched_certifications/missing_skills/match_score). match_score = score P36 (référence).

Invariant P37 (anti-invention) : adapted_data ne contient QUE du contenu issu du CV source (réordonné,
sélectionné, résumé). JAMAIS d'invention de skills/expériences/certifications. Vérifié par
cv_adapter._assert_no_invention (runtime) + test_p37 (couverture).
"""
import sqlalchemy as sa

from alembic import op

revision = "0021_p37_cv_adaptatif"
down_revision = "0020_p35_job_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapted_cvs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cv_source_id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("posting_id", sa.String(), nullable=True),
        sa.Column("target_job_title", sa.String(), nullable=True),
        sa.Column("target_company", sa.String(), nullable=True),
        sa.Column("adapted_data", sa.JSON(), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["cv_source_id"], ["cvs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["posting_id"], ["job_postings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_adapted_cvs_cv_source_id", "adapted_cvs", ["cv_source_id"])
    op.create_index("ix_adapted_cvs_career_profile_id", "adapted_cvs", ["career_profile_id"])
    op.create_index("ix_adapted_cvs_posting_id", "adapted_cvs", ["posting_id"])


def downgrade() -> None:
    op.drop_index("ix_adapted_cvs_posting_id", table_name="adapted_cvs")
    op.drop_index("ix_adapted_cvs_career_profile_id", table_name="adapted_cvs")
    op.drop_index("ix_adapted_cvs_cv_source_id", table_name="adapted_cvs")
    op.drop_table("adapted_cvs")
