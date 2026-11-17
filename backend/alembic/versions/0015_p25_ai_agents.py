"""P25 Agents IA spécialisés + AI Copilot : table agent_runs.

Revision ID: 0015_p25_ai_agents
Revises: 0014_p24b_ai_gateway
Create Date: 2026-11-17

Table agent_runs : historisation des runs des 10 agents IA spécialisés (qualification, scoring,
recherche, personnalisation, rédaction LinkedIn, veille concurrentielle, préparation RDV, analyse
performances, recommandation, synthèse). context = JSON (inputs : contact_id, opportunity_id,
message, etc.). result_text = sortie de l'agent. status + simulated (propagé depuis l'AI Gateway
P24-B) + require_human_validation (si injection OU action externe). generation_id = lien vers
l'AIGeneration sous-jacente (audit trail complet P24-B → P25).
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0015_p25_ai_agents"
down_revision = "0014_p24b_ai_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("context", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),  # JSONB PG / Text sqlite
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("require_human_validation", sa.Boolean(), nullable=False),
        sa.Column("generation_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_id"], ["ai_generations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_agent", "agent_runs", ["agent"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_id", table_name="agent_runs")
    op.drop_table("agent_runs")
