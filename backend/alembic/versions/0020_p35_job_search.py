"""P35 Career : recherche d'opportunités (connecteurs + job_postings + saved_searches).

Revision ID: 0020_p35_job_search
Revises: 0019_p34_career_cv
Create Date: 2026-11-17

Nouvelles tables (2) :
  - job_postings : offres d'emploi cacheées issues des connecteurs (LinkedIn Jobs, Free-Work, Malt,
    LeHibou, France Travail, Apec, Indeed, Hays, Welcome to the Jungle, Upwork, sites carrières,
    plateformes africaines/internationales). dedup_hash = sha256(title+company+location) pour
    détecter les doublons cross-connecteurs. UniqueConstraint (dedup_hash, source_connector).
  - saved_searches : recherches sauvegardées d'un candidat (criteria JSON + alert_enabled +
    alert_frequency daily|weekly). career_profile_id FK (CASCADE) — isolation Career (user-scoped).

Sécurité (P35) :
  - Les connecteurs utilisent UNIQUEMENT les API/flux/méthodes autorisés (official API, RSS, partner
    agreement). AUCUN scraping anti-bot (respect des ToS des plateformes — LinkedIn, Indeed, etc.).
  - Un connecteur non configuré (pas de credentials) retourne status="not_configured" + 0 postings —
    JAMAIS de faux succès (postings inventés). Les postings sont persistés seulement si retournés par
    un connecteur configuré.
"""
import sqlalchemy as sa

from alembic import op

revision = "0020_p35_job_search"
down_revision = "0019_p34_career_cv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── job_postings (offres cacheées des connecteurs) ──
    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dedup_hash", sa.String(), nullable=False),
        sa.Column("source_connector", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contract_type", sa.String(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("is_expired", sa.Boolean(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_hash", "source_connector", name="uq_job_posting_dedup_source"),
    )
    op.create_index("ix_job_postings_dedup_hash", "job_postings", ["dedup_hash"])
    op.create_index("ix_job_postings_source_connector", "job_postings", ["source_connector"])
    op.create_index("ix_job_postings_is_expired", "job_postings", ["is_expired"])

    # ── saved_searches (recherches sauvegardées candidat) ──
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("alert_enabled", sa.Boolean(), nullable=False),
        sa.Column("alert_frequency", sa.String(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_postings_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_searches_career_profile_id", "saved_searches", ["career_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_searches_career_profile_id", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_index("ix_job_postings_is_expired", table_name="job_postings")
    op.drop_index("ix_job_postings_source_connector", table_name="job_postings")
    op.drop_index("ix_job_postings_dedup_hash", table_name="job_postings")
    op.drop_table("job_postings")
