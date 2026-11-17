"""P24-A Sécurisation résiduelle : webhooks.secret_enc (encrypt-at-rest) + table inbound_events.

Revision ID: 0013_p24a_security
Revises: 0012_p24_analytics
Create Date: 2026-11-17

Deux changements :

1. webhooks.secret_enc (Text, nullable) : colonne pour le secret HMAC chiffré Fernet (P24-A
   crypto.py). Le secret plaintext legacy (webhooks.secret, String NOT NULL) est conservé pour
   backward-compat — lazy-migré vers secret_enc à la prochaine lecture (services/webhooks._get_secret)
   puis clear vers "". Aucun backfill rétroactif (les webhooks existants gardent secret jusqu'au
   prochain accès). Pas d'ALTER de la contrainte NOT NULL sur secret (nouveau rows set secret="").

2. inbound_events (nouvelle table) : idempotence des webhooks inbound. Un row par
   (tenant_id, webhook_id, event_id) traité. UniqueConstraint(tenant_id, webhook_id, event_id) →
   IntegrityError sur doublon → le router retourne le résultat précédent (skip idempotent).
   FK CASCADE sur tenant + inbound_webhook. event_id vient du header X-Webhook-Id / Webhook-Id
   OU du champ payload event_id/messageId.

Compat SQLite : add_column + create_table standards (pas de batch_alter nécessaire). create_all
 reste la source de vérité pour les tests in-memory ; cette migration sert le déploiement PostgreSQL.
"""
import sqlalchemy as sa

from alembic import op

revision = "0013_p24a_security"
down_revision = "0012_p24_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Colonne secret_enc sur webhooks (secret chiffré Fernet, lazy-migré depuis secret plaintext).
    op.add_column("webhooks", sa.Column("secret_enc", sa.Text(), nullable=True))
    # 2. Table inbound_events (idempotence des webhooks inbound).
    op.create_table(
        "inbound_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("webhook_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["webhook_id"], ["inbound_webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "webhook_id", "event_id", name="uq_inbound_event"),
    )
    op.create_index("ix_inbound_events_tenant_id", "inbound_events", ["tenant_id"])
    op.create_index("ix_inbound_events_webhook_id", "inbound_events", ["webhook_id"])


def downgrade() -> None:
    op.drop_index("ix_inbound_events_webhook_id", table_name="inbound_events")
    op.drop_index("ix_inbound_events_tenant_id", table_name="inbound_events")
    op.drop_table("inbound_events")
    op.drop_column("webhooks", "secret_enc")
