#!/usr/bin/env python3
"""test_crm.py — validation statique du CRM P11 (sans API live).

Couvre (par lecture des sources, pas de serveur) :
  - Modèles : 7 classes CRM (Company, Contact, Opportunity, Task, Note, Segment,
    SuppressionList) multi-tenant, FKs, indexes, UniqueConstraint dédup (contact.email,
    suppression.email).
  - Migration 0004 : crée les 7 tables + FKs + indexes + unique constraints.
  - Schemas : Company/Contact/Opportunity/Task/Note/Segment/Suppression + Import/Export/Score/Pipeline.
  - Router crm.py : endpoints CRUD + import/export/pipeline/score/segment/suppression,
    RBAC crm.manage sur les mutations, _normalize_email (dédup), _compute_score (5 critères).
  - RBAC : crm.manage dans Owner/Admin/Editor, PAS dans Contributor/Viewer.
  - Intégration : openpyxl dans requirements, router crm enregistré dans main.py,
    DELETE tenant cascade CRM (7 tables), seed _seed_crm idempotent.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── Modèles CRM (models.py) ──────────────────────────────────────────────────
print("=== Modèles CRM (models.py) ===")
models = read("backend/app/models.py")
for cls in ["Company", "Contact", "Opportunity", "Task", "Note", "Segment", "SuppressionList"]:
    ck(f"class {cls}(Base)" in models, True, f"classe {cls} définie")
# multi-tenant : chaque table a tenant_id FK tenants.id
for tbl in ["companies", "contacts", "opportunities", "tasks", "notes", "segments", "suppression_list"]:
    ck(f'__tablename__ = "{tbl}"' in models, True, f"table {tbl}")
# multi-tenant : chaque table CRM a tenant_id FK tenants.id (compté dans le bloc P11 uniquement,
# borné par le marker P13 pour exclure Notification — table P13 elle aussi tenant-scopée).
_p11, _p13 = "# ─── P11 : CRM", "# ─── P13 : Notifications"
crm_models_block = (models.split(_p11)[1].split(_p13)[0] if _p11 in models else models)
ck(crm_models_block.count('ForeignKey("tenants.id")'), 7,
   "7 tables CRM P11 ont tenant_id FK tenants.id (Notification P13 exclue via marker)")
# Contact : email normalisé + unique par tenant (dédup) + score + status + owner_id
ck('uq_tenant_contact_email' in models, True, "UniqueConstraint contact (tenant_id, email) — dédup")
ck("score = Column(Integer" in models, True, "Contact.score (Integer)")
ck("status = Column(String, default=\"new\"" in models, True, "Contact.status (default new)")
ck("owner_id = Column(String, ForeignKey(\"users.id\")" in models, True, "Contact.owner_id FK users")
ck("source = Column(String" in models, True, "Contact.source (manual/import/linkedin)")
# Opportunity : stage (Kanban) + value + probability + contact_id FK
ck("stage = Column(String, default=\"prospecting\"" in models, True, "Opportunity.stage (pipeline)")
ck("value = Column(Integer" in models, True, "Opportunity.value (montant)")
ck("probability = Column(Integer" in models, True, "Opportunity.probability (%)")
ck("contact_id = Column(String, ForeignKey(\"contacts.id\")" in models, True, "Opportunity.contact_id FK")
# Task : due_date (Date) + status + priority + assignee_id
ck("due_date = Column(Date" in models, True, "Task.due_date (Date)")
ck("priority = Column(String, default=\"medium\"" in models, True, "Task.priority")
ck("assignee_id = Column(String, ForeignKey(\"users.id\")" in models, True, "Task.assignee_id FK users")
# Segment : criteria JSONType
ck("criteria = Column(JSONType" in models, True, "Segment.criteria (JSONType)")
# SuppressionList : email unique par tenant + reason
ck('uq_tenant_suppression_email' in models, True, "UniqueConstraint suppression (tenant_id, email)")
ck("reason = Column(String, nullable=False)" in models, True, "SuppressionList.reason (unsubscribe/bounced/complaint)")

# ─── Migration 0004 ───────────────────────────────────────────────────────────
print("\n=== Migration 0004_p11_crm ===")
mig = read("backend/alembic/versions/0004_p11_crm.py")
ck('revision = "0004_p11_crm"' in mig, True, "revision 0004_p11_crm")
ck('down_revision = "0003_p10_security"' in mig, True, "down_revision 0003_p10_security")
for tbl in ["companies", "contacts", "opportunities", "tasks", "notes", "segments", "suppression_list"]:
    ck(f'op.create_table(\n        "{tbl}"' in mig or f'op.create_table(\n    "{tbl}"' in mig or f'"{tbl}",' in mig, True, f"migration crée table {tbl}")
ck("uq_tenant_contact_email" in mig, True, "migration : unique contact email")
ck("uq_tenant_suppression_email" in mig, True, "migration : unique suppression email")
ck("ForeignKeyConstraint" in mig and mig.count("ForeignKeyConstraint") >= 15,
   True, "migration : ≥15 FKs (tenant + company + contact + owner + assignee + author)")
ck("def downgrade" in mig and "op.drop_table" in mig, True, "migration : downgrade (drop 7 tables)")
# ordre des drops respecte les FKs (enfants avant parents)
drop_order = [l.strip().split('"')[1] for l in mig.splitlines() if "op.drop_table" in l]
ck(drop_order, ["suppression_list", "segments", "notes", "tasks", "opportunities", "contacts", "companies"],
   "downgrade drop order (enfants → parents)")

# ─── Schemas (schemas.py) ─────────────────────────────────────────────────────
print("\n=== Schemas CRM (schemas.py) ===")
sch = read("backend/app/schemas.py")
for s in ["CompanyCreate", "CompanyOut", "ContactCreate", "ContactUpdate", "ContactOut",
          "OpportunityCreate", "OpportunityUpdate", "OpportunityOut", "TaskCreate", "TaskOut",
          "NoteCreate", "NoteOut", "SegmentCreate", "SegmentOut", "SuppressionCreate", "SuppressionOut",
          "ImportResult", "ScoreBreakdown", "PipelineColumn", "StageUpdate"]:
    ck(f"class {s}(BaseModel)" in sch, True, f"schema {s}")
ck("email: EmailStr" in sch, True, "ContactCreate.email EmailStr (validation)")
ck("class ImportResult(BaseModel)" in sch, True, "ImportResult")
ck("created: int" in sch and "duplicates: int" in sch and "suppressed: int" in sch and "errors" in sch,
   True, "ImportResult : created/duplicates/suppressed/errors")
ck("class ScoreBreakdown(BaseModel)" in sch, True, "ScoreBreakdown (score + breakdown)")
ck("class PipelineColumn(BaseModel)" in sch, True, "PipelineColumn (Kanban)")

# ─── Router crm.py ────────────────────────────────────────────────────────────
print("\n=== Router crm.py ===")
crm = read("backend/app/routers/crm.py")
ck('prefix="/api/tenants/{tid}/crm"' in crm, True, "router prefix /api/tenants/{tid}/crm")
# mutations exigent crm.manage
ck(crm.count('require_permission("crm.manage")') >= 15,
   True, f"≥15 mutations protégées par crm.manage (count={crm.count('require_permission(\"crm.manage\")')})")
# endpoints clés présents
for ep in ['/companies"', '/contacts"', '/contacts/export"', '/contacts/export.xlsx"', '/contacts/import"',
           '/contacts/{ctid}"', '/contacts/{ctid}/score"', '/opportunities"',
           '/opportunities/pipeline"', '/opportunities/{oid}/stage"', '/tasks"', '/notes"',
           '/segments"', '/segments/{sid}/contacts"', '/suppression"']:
    ck(ep in crm, True, f"endpoint {ep}")
# dedup : _normalize_email (lowercase + strip)
ck("def _normalize_email" in crm, True, "_normalize_email défini")
ck(".strip().lower()" in crm, True, "email normalisé (strip + lower)")
# scoring : _compute_score 5 critères
ck("def _compute_score" in crm, True, "_compute_score défini")
for crit in ["email", "linkedin", "senior_title", "company_size", "phone"]:
    ck(f'"{crit}"' in crm or f"[\"{crit}\"]" in crm or f"breakdown[\"{crit}\"]" in crm,
       True, f"score critère {crit} (20 pts)")
ck("SENIOR_TITLE_KEYWORDS" in crm, True, "SENIOR_TITLE_KEYWORDS (CEO/CTO/VP/Director/…)")
ck("LARGE_COMPANY_SIZES" in crm, True, "LARGE_COMPANY_SIZES (51-200/201-500/501-1000/1000+)")
ck("min(sum(breakdown.values()), 100)" in crm, True, "score plafonné à 100")
# import : CSV + XLSX + suppression check + dedup
ck("csv.DictReader" in crm, True, "import CSV (csv.DictReader)")
ck("from openpyxl import load_workbook" in crm, True, "import XLSX (openpyxl)")
ck("if email in suppressed" in crm, True, "import skippe suppression list (RGPD)")
ck("if email in existing or email in seen" in crm, True, "import dédup (existants + intra-batch)")
ck("source=\"import\"" in crm, True, "import : source=import")
# export CSV
ck('media_type="text/csv"' in crm, True, "export CSV (media_type text/csv)")
ck("Content-Disposition" in crm, True, "export Content-Disposition (attachment)")
# export XLSX (P85) — openpyxl + helpers partagés + format pro
ck("import openpyxl" in crm, True, "import openpyxl (P85 export XLSX)")
ck("_CRM_EXPORT_COLS" in crm and "_crm_contact_row" in crm, True, "helpers partagés _CRM_EXPORT_COLS + _crm_contact_row (source unique CSV+XLSX)")
ck("spreadsheetml" in crm, True, "media_type xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)")
ck("freeze_panes" in crm, True, "freeze pane A2 + autofilter (XLSX formaté)")
ck(crm.count("export.xlsx") >= 1, True, "endpoint /contacts/export.xlsx déclaré")
# pipeline : 6 stages canoniques
ck("PIPELINE_STAGES" in crm, True, "PIPELINE_STAGES (6 stages)")
ck(crm.count('("prospecting"') >= 1 and "closed_won" in crm and "closed_lost" in crm,
   True, "pipeline 6 stages (prospecting → closed_won/lost)")
# cascade delete contact (opps/tasks/notes)
ck("cascade" in crm.lower() and "opp_ids" in crm, True, "delete contact cascade (opps/tasks/notes)")
# route order : export + import AVANT /contacts/{ctid} (sinon collision FastAPI)
exp_idx = crm.index('@router.get("/contacts/export"')
gc_idx = crm.index('@router.get("/contacts/{ctid}"')
imp_idx = crm.index('@router.post("/contacts/import"')
ck(exp_idx < gc_idx and imp_idx < gc_idx, True, "route order : export/import déclarés avant /contacts/{ctid}")

# ─── RBAC (deps.py) ───────────────────────────────────────────────────────────
print("\n=== RBAC crm.manage (deps.py) ===")
deps = read("backend/app/deps.py")
ck(deps.count('"crm.manage"') >= 3, True,
   f"crm.manage présent ≥3× dans ROLE_PERMISSIONS (count={deps.count('\"crm.manage\"')})")
# Owner/Admin/Editor ont crm.manage
for role_line in ['"Owner":', '"Admin":', '"Editor":']:
    # ligne du rôle contient crm.manage
    line = next((l for l in deps.splitlines() if role_line in l), "")
    ck('"crm.manage"' in line, True, f"{role_line} a crm.manage")
# Contributor + Viewer n'ont PAS crm.manage
contrib_line = next((l for l in deps.splitlines() if '"Contributor":' in l), "")
viewer_line = next((l for l in deps.splitlines() if '"Viewer":' in l), "")
ck('"crm.manage"' not in contrib_line, True, "Contributor n'a PAS crm.manage (read-only)")
ck('"crm.manage"' not in viewer_line, True, "Viewer n'a PAS crm.manage (read-only)")

# ─── Intégration ──────────────────────────────────────────────────────────────
print("\n=== Intégration (main, tenants, seed, requirements) ===")
main = read("backend/app/main.py")
ck("crm" in main.split("from .routers import")[1].split("\n\n")[0], True, "main.py importe router crm (single-line ou vertical)")
ck("crm.router" in main, True, "main.py enregistre crm.router")
reqs = read("backend/requirements.txt")
ck("openpyxl" in reqs, True, "requirements.txt : openpyxl (import XLSX)")
tenants = read("backend/app/routers/tenants.py")
for tbl in ["SuppressionList", "Segment", "Note", "Task", "Opportunity", "Contact", "Company"]:
    ck(f"models.{tbl}).filter_by(tenant_id=tid).delete" in tenants, True, f"DELETE tenant cascade {tbl}")
seed = read("backend/app/seed.py")
ck("def _seed_crm" in seed, True, "seed : _seed_crm défini")
ck("_seed_crm(db, t1.id)" in seed, True, "seed : _seed_crm appelé pour tnt-demo")
ck('"ctc-1"' in seed and '"ctc-2"' in seed, True, "seed : contacts démo (Marie/Albert/…)")
ck('"opp-1"' in seed, True, "seed : opportunité démo")
ck('"seg-1"' in seed, True, "seed : segment démo")
ck('"sup-1"' in seed, True, "seed : suppression démo")
ck("if db.query(models.Contact).filter_by(tenant_id=tenant_id).first():" in seed,
   True, "seed _seed_crm idempotent (skip si contacts existent)")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
