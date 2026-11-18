#!/usr/bin/env python3
"""test_p35_job_search.py — validation statique + runtime de P35 Career : recherche d'opportunités.

Couvre :
  - models.py : JobPosting (offres cacheées, dedup_hash), SavedSearch (recherches sauvegardées candidat).
  - migration 0020 : 2 nouvelles tables (job_postings, saved_searches) — isolation Career (saved_searches
    FK career_profile_id CASCADE ; job_postings sans FK — connecteurs externes).
  - services/job_connectors/ : base (SearchCriteria, ConnectorResult, JobPostingData, JobConnector ABC),
    registry (12 providers : linkedin_jobs, freework, malt, lehibou, france_travail, apec, indeed, hays,
    wttj, upwork, career_sites, africa). Stubs v1 — status not_configured si credential absent.
  - services/job_search.py : search_jobs (orchestrateur + dedup hash + persist + filter), detect_expired
    (heuristic 30 jours), run_saved_search, dedup_hash (sha256 title+company+location normalisé).
  - routers/career.py : 7 nouveaux endpoints (jobs/connectors, jobs/search, jobs/postings, saved-searches
    CRUD + run).
  - Runtime : 12 connectors registered, dedup_hash déterministe, search_jobs (connecteurs not_configured
    → 0 postings, JAMAIS de faux succès), saved search create + run.
"""
import os
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


print("=== test_p35_job_search.py — P35 Career : recherche d'opportunités ===\n")

# ─── Source : models.py (JobPosting + SavedSearch) ───────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : JobPosting + SavedSearch ===")
ck("class JobPosting(Base):" in models_src, True, "JobPosting model")
ck('__tablename__ = "job_postings"' in models_src, True, "table job_postings")
ck("dedup_hash = Column(String" in models_src, True, "JobPosting.dedup_hash (dédup cross-connecteurs)")
ck("source_connector = Column(String" in models_src, True, "JobPosting.source_connector (linkedin_jobs|...)")
ck("external_id = Column(String" in models_src, True, "JobPosting.external_id (ID chez le connecteur)")
ck("is_expired = Column(Boolean" in models_src, True, "JobPosting.is_expired (détection expiré)")
ck("raw_data = Column(JSONType" in models_src, True, "JobPosting.raw_data (audit/re-parse)")
ck("expires_at = Column(DateTime" in models_src, True, "JobPosting.expires_at (expiration connecteur)")
ck('UniqueConstraint("dedup_hash", "source_connector"' in models_src, True, "UniqueConstraint (dedup_hash, source_connector)")
ck("class SavedSearch(Base):" in models_src, True, "SavedSearch model")
ck('__tablename__ = "saved_searches"' in models_src, True, "table saved_searches")
ck("career_profile_id = Column(String, ForeignKey(\"career_profiles.id\", ondelete=\"CASCADE\")" in models_src, True, "SavedSearch FK career_profile (CASCADE)")
ck("criteria = Column(JSONType" in models_src, True, "SavedSearch.criteria (JSON multicritère)")
ck("alert_enabled = Column(Boolean" in models_src, True, "SavedSearch.alert_enabled")
ck("alert_frequency = Column(String" in models_src, True, "SavedSearch.alert_frequency (daily|weekly)")
ck("last_run_postings_count = Column(Integer" in models_src, True, "SavedSearch.last_run_postings_count")

# ─── Source : migration 0020 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0020_p35_job_search.py").read_text(encoding="utf-8")
print("\n=== migration 0020_p35_job_search ===")
ck('revision = "0020_p35_job_search"' in mig, True, "revision 0020_p35_job_search")
ck('down_revision = "0019_p34_career_cv"' in mig, True, "down_revision 0019 (chaîne)")
ck('"job_postings"' in mig and '"saved_searches"' in mig, True, "2 nouvelles tables")
ck("uq_job_posting_dedup_source" in mig, True, "UniqueConstraint dedup_hash+source_connector")
ck("ix_job_postings_is_expired" in mig, True, "index is_expired (filtre offres actives)")

# ─── Source : job_connectors/base.py (framework) ─────────────────────────────
base_src = (ROOT / "backend" / "app" / "services" / "job_connectors" / "base.py").read_text(encoding="utf-8")
print("\n=== job_connectors/base.py : framework ===")
ck("class SearchCriteria" in base_src, True, "SearchCriteria (keywords/location/contract_types/salary)")
ck("class ConnectorResult" in base_src, True, "ConnectorResult (status ok|not_configured|error|unauthorized)")
ck("class JobPostingData" in base_src, True, "JobPostingData (transfert connecteur→ORM)")
ck("class JobConnector(ABC):" in base_src, True, "JobConnector ABC (interface)")
ck("status" in base_src and "not_configured" in base_src, True, "status not_configured (anti-faux-succès)")
ck("@abstractmethod" in base_src, True, "search() abstract (v2 implémente concrète)")

# ─── Source : job_connectors/registry.py (12 providers) ──────────────────────
reg_src = (ROOT / "backend" / "app" / "services" / "job_connectors" / "registry.py").read_text(encoding="utf-8")
print("\n=== job_connectors/registry.py : 12 providers ===")
ck("CONNECTOR_REGISTRY" in reg_src, True, "CONNECTOR_REGISTRY dict")
ck("def get_connector(" in reg_src, True, "get_connector(name)")
ck("def list_connectors(" in reg_src, True, "list_connectors() (info + status)")
expected = ["linkedin_jobs", "freework", "malt", "lehibou", "france_travail", "apec", "indeed",
            "hays", "wttj", "upwork", "career_sites", "africa"]
for name in expected:
    ck(f'"{name}"' in reg_src, True, f"connecteur {name} enregistré")
ck(reg_src.count("class "), 13, f"13 classes (1 base _StubConnector + 12 providers) — got {reg_src.count('class ')}")
# P35-V2 : france_travail = connecteur RÉEL oauth2 (API Offres d'emploi v2, délègue à france_travail.py).
# career_sites = RSS public (credential_env="" → configured=True, stub v1).
ck("from .france_travail import" in reg_src, True, "P35-V2 : france_travail connecteur réel importé (france_travail.py)")
ck("class FranceTravailConnector(JobConnector)" in reg_src, True, "P35-V2 : FranceTravailConnector étend JobConnector (plus _StubConnector)")
ck("search_france_travail" in reg_src, True, "P35-V2 : search() délègue à search_france_travail (OAuth + API v2)")
ck('credential_env = ""  # RSS public' in reg_src, True, "career_sites RSS public (configured=True)")

# ─── Source : job_search.py (orchestrateur) ──────────────────────────────────
svc_src = (ROOT / "backend" / "app" / "services" / "job_search.py").read_text(encoding="utf-8")
print("\n=== job_search.py : orchestrateur ===")
ck("def search_jobs(" in svc_src, True, "search_jobs (orchestrateur multicritère)")
ck("def dedup_hash(" in svc_src, True, "dedup_hash (sha256 title+company+location)")
ck("def detect_expired(" in svc_src, True, "detect_expired (heuristic 30 jours)")
ck("def run_saved_search(" in svc_src, True, "run_saved_search (execute + update last_run)")
ck("EXPIRY_HEURISTIC_DAYS = 30" in svc_src, True, "EXPIRY_HEURISTIC_DAYS = 30")
ck("import hashlib" in svc_src, True, "hashlib (sha256 dedup)")
ck("status=\"not_configured\"" not in svc_src, True, "orchestrateur ne génère pas not_configured (vient du connecteur)")
ck("is_expired = True" in svc_src, True, "marquage is_expired=True sur offres expirées")

# ─── Source : routers/career.py (7 nouveaux endpoints P35) ───────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "career.py").read_text(encoding="utf-8")
print("\n=== routers/career.py : 7 endpoints P35 ===")
ck('@router.get("/jobs/connectors"' in rtr_src, True, "GET /jobs/connectors (list 12 providers + status)")
ck('@router.post("/jobs/search"' in rtr_src, True, "POST /jobs/search (multicritère)")
ck('@router.get("/jobs/postings"' in rtr_src, True, "GET /jobs/postings (cache actif)")
ck('@router.get("/saved-searches"' in rtr_src, True, "GET /saved-searches (list)")
ck('@router.post("/saved-searches"' in rtr_src, True, "POST /saved-searches (create)")
ck('@router.post("/saved-searches/{search_id}/run"' in rtr_src, True, "POST /saved-searches/{id}/run (execute)")
ck('@router.delete("/saved-searches/{search_id}"' in rtr_src, True, "DELETE /saved-searches/{id}")
ck(("from ..services import" in rtr_src and "job_search" in rtr_src), True, "import job_search")
ck("from ..services.job_connectors import SearchCriteria, list_connectors" in rtr_src, True, "import job_connectors")

# ─── Source : schemas.py (P35 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P35 schemas ===")
for s in ["SearchCriteriaRequest", "JobPostingOut", "ConnectorStatusOut", "SearchResponse",
          "ConnectorInfoOut", "SavedSearchRequest", "SavedSearchOut"]:
    ck(f"class {s}" in sch_src, True, f"schema {s}")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : connectors + dedup + search + saved ===")
try:
    import sqlalchemy  # noqa: F401
    import bcrypt  # noqa: F401
    import slowapi  # noqa: F401
    _RUNTIME_OK = True
except ImportError:
    _RUNTIME_OK = False
    print("  ⚠ Introspection ignorée (deps backend non installées — validée via E2E).")

if _RUNTIME_OK:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))

    from app import database as db_mod
    from app import models as M
    from app.services import job_search
    from app.services.job_connectors import CONNECTOR_REGISTRY, SearchCriteria, get_connector, list_connectors

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p35", email="p35@test.com", name="Cand", password_hash="h"))
    s.add(M.CareerProfile(id="crp_p35", user_id="usr_p35", mobility=True, currency="EUR"))
    s.commit()

    # ── 12 connectors registered ──
    print("--- 12 connectors registered (11 not_configured + 1 RSS configured) ---")
    ck(len(CONNECTOR_REGISTRY), 12, f"CONNECTOR_REGISTRY : {len(CONNECTOR_REGISTRY)} connecteurs (12)")
    info = list_connectors()
    ck(len(info), 12, f"list_connectors : {len(info)} infos")
    # P35-V2 : career_sites = RSS public (configured=True). france_travail = oauth2 réel (not_configured
    # sans credentials REVPULSE_FRANCE_TRAVAIL_CLIENT_ID/SECRET). 10 autres API = not_configured.
    rss_configured = [c for c in info if c["configured"]]
    ck(len(rss_configured), 1, f"{len(rss_configured)} connecteur RSS configuré (career_sites seulement — france_travail v2 oauth2)")
    ck(rss_configured[0]["name"], "career_sites", f"RSS configuré : {rss_configured[0]['name']}")
    # france_travail v2 : auth_method oauth2 (plus rss), configured=False sans credentials.
    ft = next(c for c in info if c["name"] == "france_travail")
    ck(ft["auth_method"], "oauth2", f"france_travail auth_method=oauth2 (v2 réel, got {ft['auth_method']})")
    ck(ft["configured"], False, "france_travail configured=False (v2 : credentials absents en test)")
    # API connectors (11) = not_configured (10 API stubs + france_travail v2 oauth2 sans credentials).
    api_not_configured = [c for c in info if not c["configured"]]
    ck(len(api_not_configured), 11, f"{len(api_not_configured)} connecteurs non configurés (10 API stubs + france_travail v2)")

    # ── dedup_hash déterministe + normalisation ──
    print("--- dedup_hash : déterministe + normalisation (strip/lower/collapse) ---")
    h1 = job_search.dedup_hash("Product Owner", "TechCorp", "Paris")
    h2 = job_search.dedup_hash("  product owner  ", "  techcorp  ", "  paris  ")
    ck(h1, h2, "dedup_hash normalise strip+lower+collapse (variantes → même hash)")
    ck(len(h1), 64, f"dedup_hash = sha256 64 chars : {len(h1)}")
    h3 = job_search.dedup_hash("Data Scientist", "TechCorp", "Paris")
    ck(h1 != h3, True, "dedup_hash différent pour titre différent")

    # ── search_jobs : connecteurs not_configured → 0 postings (JAMAIS faux succès) ──
    print("--- search_jobs : connecteurs non configurés → 0 postings (anti-faux-succès) ---")
    crit = SearchCriteria(keywords="product owner", location="Paris", contract_types=["CDI"])
    response = job_search.search_jobs(s, crit)
    ck(response["total"], 0, f"total postings = {response['total']} (0 — aucun connecteur configuré ne retourne de postings)")
    ck(len(response["postings"]), 0, "0 postings dans la réponse (anti-faux-succès)")
    # Tous les connecteurs interrogés (12) → status visible.
    statuses = {c["name"]: c["status"] for c in response["connector_statuses"]}
    ck(len(response["connector_statuses"]), 12, f"12 connecteurs interrogés (status transparent)")
    # France Travail + career_sites (RSS configured) → status "ok" (mais 0 postings v1 stub).
    ck(statuses.get("france_travail"), "ok", f"france_travail (RSS) status=ok : {statuses.get('france_travail')}")
    ck(statuses.get("career_sites"), "ok", f"career_sites (RSS) status=ok : {statuses.get('career_sites')}")
    # LinkedIn Jobs (API not_configured) → status "not_configured".
    ck(statuses.get("linkedin_jobs"), "not_configured", f"linkedin_jobs status=not_configured : {statuses.get('linkedin_jobs')}")
    ck(statuses.get("malt"), "not_configured", f"malt status=not_configured : {statuses.get('malt')}")
    # Aucun connecteur ne retourne de postings (v1 stub) → 0 dans la DB.
    db_count = s.query(M.JobPosting).count()
    ck(db_count, 0, f"0 postings persistés en DB (v1 stubs ne génèrent pas de postings)")

    # ── SavedSearch : create + run ──
    print("--- SavedSearch : create + run (isolation career_profile_id) ---")
    saved = M.SavedSearch(
        career_profile_id="crp_p35", name="PO Paris CDI",
        criteria={"keywords": "product owner", "location": "Paris", "contract_types": ["CDI"]},
        alert_enabled=True, alert_frequency="daily")
    s.add(saved)
    s.commit()
    s.refresh(saved)
    ck(saved.alert_enabled, True, "saved.alert_enabled=True")
    ck(saved.alert_frequency, "daily", "saved.alert_frequency=daily")
    ck(saved.last_run_postings_count, 0, f"saved.last_run_postings_count={saved.last_run_postings_count} (init 0)")
    # Run → execute search + update last_run_at + count.
    run_resp = job_search.run_saved_search(s, saved)
    s.refresh(saved)
    ck(saved.last_run_at is not None, True, "saved.last_run_at updated après run")
    ck(saved.last_run_postings_count, 0, f"saved.last_run_postings_count=0 après run (aucun posting v1)")
    ck(run_resp["total"], 0, f"run_saved_search total=0 (v1 stubs)")

    # ── detect_expired : heuristic 30 jours ──
    print("--- detect_expired : heuristic (posted_at > 30 jours → is_expired) ---")
    from datetime import datetime, timedelta
    old_posting = M.JobPosting(
        dedup_hash=job_search.dedup_hash("Old Job", "OldCorp", "Lyon"),
        source_connector="france_travail", title="Old Job", company="OldCorp", location="Lyon",
        posted_at=datetime.utcnow() - timedelta(days=45),  # > 30 jours → expired
        fetched_at=datetime.utcnow(), is_expired=False)
    s.add(old_posting)
    s.commit()
    marked = job_search.detect_expired(s)
    ck(marked, 1, f"detect_expired marque {marked} offre (posted_at > 30 jours)")
    s.refresh(old_posting)
    ck(old_posting.is_expired, True, "old_posting.is_expired=True (heuristic 30 jours)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
