"""P62 2FA/TOTP (RFC 6238) — add User.totp_secret_encrypted (Fernet) + User.totp_enabled (Boolean).

Revision ID: 0035_p62_2fa_totp
Revises: 0034_p61_gdpr_consent
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_p62_2fa_totp"
down_revision = "0034_p61_gdpr_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # totp_secret_encrypted : secret base32 pyotp chiffré Fernet (jamais en clair en DB).
    # totp_enabled : False par défaut — passe à True après verify (anti-lockout : setup sans verify = no-op).
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
