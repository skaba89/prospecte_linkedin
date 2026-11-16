#!/usr/bin/env python3
"""test_sequences.py — validation statique des séquences & automatisation (P15, sans API live).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - Router sequences.py : 8 endpoints (CRUD + enroll + enrollments + process + stats), RBAC
    crm.manage sur writes / get_tenant sur reads, audit crm.sequence_*, step_order (évite le
    mot-clé SQL réservé `order`), cascade delete (steps + enrollments), 409 si déjà enrollé.
  - Services sequences.py : process_due_enrollments (query due + send + advance), process_sequence
    (manual trigger, filtre sequence_id+tenant_id), start_scheduler (stdlib threading daemon,
    env SEQUENCE_SCHEDULER_INTERVAL_SECONDS défault 300, désactivé si <=0), lazy import
    send_email_to_contact (évite cycle), _OK_STATUSES (sent/dry_run/skipped avancent).
  - Models : EmailSequence, SequenceStep (step_order), SequenceEnrollment (current_step_order,
    next_step_at, status active/completed/paused/failed).
  - Migration 0007 : 3 tables + index ix_enrollments_status_next.
  - Schemas : 8 classes sequence.
  - Intégration : router enregistré dans main.py + start_scheduler appelé dans _startup +
    helper send_email_to_contact extrait de notifications.py (réutilisé P13 endpoint + P15 scheduler).
  - CI : test_sequences.py dans le static checks.
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


# ─── Router sequences.py (8 endpoints + RBAC + audit + cascade) ────────────────
print("=== Router sequences.py (P15) ===")
rt = read("backend/app/routers/sequences.py")
for path in ["/crm/sequences", "/crm/sequences/{sid}", "/crm/sequences/{sid}/enroll",
             "/crm/sequences/{sid}/enrollments", "/crm/sequences/{sid}/process",
             "/crm/sequences/{sid}/stats"]:
    ck(path in rt, True, f"endpoint path {path}")
# 8 endpoints (POST create + GET list sur /sequences ; GET/PATCH/DELETE sur /{sid} ; POST enroll ;
# GET enrollments ; POST process ; GET stats).
ck(rt.count("@router.post") + rt.count("@router.get") + rt.count("@router.patch") + rt.count("@router.delete") >= 9, True,
   "≥9 endpoints P15 (3 POST + 4 GET + 1 PATCH + 1 DELETE — lenient pour stacked PR P16+)")
ck(rt.count('require_permission("crm.manage")') >= 5, True, "≥5 writes RBAC crm.manage (create/update/delete/enroll/process — lenient P16+)")
ck("get_tenant" in rt, True, "get_tenant sur reads (list/detail/enrollments/stats)")
for action in ["crm.sequence_create", "crm.sequence_update", "crm.sequence_delete",
               "crm.sequence_enroll", "crm.sequence_process"]:
    ck(action in rt, True, f"audit {action}")
ck("step_order" in rt, True, "step_order (évite le mot-clé SQL réservé `order`)")
ck("SequenceStep.step_order.asc" in rt, True, "steps triés par step_order asc")
ck('status="active"' in rt and "HTTP_409_CONFLICT" in rt, True, "409 si déjà enrollé (filter_by status=active)")
ck("SequenceStep" in rt and "SequenceEnrollment" in rt, True, "cascade delete steps + enrollments")
ck("HTTP_409_CONFLICT" in rt, True, "409 conflict sur double enroll")
ck("HTTP_400_BAD_REQUEST" in rt, True, "400 validation (≥1 step, template inconnu, statut invalide)")
ck("HTTP_404_NOT_FOUND" in rt, True, "404 séquence/contact introuvable")
ck("_VALID_TEMPLATES" in rt, True, "validation template_id (cold_outreach/follow_up/intro/re_engagement)")
ck("_VALID_STATUSES" in rt, True, "validation status (draft/active/paused)")
ck("process_sequence" in rt, True, "import process_sequence (services) pour endpoint /process")
ck("timedelta(days=first_step.delay_days)" in rt, True, "next_step_at = now + step.delay_days à l'enrollment")
ck('return {"deleted":' in rt, True, "DELETE retourne 200 {deleted, name} (pas 204 — testable via call())")

# ─── Services sequences.py (scheduler + process) ──────────────────────────────
print("\n=== Services sequences.py (scheduler + process) ===")
sv = read("backend/app/services/sequences.py")
ck("def process_due_enrollments" in sv, True, "process_due_enrollments (passe scheduler)")
ck("def process_sequence" in sv, True, "process_sequence (manual trigger, filtre sequence_id+tenant_id)")
ck("def start_scheduler" in sv, True, "start_scheduler")
ck("import threading" in sv, True, "stdlib threading (pas de nouvelle dep — pas APScheduler)")
ck("import time" in sv, True, "time.sleep(interval) — boucle scheduler")
ck("SEQUENCE_SCHEDULER_INTERVAL_SECONDS" in sv, True, "env SEQUENCE_SCHEDULER_INTERVAL_SECONDS (défaut 300)")
ck('"300"' in sv, True, "défaut 300s")
ck("interval <= 0" in sv, True, "désactivé si interval <= 0")
ck("daemon=True" in sv, True, "thread daemon (s'arrête avec le process)")
ck("SessionLocal" in sv, True, "crée sa propre session (background thread, pas de contexte requête)")
ck("from ..routers.notifications import send_email_to_contact" in sv, True, "lazy import send_email_to_contact (évite cycle)")
ck("_OK_STATUSES" in sv and '"sent"' in sv and '"dry_run"' in sv and '"skipped"' in sv, True,
   "_OK_STATUSES = sent/dry_run/skipped (avancent ; failed bloque)")
ck("SequenceEnrollment.status == \"active\"" in sv, True, "query due: status=active")
ck("SequenceEnrollment.next_step_at <= now" in sv, True, "query due: next_step_at <= now")
ck("enr.current_step_order = next_order" in sv, True, "avance current_step_order")
ck("enr.next_step_at = now + timedelta(days=next_step.delay_days)" in sv, True, "calcule next_step_at")
ck('enr.status = "completed"' in sv, True, "dernier step → completed")
ck('enr.status = "failed"' in sv, True, "échec envoi → failed")
ck("EmailSequence.id == sequence_id" in sv and "EmailSequence.tenant_id == tenant_id" in sv,
   True, "process_sequence filtre sequence_id + tenant_id (multi-tenant strict)")
ck('actor_name="Sequence scheduler"' in sv, True, "audit via send_email_to_contact (actor_name=scheduler)")

# ─── Models (P15) ─────────────────────────────────────────────────────────────
print("\n=== Models P15 (EmailSequence/SequenceStep/SequenceEnrollment) ===")
md = read("backend/app/models.py")
for cls in ["EmailSequence", "SequenceStep", "SequenceEnrollment"]:
    ck(f"class {cls}(Base):" in md, True, f"{cls} défini")
ck("email_sequences" in md and "sequence_steps" in md and "sequence_enrollments" in md, True, "3 __tablename__")
ck("step_order = Column(Integer" in md, True, "SequenceStep.step_order (pas `order` — mot-clé réservé)")
ck("ondelete=\"CASCADE\"" in md, True, "CASCADE sur sequence_id (steps + enrollments)")
ck("current_step_order = Column(Integer" in md, True, "SequenceEnrollment.current_step_order")
ck("next_step_at = Column(DateTime" in md, True, "SequenceEnrollment.next_step_at")
ck('default="active"' in md, True, "SequenceEnrollment.status default=active")
ck('default="draft"' in md, True, "EmailSequence.status default=draft")

# ─── Migration 0007 ───────────────────────────────────────────────────────────
print("\n=== Migration 0007_p15_sequences ===")
mig = read("backend/alembic/versions/0007_p15_sequences.py")
ck('revision = "0007_p15_sequences"' in mig, True, "revision 0007_p15_sequences")
ck('down_revision = "0006_p13_notifications"' in mig, True, "down_revision 0006_p13 (P13)")
for tbl in ["email_sequences", "sequence_steps", "sequence_enrollments"]:
    ck(f'op.create_table(\n        "{tbl}"' in mig or f'"{tbl}"' in mig, True, f"create_table {tbl}")
ck("ix_enrollments_status_next" in mig, True, "index composé (status, next_step_at) — lookup scheduler")
ck("def downgrade" in mig and "drop_table" in mig, True, "downgrade (drop 3 tables + index)")
ck("step_order" in mig, True, "colonne step_order (pas `order`)")

# ─── Schemas (P15) ────────────────────────────────────────────────────────────
print("\n=== Schemas P15 ===")
sch = read("backend/app/schemas.py")
for cls in ["SequenceCreate", "SequenceUpdate", "SequenceOut", "SequenceStepCreate",
            "SequenceStepOut", "EnrollRequest", "SequenceEnrollmentOut",
            "SequenceProcessResult", "SequenceStats"]:
    ck(f"class {cls}(BaseModel):" in sch, True, f"{cls} défini")
ck("steps: list[SequenceStepCreate]" in sch, True, "SequenceCreate.steps (nested)")
ck("enrollments_count: int" in sch, True, "SequenceOut.enrollments_count")
ck("SequenceProcessResult" in sch and "processed" in sch and "sent" in sch, True, "SequenceProcessResult stats")

# ─── Helper send_email_to_contact extrait (P13/P15 réutilisé) ─────────────────
print("\n=== Helper send_email_to_contact (extraction P13 → réutilisé P15) ===")
nt = read("backend/app/routers/notifications.py")
ck("def send_email_to_contact(" in nt and "db: Session," in nt, True, "helper send_email_to_contact défini (module-level)")
ck("def email_contact(" in nt, True, "endpoint email_contact toujours présent")
ck("send_email_to_contact(" in nt and "db, tid, contact, payload.template" in nt, True, "endpoint appelle le helper")
ck('result.status == "failed"' in nt and '"Template inconnu"' in nt, True, "endpoint traduit result failed → 400")
ck("raise HTTPException" in nt and "from None" in nt, True, "B904 : raise ... from None (template inconnu)")

# ─── Intégration (main.py + CI) ───────────────────────────────────────────────
print("\n=== Intégration (main.py + CI) ===")
main_src = read("backend/app/main.py")
ck("sequences.router" in main_src, True, "router sequences enregistré dans main.py")
ck("sequences" in main_src.split("from .routers import")[1].split("\n\n")[0], True, "sequences dans l'import routers")
ck("from .services.sequences import start_scheduler" in main_src, True, "start_scheduler importé dans _startup")
ck("start_scheduler()" in main_src, True, "start_scheduler() appelé sur app startup")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_sequences.py" in ci, True, "test_sequences.py dans le CI (static checks)")

# ─── Introspection runtime (endpoints + schemas instanciables) ────────────────
print("\n=== Introspection runtime ===")
sys.path.insert(0, str(ROOT / "backend"))
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via job test-api E2E).")
else:
    try:
        from app.routers import sequences as s
        from app import schemas
        paths = {(r.path, m) for r in s.router.routes for m in (r.methods - {"HEAD", "OPTIONS"})}
        expected = [
            ("/api/tenants/{tid}/crm/sequences", "GET"),
            ("/api/tenants/{tid}/crm/sequences", "POST"),
            ("/api/tenants/{tid}/crm/sequences/{sid}", "GET"),
            ("/api/tenants/{tid}/crm/sequences/{sid}", "PATCH"),
            ("/api/tenants/{tid}/crm/sequences/{sid}", "DELETE"),
            ("/api/tenants/{tid}/crm/sequences/{sid}/enroll", "POST"),
            ("/api/tenants/{tid}/crm/sequences/{sid}/enrollments", "GET"),
            ("/api/tenants/{tid}/crm/sequences/{sid}/process", "POST"),
            ("/api/tenants/{tid}/crm/sequences/{sid}/stats", "GET"),
        ]
        for p, m in expected:
            ck((p, m) in paths, True, f"route {p} [{m}]")
        so = schemas.SequenceOut(id="seq1", tenant_id="t1", name="Drip", description="", status="active",
                                 created_at="2026-11-16T00:00:00", updated_at="2026-11-16T00:00:00",
                                 steps=[schemas.SequenceStepOut(id="s1", step_order=1, template_id="cold_outreach", delay_days=0)])
        ck(so.steps[0].template_id == "cold_outreach", True, "SequenceOut instanciable (nested SequenceStepOut)")
        pr = schemas.SequenceProcessResult(sequence_id="seq1", processed=2, sent=2, skipped=0, failed=0, completed=1)
        ck(pr.sent == 2, True, "SequenceProcessResult instanciable")
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
