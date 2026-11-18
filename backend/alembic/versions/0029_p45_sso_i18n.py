"""P45 SSO Enterprise OIDC + i18n : User/Tenant columns + table enterprise_sso.

Revision ID: 0029_p45_sso_i18n
Revises: 0028_p44_stripe_event_log
Create Date: 2026-11-18

Colonnes ajoutées (3) :
  - users.sso_id (String, index) : sub du fournisseur d'identité (OIDC userinfo.sub).
  - users.identity_provider (String) : "oidc:{issuer}" ex. oidc:https://login.microsoftonline.com/...
  - users.locale (String(8), default "fr") : préférence UI persistée (fr|en|es).
  - users.sso_tenant_id (FK tenants.id, SET NULL) : tenant SSO d'origine (JIT provisioning).
  - tenants.sso_enabled (Boolean, default False) : SSO obligatoire pour ce tenant ?

Nouvelle table (1) :
  - enterprise_sso : config SSO OIDC par tenant (unique tenant_id). client_secret chiffré Fernet.
    Colonnes : id, tenant_id, provider_type (oidc), client_id, client_secret_encrypted,
    issuer_url, authorize_url, token_url, userinfo_url, scopes, domains (JSON), enabled.

Sécurité : client_secret JAMAIS en clair (Fernet via security.encrypt_token). domains contrôle
l'auto-provisioning JIT (un user dont l'email domain n'est pas dans la liste → rejet, pas de création).
state OAuth signé JWT + nonce single-use (anti-CSRF, réutilise OAuthState model de LinkedIn finalize).

Risques : aucun (colonnes nullable/sans défaut cassant — backward compat). Users existants :
sso_id/identity_provider NULL, locale="fr", sso_tenant_id NULL. Tenants existants : sso_enabled=False.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_p45_sso_i18n"
down_revision = "0028_p44_stripe_event_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Colonnes User (SSO + i18n)
    op.add_column("users", sa.Column("sso_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("identity_provider", sa.String(), nullable=True))
    op.add_column("users", sa.Column("locale", sa.String(8), nullable=False, server_default="fr"))
    op.add_column("users", sa.Column("sso_tenant_id", sa.String(), nullable=True))
    op.create_index("ix_users_sso_id", "users", ["sso_id"])
    op.create_foreign_key("fk_users_sso_tenant_id", "users", "tenants",
                          ["sso_tenant_id"], ["id"], ondelete="SET NULL")
    # Colonne Tenant (SSO flag)
    op.add_column("tenants", sa.Column("sso_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    # Table EnterpriseSSO
    op.create_table(
        "enterprise_sso",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("provider_type", sa.String(), nullable=False, server_default="oidc"),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("issuer_url", sa.String(), nullable=False),
        sa.Column("authorize_url", sa.String(), nullable=False),
        sa.Column("token_url", sa.String(), nullable=False),
        sa.Column("userinfo_url", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False, server_default="openid profile email"),
        sa.Column("domains", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_enterprise_sso_tenant_id"),
    )
    op.create_index("ix_enterprise_sso_tenant_id", "enterprise_sso", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_enterprise_sso_tenant_id", table_name="enterprise_sso")
    op.drop_table("enterprise_sso")
    op.drop_column("tenants", "sso_enabled")
    op.drop_constraint("fk_users_sso_tenant_id", "users", type_="foreignkey")
    op.drop_index("ix_users_sso_id", table_name="users")
    op.drop_column("users", "sso_tenant_id")
    op.drop_column("users", "locale")
    op.drop_column("users", "identity_provider")
    op.drop_column("users", "sso_id")
