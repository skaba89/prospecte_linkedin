#!/usr/bin/env python3
"""test_segments_dyn.py — validation statique du ciblage avancé + batch enrollment (P16).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - crm.py : _apply_contact_filters étendu (has_email, no_email_days, enrolled_in_sequence),
    sous-requêtes NOT EXISTS (Notification pour no_email_days, SequenceEnrollment pour
    enrolled_in_sequence), filtre placeholder (@linkedin.sync, @local.revpulse.io).
    list_contacts : 3 nouveaux query params. apply_segment : passe les nouveaux criteria.
  - sequences.py : endpoint /enroll-batch (segment_id OU contact_ids), _CRITERIA_KEYS
    (filtrage safe **kwargs), import _apply_contact_filters depuis crm, skip déjà-active,
    audit crm.sequence_enroll_batch.
  - schemas.py : EnrollBatchRequest (segment_id + contact_ids), EnrollBatchResult
    (enrolled + skipped + total).
  - Pas de migration (criteria est JSON — les nouvelles clés sont des données, pas de schéma).
  - CI : test_segments_dyn.py dans static checks.
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


# ─── crm.py : _apply_contact_filters étendu ───────────────────────────────────
print("=== crm.py : _apply_contact_filters étendu (P16) ===")
crm = read("backend/app/routers/crm.py")
ck("has_email=None" in crm, True, "param has_email dans signature _apply_contact_filters")
ck("no_email_days=None" in crm, True, "param no_email_days")
ck("enrolled_in_sequence=None" in crm, True, "param enrolled_in_sequence")
ck("from sqlalchemy import exists, or_" in crm, True, "import exists (sous-requêtes NOT EXISTS)")
ck("from datetime import datetime, timedelta" in crm, True, "import timedelta (cutoff no_email_days)")
# has_email : filtre placeholder @linkedin.sync + @local.revpulse.io.
ck('models.Contact.email.like("%@linkedin.sync")' in crm, True, "has_email exclut placeholder @linkedin.sync")
ck('models.Contact.email.like("%@local.revpulse.io")' in crm, True, "has_email exclut placeholder @local.revpulse.io")
ck("has_email is True" in crm and "has_email is False" in crm, True, "has_email True/False (bool tri-state)")
# no_email_days : NOT EXISTS Notification type=email created_at > cutoff.
ck("no_email_days is not None" in crm, True, "no_email_days guard")
ck("cutoff = datetime.utcnow() - timedelta(days=no_email_days)" in crm, True, "cutoff calculé")
ck("models.Notification.recipient_contact_id == models.Contact.id" in crm, True, "NOT EXISTS subquery Notification")
ck('models.Notification.type == "email"' in crm, True, "filtre type=email")
ck("models.Notification.created_at > cutoff" in crm, True, "filtre created_at > cutoff")
ck("~exists()" in crm, True, "NOT EXISTS (dormants)")
# enrolled_in_sequence : NOT EXISTS SequenceEnrollment active.
ck("enrolled_in_sequence is not None" in crm, True, "enrolled_in_sequence guard")
ck("models.SequenceEnrollment.contact_id == models.Contact.id" in crm, True, "NOT EXISTS subquery SequenceEnrollment")
ck("models.SequenceEnrollment.sequence_id == enrolled_in_sequence" in crm, True, "filtre sequence_id")
ck('models.SequenceEnrollment.status == "active"' in crm, True, "filtre status=active (NON enrollé)")

# ─── crm.py : list_contacts + apply_segment ───────────────────────────────────
print("\n=== crm.py : list_contacts + apply_segment (P16) ===")
ck("has_email: bool | None = Query(None)" in crm, True, "list_contacts query param has_email")
ck("no_email_days: int | None = Query(None, ge=0)" in crm, True, "list_contacts query param no_email_days")
ck("enrolled_in_sequence: str | None = Query(None)" in crm, True, "list_contacts query param enrolled_in_sequence")
ck("has_email=has_email, no_email_days=no_email_days" in crm, True, "list_contacts passe les nouveaux filtres")
ck('criteria.get("has_email")' in crm, True, "apply_segment lit criteria has_email")
ck('criteria.get("no_email_days")' in crm, True, "apply_segment lit criteria no_email_days")
ck('criteria.get("enrolled_in_sequence")' in crm, True, "apply_segment lit criteria enrolled_in_sequence")

# ─── sequences.py : endpoint enroll-batch ─────────────────────────────────────
print("\n=== sequences.py : enroll-batch (P16) ===")
seq = read("backend/app/routers/sequences.py")
ck("/crm/sequences/{sid}/enroll-batch" in seq, True, "endpoint path /enroll-batch")
ck("def enroll_batch(" in seq, True, "fonction enroll_batch définie")
ck("from .crm import _apply_contact_filters" in seq, True, "import _apply_contact_filters depuis crm")
ck("_CRITERIA_KEYS" in seq and '"has_email"' in seq and '"no_email_days"' in seq and '"enrolled_in_sequence"' in seq,
   True, "_CRITERIA_KEYS (filtrage safe **kwargs)")
ck("payload.segment_id" in seq, True, "résolution via segment_id")
ck("payload.contact_ids" in seq, True, "résolution via contact_ids")
ck('models.Contact.id.in_(payload.contact_ids)' in seq, True, "filtrage contact_ids in (...) ")
ck("HTTP_400_BAD_REQUEST" in seq and "segment_id ou contact_ids requis" in seq, True, "400 si ni segment_id ni contact_ids")
ck('status="active"' in seq, True, "skip déjà-active (filter_by status=active)")
ck("skipped += 1" in seq, True, "compteur skipped")
ck("enrolled += 1" in seq, True, "compteur enrolled")
ck("crm.sequence_enroll_batch" in seq, True, "audit crm.sequence_enroll_batch")
ck("schemas.EnrollBatchResult(enrolled=" in seq, True, "retourne EnrollBatchResult")

# ─── schemas.py : EnrollBatchRequest/Result ───────────────────────────────────
print("\n=== schemas.py : EnrollBatchRequest/Result (P16) ===")
sch = read("backend/app/schemas.py")
ck("class EnrollBatchRequest(BaseModel):" in sch, True, "EnrollBatchRequest défini")
ck("segment_id: str | None = None" in sch, True, "segment_id nullable")
ck("contact_ids: list[str] = []" in sch, True, "contact_ids list")
ck("class EnrollBatchResult(BaseModel):" in sch, True, "EnrollBatchResult défini")
ck("enrolled: int" in sch and "skipped: int" in sch and "total: int" in sch, True, "enrolled/skipped/total fields")

# ─── Pas de migration (criteria est JSON) ─────────────────────────────────────
print("\n=== Pas de migration P16 (criteria JSON) ===")
migs = list((ROOT / "backend/alembic/versions").glob("*.py"))
ck(any("p16" in f.name.lower() for f in migs), False, "AUCUNE migration P16 (criteria JSON = données, pas de schéma)")

# ─── CI : test_segments_dyn.py ────────────────────────────────────────────────
print("\n=== CI : test_segments_dyn.py ===")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_segments_dyn.py" in ci, True, "test_segments_dyn.py dans static checks CI")

# ─── Introspection runtime (route enroll-batch + filtres) ─────────────────────
print("\n=== Introspection runtime ===")
sys.path.insert(0, str(ROOT / "backend"))
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    try:
        from app.routers import sequences as s
        from app.routers.crm import _apply_contact_filters
        from app import schemas
        import inspect
        params = set(inspect.signature(_apply_contact_filters).parameters)
        for p in ["has_email", "no_email_days", "enrolled_in_sequence"]:
            ck(p in params, True, f"_apply_contact_filters paramètre {p}")
        paths = {(r.path, m) for r in s.router.routes for m in (r.methods - {"HEAD", "OPTIONS"})}
        ck(("/api/tenants/{tid}/crm/sequences/{sid}/enroll-batch", "POST") in paths, True,
           "route /enroll-batch [POST] enregistrée")
        eb = schemas.EnrollBatchRequest(segment_id="seg1")
        ck(eb.segment_id == "seg1" and eb.contact_ids == [], True, "EnrollBatchRequest instanciable")
        res = schemas.EnrollBatchResult(enrolled=5, skipped=2, total=7)
        ck(res.total == 7, True, "EnrollBatchResult instanciable")
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
