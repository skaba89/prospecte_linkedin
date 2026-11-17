"""P34 Career : référentiel métiers + CV (espace candidat isolé du Business).

Revision ID: 0019_p34_career_cv
Revises: 0018_p28_warmup_sales_intel
Create Date: 2026-11-17

Nouvelles tables (3) :
  - career_profiles : profil Career d'un candidat (user_id unique, ISOLÉ du Business tenant — pas de
    tenant_id). Préférences (target_job_title, location, mobility, contract_types, salary/tjm, languages).
  - cvs : CV importés (text|pdf|docx) par candidat. raw_text = texte extrait (pdfminer/python-docx).
    parsed_data = JSON structuré (skills, experiences, certifications, education). versionning + is_active.
  - job_references : référentiel métiers universel (ROME/ESCO/O*NET). code + source + label + category.
    UniqueConstraint (code, source). Seeded via services/career_referential.seed_referential.

Isolation Career (P34) : les tables career_profiles + cvs sont scoped par user_id (pas tenant_id) —
un candidat peut être Member d'un tenant Business (côté prospecteur) ET avoir un CareerProfile
(côté candidat) sans que les données Career soient accessibles via le tenant Business. job_references
est un référentiel global (pas de scope user/tenant).
"""
import sqlalchemy as sa

from alembic import op

revision = "0019_p34_career_cv"
down_revision = "0018_p28_warmup_sales_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── career_profiles (profil candidat isolé du Business) ──
    op.create_table(
        "career_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("target_job_title", sa.String(), nullable=True),
        sa.Column("preferred_location", sa.String(), nullable=True),
        sa.Column("mobility", sa.Boolean(), nullable=False),
        sa.Column("contract_types", sa.JSON(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("tjm_min", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_career_profile_user"),
    )
    op.create_index("ix_career_profiles_user_id", "career_profiles", ["user_id"])

    # ── cvs (CV importés par candidat) ──
    op.create_table(
        "cvs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_filename", sa.String(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("parsed_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cvs_career_profile_id", "cvs", ["career_profile_id"])
    op.create_index("ix_cvs_is_active", "cvs", ["is_active"])

    # ── job_references (référentiel métiers ROME/ESCO/O*NET) ──
    op.create_table(
        "job_references",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "source", name="uq_job_reference_code_source"),
    )
    op.create_index("ix_job_references_code", "job_references", ["code"])


def downgrade() -> None:
    op.drop_index("ix_job_references_code", table_name="job_references")
    op.drop_table("job_references")
    op.drop_index("ix_cvs_is_active", table_name="cvs")
    op.drop_index("ix_cvs_career_profile_id", table_name="cvs")
    op.drop_table("cvs")
    op.drop_index("ix_career_profiles_user_id", table_name="career_profiles")
    op.drop_table("career_profiles")
