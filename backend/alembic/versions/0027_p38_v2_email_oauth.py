"""P38-V2 envoi candidatures réel : intégration email OAuth2 Gmail (authorization code flow).

Revision ID: 0027_p38_v2_email_oauth
Revises: 0026_p30_billing
Create Date: 2026-11-18

Nouvelle table (1) :
  - user_email_integrations : intégration email OAuth2 d'un utilisateur (Gmail/Outlook connecté).
    user_id FK CASCADE (isolation), provider (gmail_oauth|outlook_oauth), email (adresse connectée),
    refresh_token_encrypted (Fernet — long-lived), access_token_encrypted (Fernet — éphémère),
    access_token_expires_at, scopes, created_at, updated_at.
    UniqueConstraint (user_id, provider) : une intégration par provider par utilisateur.

OAuth2 Gmail (3-legged authorization code flow) :
  1. GET /api/career/email/connect/gmail_oauth → redirect Google consent (scope gmail.send).
  2. Google callback → /api/career/email/oauth/callback?code=XXX&state=XXX.
  3. Backend exchange code → access_token + refresh_token (POST oauth2.googleapis.com/token).
  4. refresh_token chiffré Fernet stocké en DB (long-lived). access_token chiffré + expires_at.
  5. send_application_email : si user a intégration Gmail → refresh si expiré → Gmail API send.
     Sinon → status not_connected (pas not_configured — le connecteur est configuré, l'user n'a pas connecté).

Sécurité (P24-A) : refresh_token + access_token JAMAIS en clair en DB (Fernet). state OAuth anti-CSRF
(réutilise OAuthState P10.3). disconnect() supprime la ligne (révocation user-side).
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_p38_v2_email_oauth"
down_revision = "0026_p30_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_email_integrations",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("provider", sa.String, nullable=False, index=True),  # gmail_oauth|outlook_oauth
        sa.Column("email", sa.String, nullable=True),  # adresse Gmail connectée (tokeninfo)
        sa.Column("refresh_token_encrypted", sa.Text, nullable=True),  # Fernet — long-lived
        sa.Column("access_token_encrypted", sa.Text, nullable=True),  # Fernet — éphémère
        sa.Column("access_token_expires_at", sa.DateTime, nullable=True),
        sa.Column("scopes", sa.String, nullable=True),  # "gmail.send" ou "https://mail.google.com/"
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_email_integration_provider"),
    )


def downgrade():
    op.drop_table("user_email_integrations")
