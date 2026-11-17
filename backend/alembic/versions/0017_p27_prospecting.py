"""P27 Prospection avancée : multicanal sequences + A/B testing + intent signals.

Revision ID: 0017_p27_prospecting
Revises: 0016_p26_content_studio
Create Date: 2026-11-17

Modifications :
  - sequence_steps : add channel (default 'email' — backward compat P15) + channel_config JSON.
    template_id devient nullable (steps non-email : linkedin, call, sms, manual n'ont pas de template).
  - contacts : add intent_score (default 0) + enriched_at (nullable).

Nouvelles tables :
  - sequence_step_variants : variantes A/B d'un step (subject/body override + métriques sent/open/
    click/reply pour calculer les taux par variante).
  - intent_signals : signaux d'intention d'un contact (job_change, funding, hire_spike, news,
    engagement, website_visit) avec score 1-100 + source (manual|webhook|enrichment).

Utilise batch_alter_table pour compat SQLite (rebuild table) + PostgreSQL (alter direct).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0017_p27_prospecting"
down_revision = "0016_p26_content_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── sequence_steps : add channel + channel_config + template_id nullable ──
    with op.batch_alter_table("sequence_steps") as batch_op:
        batch_op.add_column(sa.Column("channel", sa.String(), nullable=False, server_default="email"))
        batch_op.add_column(sa.Column("channel_config", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True))
        batch_op.alter_column("template_id", existing_type=sa.String(), nullable=True)

    # ── contacts : add intent_score + enriched_at ──
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.add_column(sa.Column("intent_score", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("enriched_at", sa.DateTime(), nullable=True))

    # ── sequence_step_variants (A/B testing) ──
    op.create_table(
        "sequence_step_variants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("subject_override", sa.String(), nullable=True),
        sa.Column("body_override", sa.Text(), nullable=True),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("open_count", sa.Integer(), nullable=False),
        sa.Column("click_count", sa.Integer(), nullable=False),
        sa.Column("reply_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["step_id"], ["sequence_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sequence_step_variants_step_id", "sequence_step_variants", ["step_id"])

    # ── intent_signals (détection d'intention) ──
    op.create_table(
        "intent_signals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_intent_signals_tenant_id", "intent_signals", ["tenant_id"])
    op.create_index("ix_intent_signals_contact_id", "intent_signals", ["contact_id"])
    op.create_index("ix_intent_signals_signal_type", "intent_signals", ["signal_type"])


def downgrade() -> None:
    op.drop_index("ix_intent_signals_signal_type", table_name="intent_signals")
    op.drop_index("ix_intent_signals_contact_id", table_name="intent_signals")
    op.drop_index("ix_intent_signals_tenant_id", table_name="intent_signals")
    op.drop_table("intent_signals")
    op.drop_index("ix_sequence_step_variants_step_id", table_name="sequence_step_variants")
    op.drop_table("sequence_step_variants")
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.drop_column("enriched_at")
        batch_op.drop_column("intent_score")
    with op.batch_alter_table("sequence_steps") as batch_op:
        batch_op.alter_column("template_id", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("channel_config")
        batch_op.drop_column("channel")
