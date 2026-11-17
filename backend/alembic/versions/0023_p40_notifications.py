"""P40 Career : Notifications intelligentes (alertes multi-canal + prefs).

Revision ID: 0023_p40_notifications
Revises: 0022_p38_candidatures
Create Date: 2026-11-17

Nouvelle table (1) :
  - career_notifications : notifications Career isolées du Business Notification (tenant_id).
    user_id FK CASCADE + career_profile_id FK CASCADE (isolation stricte Career/Business, P34 invariant).
    type (new_match|application_status|interview_reminder), channel (email|in_app),
    payload JSON (posting_id/application_id/score/message), read_at (nullable), created_at.

Colonnes ajoutées à career_profiles (4) — préférences notification :
  - alert_enabled (Bool, défaut True) : opt-in explicite (anti-spam P40 invariant).
  - alert_min_score (Int, défaut 70) : seuil déclenchant alerte new_match (P36 score ≥ seuil).
  - alert_channels (JSON, défaut ["in_app"]) : canaux activés (email|in_app).
  - alert_frequency (String, défaut "daily") : fréquence (daily|weekly|instant).

Hooks : matching/search génère new_match si score ≥ alert_min_score + alert_enabled ;
application PATCH génère application_status sur changement interview/offer/rejected.
"""
import sqlalchemy as sa

from alembic import op

revision = "0023_p40_notifications"
down_revision = "0022_p38_candidatures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "career_notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("career_profile_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),  # new_match|application_status|interview_reminder
        sa.Column("channel", sa.String(), nullable=False),  # email|in_app
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),  # posting_id/application_id/score/message
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["career_profile_id"], ["career_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_career_notifications_user_id", "career_notifications", ["user_id"])
    op.create_index("ix_career_notifications_read_at", "career_notifications", ["read_at"])
    op.create_index("ix_career_notifications_type", "career_notifications", ["type"])

    # Préférences notification sur career_profiles (opt-in explicite anti-spam)
    op.add_column("career_profiles", sa.Column("alert_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.add_column("career_profiles", sa.Column("alert_min_score", sa.Integer(), nullable=False, server_default=sa.text("70")))
    op.add_column("career_profiles", sa.Column("alert_channels", sa.JSON(), nullable=True))
    op.add_column("career_profiles", sa.Column("alert_frequency", sa.String(), nullable=False, server_default="daily"))


def downgrade() -> None:
    op.drop_column("career_profiles", "alert_frequency")
    op.drop_column("career_profiles", "alert_channels")
    op.drop_column("career_profiles", "alert_min_score")
    op.drop_column("career_profiles", "alert_enabled")
    op.drop_index("ix_career_notifications_type", table_name="career_notifications")
    op.drop_index("ix_career_notifications_read_at", table_name="career_notifications")
    op.drop_index("ix_career_notifications_user_id", table_name="career_notifications")
    op.drop_table("career_notifications")
