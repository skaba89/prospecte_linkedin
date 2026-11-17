"""P24-B AI Gateway multi-provider : tables ai_provider_configs + prompt_versions + ai_generations.

Revision ID: 0014_p24b_ai_gateway
Revises: 0013_p24a_security
Create Date: 2026-11-17

Trois tables pour l'AI Gateway indépendant des fournisseurs (OpenAI, Anthropic, Gemini, DeepSeek,
GLM, OpenRouter, Ollama) :

1. ai_provider_configs : config provider par tenant. api_key_enc (Text, chiffré Fernet via
   services/crypto — jamais en clair). priority (ordre fallback). quota_tokens_month +
   cost_usd_month + compteurs tokens_used_month/cost_incurred_month (reset mensuel). base_url
   override (Ollama local, proxy). UniqueConstraint (tenant, provider, label).

2. prompt_versions : prompts versionnés par tenant. content = template {{variables}}. active (un
   seul actif par name). UniqueConstraint (tenant, name, version).

3. ai_generations : historisation complète (provider, model, input/output, tokens, cost, latency,
   status, simulated). simulated=True → provider simulé (jamais présenté comme réel).

Compat SQLite : create_table standards. create_all reste la source de vérité pour les tests
in-memory ; cette migration sert le déploiement PostgreSQL.
"""
import sqlalchemy as sa

from alembic import op

revision = "0014_p24b_ai_gateway"
down_revision = "0013_p24a_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_configs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("quota_tokens_month", sa.Integer(), nullable=True),
        sa.Column("cost_usd_month", sa.Float(), nullable=True),
        sa.Column("tokens_used_month", sa.Integer(), nullable=False),
        sa.Column("cost_incurred_month", sa.Float(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", "label", name="uq_ai_provider_config"),
    )
    op.create_index("ix_ai_provider_configs_tenant_id", "ai_provider_configs", ["tenant_id"])

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("variables", sa.Text(), nullable=True),  # JSON
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_prompt_version"),
    )
    op.create_index("ix_prompt_versions_tenant_id", "prompt_versions", ["tenant_id"])

    op.create_table(
        "ai_generations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("provider_config_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version_id", sa.String(), nullable=True),
        sa.Column("prompt_name", sa.String(), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_config_id"], ["ai_provider_configs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["prompt_versions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_generations_tenant_id", "ai_generations", ["tenant_id"])
    op.create_index("ix_ai_generations_created_at", "ai_generations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_generations_created_at", table_name="ai_generations")
    op.drop_index("ix_ai_generations_tenant_id", table_name="ai_generations")
    op.drop_table("ai_generations")
    op.drop_index("ix_prompt_versions_tenant_id", table_name="prompt_versions")
    op.drop_table("prompt_versions")
    op.drop_index("ix_ai_provider_configs_tenant_id", table_name="ai_provider_configs")
    op.drop_table("ai_provider_configs")
