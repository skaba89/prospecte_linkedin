"""P66 Auth P0 — forgot/reset password (A2) + 2FA backup codes (A3).

Ajoute 3 colonnes à users :
- password_reset_token_hash : bcrypt hash du token de reset (jamais stocké en clair — anti forge).
- password_reset_expires_at : TTL 15 min (anti replay).
- backup_codes_hashed : JSON array de hashes bcrypt des 10 codes de récupération 1-use (anti-lockout
  2FA : perte téléphone = compte récupérable via backup code).

Revision ID: 0036_p66_auth_reset_backup
Revises: 0035_p62_2fa_totp
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_p66_auth_reset_backup"
down_revision = "0035_p62_2fa_totp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_token_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("backup_codes_hashed", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "backup_codes_hashed")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token_hash")
