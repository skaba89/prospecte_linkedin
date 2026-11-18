"""P68 Auth P0 — email verification (A5) + SSO email_verified gate (A4, anti takeover).

Ajoute User.email_verified (Boolean, server_default true — les users EXISTANTS restent vérifiés,
backward compat). Les nouvelles inscriptions via /auth/register ont email_verified=False jusqu'à
validation du token email (24h). Le gate SSO (_find_or_create_sso_user) refuse de lier un SSO à un
User existant non vérifié — anti takeover : un attaquant ne peut pas créer un compte local avec
l'email de la victime (non vérifié) puis lier son SSO pour prendre le compte.

Revision ID: 0037_p68_email_verified
Revises: 0036_p66_auth_reset_backup
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_p68_email_verified"
down_revision = "0036_p66_auth_reset_backup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default=true : les users existants (créés avant P68) restent email_verified=True (backward compat).
    # Les nouveaux users via /auth/register ont email_verified=False (set explicite dans le code register).
    op.add_column("users", sa.Column("email_verified", sa.Boolean(),
                                      server_default=sa.text("true"), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "email_verified")
