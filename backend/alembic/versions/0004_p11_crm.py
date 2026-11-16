"""P11 CRM : companies, contacts, opportunities, tasks, notes, segments, suppression_list.

Revision ID: 0004_p11_crm
Revises: 0003_p10_security
Create Date: 2026-11-16

CRM multi-tenant de prospection :
  - Company (entreprise) → Contact (prospect, email normalisé unique par tenant = dédup)
  - Contact → Opportunity (affaire en pipeline Kanban : prospecting → ... → closed_won/lost)
  - Task + Note (activités liées à un contact et/ou une opportunité)
  - Segment (filtre sauvegardé, criteria JSON)
  - SuppressionList (exclusions RGPD — l'import skippe ces emails)

Sécurité de la migration :
  - Création seule (aucune modification de table existante, aucune suppression).
  - Toutes les tables scopées par tenant_id (FK tenants.id, indexé).
  - contacts.email + suppression_list.email uniques par tenant (dédup + RGPD).
  - Aucune données seedée par la migration (le seed Python idempotent gère la démo).
"""
import sqlalchemy as sa

from alembic import op

revision = "0004_p11_crm"
down_revision = "0003_p10_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) companies : entreprises/prospect-comptes.
    op.create_table(
        "companies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True, index=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("size", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    # 2) contacts : prospects, email normalisé unique par tenant (dédup).
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("company_id", sa.String(), nullable=True, index=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="new", index=True),
        sa.Column("owner_id", sa.String(), nullable=True, index=True),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.UniqueConstraint("tenant_id", "email", name="uq_tenant_contact_email"),
    )
    # 3) opportunities : affaires en pipeline (Kanban par stage).
    op.create_table(
        "opportunities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("contact_id", sa.String(), nullable=False, index=True),
        sa.Column("company_id", sa.String(), nullable=True, index=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="EUR"),
        sa.Column("stage", sa.String(), nullable=False, server_default="prospecting", index=True),
        sa.Column("probability", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("expected_close_date", sa.Date(), nullable=True, index=True),
        sa.Column("owner_id", sa.String(), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
    )
    # 4) tasks : tâches commerciales (call/demo/relance).
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("contact_id", sa.String(), nullable=True, index=True),
        sa.Column("opportunity_id", sa.String(), nullable=True, index=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default="todo", index=True),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("assignee_id", sa.String(), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
    )
    # 5) notes : notes libres sur contact/opportunité.
    op.create_table(
        "notes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("contact_id", sa.String(), nullable=True, index=True),
        sa.Column("opportunity_id", sa.String(), nullable=True, index=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
    )
    # 6) segments : filtres sauvegardés (criteria JSON).
    op.create_table(
        "segments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("criteria", sa.Text(), nullable=False),  # JSON sérialisé (JSONType côté ORM)
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    # 7) suppression_list : exclusions RGPD (désinscription/bounce/réclamation).
    op.create_table(
        "suppression_list",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "email", name="uq_tenant_suppression_email"),
    )


def downgrade() -> None:
    op.drop_table("suppression_list")
    op.drop_table("segments")
    op.drop_table("notes")
    op.drop_table("tasks")
    op.drop_table("opportunities")
    op.drop_table("contacts")
    op.drop_table("companies")
