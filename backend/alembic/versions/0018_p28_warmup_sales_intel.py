"""P28 Warmup inbox + Sales Intelligence.

Revision ID: 0018_p28_warmup_sales_intel
Revises: 0017_p27_prospecting
Create Date: 2026-11-17

Nouvelles tables :
  - inbox_warmups : configuration de warmup inbox (gradation volume envoi email pour réputation
    expéditeur). sender_email + target_volume + ramp_days + current_day + daily_volume + ramp_status.
  - warmup_messages : messages de warmup (sent_at + opened + replied) pour tracker la réputation
    (open_rate / reply_rate) pendant la rampe.

Pas de modification de tables existantes (P28 est additif — warmup + analytics computed on-the-fly
à partir des Contact/Opportunity/SequenceEnrollment existants).
"""
import sqlalchemy as sa

from alembic import op

revision = "0018_p28_warmup_sales_intel"
down_revision = "0017_p27_prospecting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── inbox_warmups (configuration warmup) ──
    op.create_table(
        "inbox_warmups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("sender_email", sa.String(), nullable=False),
        sa.Column("target_volume", sa.Integer(), nullable=False),
        sa.Column("ramp_days", sa.Integer(), nullable=False),
        sa.Column("current_day", sa.Integer(), nullable=False),
        sa.Column("daily_volume", sa.Integer(), nullable=False),
        sa.Column("ramp_status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inbox_warmups_tenant_id", "inbox_warmups", ["tenant_id"])
    op.create_index("ix_inbox_warmups_ramp_status", "inbox_warmups", ["ramp_status"])

    # ── warmup_messages (tracking réputation) ──
    op.create_table(
        "warmup_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("warmup_id", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("opened", sa.Boolean(), nullable=False),
        sa.Column("replied", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["warmup_id"], ["inbox_warmups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warmup_messages_warmup_id", "warmup_messages", ["warmup_id"])


def downgrade() -> None:
    op.drop_index("ix_warmup_messages_warmup_id", table_name="warmup_messages")
    op.drop_table("warmup_messages")
    op.drop_index("ix_inbox_warmups_ramp_status", table_name="inbox_warmups")
    op.drop_index("ix_inbox_warmups_tenant_id", table_name="inbox_warmups")
    op.drop_table("inbox_warmups")
