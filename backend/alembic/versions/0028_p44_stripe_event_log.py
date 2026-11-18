"""P44 Stripe réel : idempotency webhook (table stripe_event_log).

Revision ID: 0028_p44_stripe_event_log
Revises: 0027_p38_v2_email_oauth
Create Date: 2026-11-18

Nouvelle table (1) :
  - stripe_event_log : idempotency webhook Stripe. PK = stripe event_id (evt_...).
    Anti-replay : handle_webhook_event vérifie la présence avant traitement → un event renvoyé
    par Stripe (retry) n'est JAMAIS retraité (double quota reset / double upgrade évités).
    Colonnes : id (PK = event_id), event_type, processed_at, result (audit court).

Sécurité : un opérateur qui rePOST un webhook Stripe (retry réseau, Stripe renvoie jusqu'à ACK 200)
ne déclenche pas un double traitement. La vérif signature (main.py webhook) + l'idempotency (cette table)
sont les 2 couches anti-abus.

Risques : aucun (table neuve, pas de migration de données existantes).
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_p44_stripe_event_log"
down_revision = "0027_p38_v2_email_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_event_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("result", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stripe_event_log_event_type", "stripe_event_log", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_stripe_event_log_event_type", table_name="stripe_event_log")
    op.drop_table("stripe_event_log")
