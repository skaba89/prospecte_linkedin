"""P30 Commercialisation : abonnements, quotas IA plan-based, devises multi-monnaies.

Revision ID: 0026_p30_billing
Revises: 0025_p42_agent
Create Date: 2026-11-17

Nouvelles tables (1) + colonne (1) :
  - subscriptions : état d'abonnement par tenant (1 par tenant, unique tenant_id).
    plan (Free|Starter|Pro|Enterprise), status (active|past_due|canceled|trialing),
    stripe_customer_id, stripe_subscription_id (null si pas de Stripe configuré — mode mock),
    current_period_end (DateTime), ia_calls_used (Int 0 — compteur mensuel), ia_calls_reset_at
    (DateTime — reset mensuel), created_at, updated_at.
    Le quota IA est plan-based : PLANS[plan].ia_calls_month (Free=10, Starter=200, Pro=2000,
    Enterprise=20000). Le guard est dans services/ai/gateway.generate() (check avant provider).
  - tenants.currency : colonne String(8) défaut "EUR" (EUR|USD|GNF|XOF) — devise de facturation
    du tenant (affichage prix + rapports). CareerProfile.currency existe déjà (P34).

Stripe : si STRIPE_SECRET_KEY non configuré → mode mock (checkout retourne une URL de test,
webhook accepte les events de test sans vérif signature). Anti-fake : jamais présenter un paiement
mock comme réel — le retour checkout porte simulated=True quand mock.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_p30_billing"
down_revision = "0025_p42_agent"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, unique=True, index=True),
        sa.Column("plan", sa.String, nullable=False, default="Free"),
        sa.Column("status", sa.String, nullable=False, default="active"),  # active|past_due|canceled|trialing
        sa.Column("stripe_customer_id", sa.String, nullable=True),
        sa.Column("stripe_subscription_id", sa.String, nullable=True),
        sa.Column("current_period_end", sa.DateTime, nullable=True),
        sa.Column("ia_calls_used", sa.Integer, nullable=False, default=0),
        sa.Column("ia_calls_reset_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    # Colonne currency sur tenants (devise de facturation)
    op.add_column("tenants", sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"))


def downgrade():
    op.drop_column("tenants", "currency")
    op.drop_table("subscriptions")
