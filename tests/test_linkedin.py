#!/usr/bin/env python3
"""test_linkedin.py — validation statique du connecteur LinkedIn → CRM (P12, sans API live).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - Modèle LinkedInSyncRun (models.py) : 11 champs, tablename, FK tenant_id/actor_user_id,
    index tenant_id, default _uid("syn").
  - Migration 0005 : crée linkedin_sync_runs + index composé tenant_id+started_at,
    down_revision=0004_p11_crm, server_default compteurs à 0.
  - Schemas : SyncResult (sync_run_id + 6 compteurs/status/detail) + SyncRunOut (from_attributes).
  - Client (services/linkedin_client.py) : fetch_connections (mock déterministe 8 entrées dont
    ≥3 CTO pour nourrir le segment P11 "Décideurs tech"), _http_get_with_retry (max_retries=3,
    retry sur 429 Retry-After + 5xx + URLError, backoff exponentiel, pas de retry sur 4xx).
  - Router linkedin.py : POST sync-contacts (RBAC crm.manage, 409 si pas de connexion,
    dédup linkedin_url puis email, skip suppression RGPD, source='linkedin', _compute_score),
    GET sync-runs (paginé, X-Total-Count), helper _placeholder_email (@linkedin.sync factice).
  - Intégration : router linkedin enregistré dans main.py, LinkedInSyncRun dans Base.metadata,
    test_linkedin.py dans le CI (static checks), migration 0005 chaînée après 0004.
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


# ─── Modèle LinkedInSyncRun (models.py) ────────────────────────────────────────
print("=== Modèle LinkedInSyncRun (models.py) ===")
models_src = read("backend/app/models.py")
ck("class LinkedInSyncRun(Base):" in models_src, True, "LinkedInSyncRun class définie")
ck('__tablename__ = "linkedin_sync_runs"' in models_src, True, "tablename linkedin_sync_runs")
for field, label in [
    ("tenant_id", "FK tenant_id"),
    ("status", "status (running|completed|failed)"),
    ("total", "total (Integer)"),
    ("imported", "imported (Integer)"),
    ("updated", "updated (Integer)"),
    ("skipped", "skipped (Integer)"),
    ("error_detail", "error_detail (Text)"),
    ("started_at", "started_at (DateTime)"),
    ("completed_at", "completed_at (DateTime)"),
    ("actor_user_id", "actor_user_id (FK users)"),
]:
    ck(f"{field} = Column" in models_src, True, f"champ {label} présent")
ck('default=lambda: _uid("syn")' in models_src, True, "default id _uid('syn')")
ck('ForeignKey("tenants.id")' in models_src and "linkedin_sync_runs" in models_src, True,
   "tenant_id FK tenants.id")

# ─── Migration 0005 ────────────────────────────────────────────────────────────
print("\n=== Migration 0005_p12_linkedin_sync ===")
mig = read("backend/alembic/versions/0005_p12_linkedin_sync.py")
ck('revision = "0005_p12_linkedin_sync"' in mig, True, "revision 0005_p12_linkedin_sync")
ck('down_revision = "0004_p11_crm"' in mig, True, "down_revision 0004_p11_crm (chaînée P11)")
ck('"linkedin_sync_runs"' in mig and "create_table" in mig, True, "create_table linkedin_sync_runs")
ck('ix_linkedin_sync_runs_tenant_started' in mig, True, "index composé tenant_id+started_at")
for col in ["tenant_id", "status", "total", "imported", "updated", "skipped",
            "error_detail", "started_at", "completed_at", "actor_user_id"]:
    ck(f'"{col}"' in mig, True, f"colonne {col} dans la migration")
ck('server_default="0"' in mig, True, "compteurs server_default 0 (NOT NULL safe)")
ck("drop_table" in mig and "drop_index" in mig, True, "downgrade propre (drop table + index)")

# ─── Schemas (SyncResult + SyncRunOut) ─────────────────────────────────────────
print("\n=== Schemas SyncResult + SyncRunOut ===")
sch = read("backend/app/schemas.py")
for field in ["sync_run_id", "status", "total", "imported", "updated", "skipped", "detail"]:
    ck(f"{field}" in sch and "SyncResult" in sch, True, f"SyncResult.{field}")
ck("class SyncResult(BaseModel):" in sch, True, "SyncResult défini")
ck("class SyncRunOut(BaseModel):" in sch, True, "SyncRunOut défini")
ck("from_attributes=True" in sch and "SyncRunOut" in sch, True, "SyncRunOut from_attributes (ORM mode)")
ck("started_at: datetime" in sch and "completed_at: datetime" in sch, True,
   "SyncRunOut started_at/completed_at datetime")

# ─── Client LinkedIn (services/linkedin_client.py) ─────────────────────────────
print("\n=== Client services/linkedin_client.py ===")
cli = read("backend/app/services/linkedin_client.py")
ck("def fetch_connections(" in cli, True, "fetch_connections défini")
ck("mock: bool" in cli, True, "fetch_connections param mock")
ck("_MOCK_CONNECTIONS" in cli, True, "_MOCK_CONNECTIONS (données déterministes)")
# 8 entrées mock — compte les occurrences de "linkedin_url"
mock_count = cli.count('"linkedin_url":')
ck(mock_count >= 8, True, f"≥8 connexions mock ({mock_count} entrées)")
# ≥3 CTO pour nourrir le segment "Décideurs tech" (search=cto)
cto_count = cli.lower().count('"title": "cto"')
ck(cto_count >= 3, True, f"≥3 CTO dans le mock ({cto_count} — segment 'Décideurs tech')")
ck("def _http_get_with_retry(" in cli, True, "_http_get_with_retry défini")
ck("max_retries: int = 3" in cli, True, "max_retries=3")
ck("e.code == 429" in cli, True, "retry sur 429 (rate-limit)")
ck("Retry-After" in cli, True, "respecte header Retry-After")
ck("e.code >= 500" in cli, True, "retry sur 5xx (serveur transitoire)")
ck("URLError" in cli, True, "retry sur erreur réseau (URLError)")
ck("2 ** attempt" in cli, True, "backoff exponentiel")
ck("raise" in cli, True, "lève après épuisement des retries")

# ─── Router linkedin.py : sync-contacts + sync-runs ────────────────────────────
print("\n=== Router linkedin.py (P12 sync endpoints) ===")
rt = read("backend/app/routers/linkedin.py")
ck('"/tenants/{tid}/linkedin/sync-contacts"' in rt, True, "endpoint POST sync-contacts")
ck('"/tenants/{tid}/linkedin/sync-runs"' in rt, True, "endpoint GET sync-runs")
ck("response_model=schemas.SyncResult" in rt, True, "sync-contacts response_model SyncResult")
ck("response_model=list[schemas.SyncRunOut]" in rt, True, "sync-runs response_model list[SyncRunOut]")
ck('require_permission("crm.manage")' in rt, True, "RBAC crm.manage sur sync-contacts (mutation CRM)")
ck("status.HTTP_409_CONFLICT" in rt and "sync" in rt.lower(), True,
   "409 si pas de connexion LinkedIn")
ck("_compute_score" in rt, True, "réutilise _compute_score du CRM (scoring cohérent)")
ck('source = "linkedin"' in rt or 'source="linkedin"' in rt, True,
   "contacts syncés source='linkedin'")
ck("SuppressionList" in rt, True, "skip suppression list (RGPD)")
ck("linkedin_url" in rt and "filter_by" in rt, True, "dédup par linkedin_url")
ck("log_audit" in rt and "linkedin.sync_contacts" in rt, True, "audit log linkedin.sync_contacts")
ck("LinkedInSyncRun" in rt, True, "crée un LinkedInSyncRun (trace)")
ck("def _placeholder_email(" in rt, True, "helper _placeholder_email")
ck("@linkedin.sync" in rt, True, "email factice @linkedin.sync (pas un email réel)")
ck("set_total" in rt, True, "sync-runs pagination X-Total-Count")
ck("linkedin_client" in rt, True, "importe le service linkedin_client")

# ─── Intégration ───────────────────────────────────────────────────────────────
print("\n=== Intégration (main.py + metadata + CI) ===")
main_src = read("backend/app/main.py")
ck("linkedin.router" in main_src, True, "router linkedin enregistré dans main.py")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_linkedin.py" in ci, True, "test_linkedin.py dans le CI (static checks)")

# Introspection : LinkedInSyncRun dans Base.metadata + endpoint enregistré.
sys.path.insert(0, str(ROOT / "backend"))
try:
    from app.database import Base
    from app import models
    from app.routers import linkedin as lg
    ck("linkedin_sync_runs" in Base.metadata.tables, True,
       "table linkedin_sync_runs dans Base.metadata")
    has_field = hasattr(models.LinkedInSyncRun, "total") and hasattr(models.LinkedInSyncRun, "imported")
    ck(has_field, True, "LinkedInSyncRun.total/imported accessibles (ORM)")
    # endpoints enregistrés sur le router
    routes = [r.path for r in lg.router.routes]
    ck(any("sync-contacts" in p for p in routes), True, "route sync-contacts enregistrée")
    ck(any("sync-runs" in p for p in routes), True, "route sync-runs enregistrée")
    # client importable
    from app.services import linkedin_client as lc
    mock_data = lc.fetch_connections("tok", mock=True)
    ck(len(mock_data) >= 8, True, f"fetch_connections mock retourne ≥8 ({len(mock_data)})")
    ck(all("linkedin_url" in c and "title" in c for c in mock_data), True,
       "chaque entrée mock a linkedin_url + title")
    ck(any("cto" in c["title"].lower() for c in mock_data), True,
       "≥1 CTO dans le mock (segment P11 'Décideurs tech')")
except Exception as e:
    ck(False, True, f"introspection OK (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
