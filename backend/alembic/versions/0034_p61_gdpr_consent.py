"""P61 GDPR Art. 7 — ConsentRecord table (proof of consent : qui, quel purpose, quand, quelle version
privacy policy, depuis quelle IP/UA). Consentement explicite + retrait traçable (Art. 7.3).

Revision ID: 0034_p61_gdpr_consent
Revises: 0033_p60_gdpr_erased_at
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_p61_gdpr_consent"
down_revision = "0033_p60_gdpr_erased_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        # purpose : marketing | analytics | third_party_sharing (extensible). Pas d'enum DB (flexibilité).
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),  # null = consent actif ; timestamp = retiré
        sa.Column("privacy_policy_version", sa.String(30), nullable=False),  # ex. "2026-11-18-v1" (Art. 7.2 proof)
        sa.Column("ip", sa.String(64), nullable=True),  # IP au moment du consentement (preuve Art. 7.1)
        sa.Column("user_agent", sa.String(255), nullable=True),  # UA au moment du consentement
    )
    op.create_index("ix_consent_records_tenant_user_purpose", "consent_records",
                    ["tenant_id", "user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_consent_records_tenant_user_purpose", table_name="consent_records")
    op.drop_table("consent_records")
