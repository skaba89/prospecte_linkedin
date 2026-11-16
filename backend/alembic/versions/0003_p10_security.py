"""P10 security : oauth_states (nonce single-use) + revoked_tokens (JIT blacklist)
+ publications (dédup + statut durable) + chiffrement des access_tokens OAuth existants.

Revision ID: 0003_p10_security
Revises: 0002_audit_log
Create Date: 2026-11-16

Sécurité de la migration :
  - Aucune suppression de données métier (création de 3 tables + UPDATE idempotent).
  - Le chiffrement des tokens existants détecte le préfixe Fernet (gAAAA) → skip si déjà
    chiffré, et SKIP entier si REVPULSE_TOKEN_ENCRYPTION_KEY est absent/invalide (fresh
    dev/test DB sans tokens réels → no-op). En production, la clé DOIT être définie avant
    la migration (fail-fast dans config.Settings._fail_fast_secrets).
"""
import os

import sqlalchemy as sa

from alembic import op

revision = "0003_p10_security"
down_revision = "0002_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) oauth_states : nonce à usage unique (anti-rejeu du state OAuth).
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("nonce", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    # 2) revoked_tokens : blacklist JIT (logout / révocation).
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True, index=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("jti"),
    )
    # 3) publications : trace durable + dédup (content_hash sha256) des publications LinkedIn.
    op.create_table(
        "publications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("approval_id", sa.String(), nullable=True, index=True),
        sa.Column("kind", sa.String(), nullable=False),  # post | comment | dm
        sa.Column("content_hash", sa.String(), nullable=False, index=True),
        sa.Column("linkedin_urn", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),  # published | failed | pending
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    # 4) Chiffrement des access_tokens OAuth existants (idempotent, safe, no-op sans clé).
    _encrypt_existing_tokens()


def _encrypt_existing_tokens() -> None:
    """Chiffre sur place les access_tokens en clair. Idempotent : détecte le préfixe
    Fernet (gAAAA) et skip. No-op si la clé est absente/invalide (dev/test)."""
    key = os.environ.get("REVPULSE_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        return
    try:
        from cryptography.fernet import Fernet

        f = Fernet(key.encode())
    except Exception:
        return
    bind = op.get_bind()
    try:
        rows = bind.execute(sa.text("SELECT id, access_token FROM linkedin_connections")).fetchall()
    except Exception:
        return
    for rid, tok in rows:
        if not tok or str(tok).startswith("gAAAA"):
            continue
        try:
            enc = f.encrypt(str(tok).encode()).decode()
            bind.execute(
                sa.text("UPDATE linkedin_connections SET access_token = :enc WHERE id = :id"),
                {"enc": enc, "id": rid},
            )
        except Exception:
            pass


def downgrade() -> None:
    op.drop_table("publications")
    op.drop_table("revoked_tokens")
    op.drop_table("oauth_states")
