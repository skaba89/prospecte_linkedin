"""P38 Career : Candidatures + Gmail/Outlook (pipeline de candidatures).

Revision ID: 0022_p38_candidatures
Revises: 0021_p37_cv_adaptatif
Create Date: 2026-11-17

Nouvelle table (1) :
  - job_applications : candidatures + pipeline (submitted/interview/offer/rejected/withdrawn).
    career_profile_id FK CASCADE (isolation), posting_id FK SET NULL (nullable), adapted_cv_id FK SET NULL
    (nullable). cover_letter (Text, anti-invention P37 invariant), timeline (JSON historique étapes),
    application_method (manual|email|external), recipient_email, sent_at (envoi effectif).

Anti-spam (P38 invariant) : envoi email via email_sender (OAuth2 Gmail/Outlook officiel — v1 stub
not_configured comme P35 connectors). JAMAIS de mass-email/spam — une candidature à la fois avec
consentement explicite (POST /applications/{id}/send).
"""
import sqlalchemy as sa

from alembic import op

revision = "0022_p38_candidatures"
down_revision = "0021_p37_cv_adaptatif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("posting_id", sa.String(), nullable=True),
        sa.Column("adapted_cv_id", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("job_title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("application_method", sa.String(), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("timeline", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["posting_id"], ["job_postings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["adapted_cv_id"], ["adapted_cvs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_applications_career_profile_id", "job_applications", ["career_profile_id"])
    op.create_index("ix_job_applications_posting_id", "job_applications", ["posting_id"])
    op.create_index("ix_job_applications_status", "job_applications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_applications_status", table_name="job_applications")
    op.drop_index("ix_job_applications_posting_id", table_name="job_applications")
    op.drop_index("ix_job_applications_career_profile_id", table_name="job_applications")
    op.drop_table("job_applications")
