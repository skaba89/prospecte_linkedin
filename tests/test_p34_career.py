#!/usr/bin/env python3
"""test_p34_career.py — validation statique + runtime de P34 Career : référentiel métiers + CV.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : CareerProfile (profil candidat isolé du Business), CV (import + parse), JobReference (ROME/ESCO/O*NET).
  - migration 0019 : 3 nouvelles tables (career_profiles, cvs, job_references) — isolation Career (user_id, pas tenant_id).
  - services/career_cv.py : import_cv_text/pdf/docx, parse_cv (extraction heuristique), set_active_cv, detect_transferrable_skills.
  - services/career_referential.py : seed_referential (MVP subset), search_jobs.
  - routers/career.py : 10 endpoints (prefix /api/career — user-scoped via get_current_user, isolation stricte).
  - schemas.py : CareerProfileRequest/Out, CVTextImport, CVOut, JobReferenceOut, TransferrableSkillsOut.
  - main.py : router career inclus.
  - Runtime : create profile + import CV text + parse (skills/experiences) + set_active + transferrable +
    referential seed (28 métiers MVP) + search_jobs (filter category).
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


print("=== test_p34_career.py — P34 Career : référentiel métiers + CV ===\n")

# ─── Source : models.py (3 nouveaux modèles) ─────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : CareerProfile + CV + JobReference ===")
ck("class CareerProfile(Base):" in models_src, True, "CareerProfile model")
ck('__tablename__ = "career_profiles"' in models_src, True, "table career_profiles")
ck("user_id = Column(String, ForeignKey(\"users.id\", ondelete=\"CASCADE\"), unique=True" in models_src, True, "CareerProfile.user_id unique (isolation Career — pas tenant_id)")
ck("target_job_title = Column(String" in models_src, True, "CareerProfile.target_job_title")
ck("contract_types = Column(JSONType" in models_src, True, "CareerProfile.contract_types (JSON)")
ck("salary_min = Column(Integer" in models_src, True, "CareerProfile.salary_min")
ck("tjm_min = Column(Integer" in models_src, True, "CareerProfile.tjm_min (freelance)")
ck("class CV(Base):" in models_src, True, "CV model")
ck('__tablename__ = "cvs"' in models_src, True, "table cvs")
ck("source_type = Column(String" in models_src, True, "CV.source_type (text|pdf|docx)")
ck("raw_text = Column(Text" in models_src, True, "CV.raw_text (texte extrait, jamais le binaire)")
ck("version = Column(Integer" in models_src, True, "CV.version (versionning)")
ck("is_active = Column(Boolean" in models_src, True, "CV.is_active (1 seul actif)")
ck("parsed_data = Column(JSONType" in models_src, True, "CV.parsed_data (JSON structuré)")
ck("class JobReference(Base):" in models_src, True, "JobReference model")
ck('__tablename__ = "job_references"' in models_src, True, "table job_references")
ck('UniqueConstraint("code", "source"' in models_src, True, "JobReference UniqueConstraint (code, source)")

# ─── Source : migration 0019 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0019_p34_career_cv.py").read_text(encoding="utf-8")
print("\n=== migration 0019_p34_career_cv ===")
ck('revision = "0019_p34_career_cv"' in mig, True, "revision 0019_p34_career_cv")
ck('down_revision = "0018_p28_warmup_sales_intel"' in mig, True, "down_revision 0018 (chaîne)")
ck('"career_profiles"' in mig and '"cvs"' in mig and '"job_references"' in mig, True, "3 nouvelles tables")
ck("ondelete=\"CASCADE\"" in mig, True, "CASCADE (user→profile→CV)")
ck("uq_career_profile_user" in mig, True, "UniqueConstraint user_id (1 profile par user)")
ck("uq_job_reference_code_source" in mig, True, "UniqueConstraint (code, source) sur job_references")

# ─── Source : services/career_cv.py (6 fonctions) ────────────────────────────
svc_src = (ROOT / "backend" / "app" / "services" / "career_cv.py").read_text(encoding="utf-8")
print("\n=== services/career_cv.py : 6 fonctions ===")
ck("def import_cv_text(" in svc_src, True, "import_cv_text")
ck("def import_cv_pdf(" in svc_src, True, "import_cv_pdf (pdfminer)")
ck("def import_cv_docx(" in svc_src, True, "import_cv_docx (python-docx)")
ck("def parse_cv(" in svc_src, True, "parse_cv (extraction heuristique)")
ck("def set_active_cv(" in svc_src, True, "set_active_cv (1 seul actif)")
ck("def detect_transferrable_skills(" in svc_src, True, "detect_transferrable_skills")
ck("from pdfminer.high_level import extract_text" in svc_src, True, "pdfminer.six (extraction PDF)")
ck("import docx" in svc_src, True, "python-docx (extraction Word)")
ck('"heuristic": True' in svc_src, True, "parsed_data.heuristic=True (v1 — pas ML/NLP parfait)")
ck("_TECH_KEYWORDS" in svc_src, True, "keywords techniques (Python, SQL, Scrum...)")
ck("_SOFT_SKILLS" in svc_src, True, "soft skills transférables (leadership, communication...)")

# ─── Source : services/career_referential.py (2 fonctions + MVP seed) ────────
ref_src = (ROOT / "backend" / "app" / "services" / "career_referential.py").read_text(encoding="utf-8")
print("\n=== services/career_referential.py : seed + search ===")
ck("def seed_referential(" in ref_src, True, "seed_referential (idempotent)")
ck("def search_jobs(" in ref_src, True, "search_jobs (filter query/source/category)")
ck("_MVP_REFERENTIAL" in ref_src, True, "subset MVP (28 métiers prioritaires)")
ck("M1805" in ref_src, True, "ROME M1805 (Product Owner)")
ck("M1801" in ref_src, True, "ROME M1801 (Data Scientist)")
ck("M1807" in ref_src, True, "ROME M1807 (Ingénieur cybersécurité)")

# ─── Source : routers/career.py (10 endpoints + isolation) ───────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "career.py").read_text(encoding="utf-8")
print("\n=== routers/career.py : 10 endpoints + isolation ===")
ck('prefix="/api/career"' in rtr_src, True, "router prefix /api/career (user-scoped, pas tenant)")
ck('tags=["career"]' in rtr_src, True, "router tag career")
ck('@router.get("/profile"' in rtr_src, True, "GET /profile")
ck('@router.put("/profile"' in rtr_src, True, "PUT /profile (upsert)")
ck('@router.post("/cv/text"' in rtr_src, True, "POST /cv/text")
ck('@router.post("/cv/pdf"' in rtr_src, True, "POST /cv/pdf (UploadFile)")
ck('@router.post("/cv/docx"' in rtr_src, True, "POST /cv/docx (UploadFile)")
ck('@router.get("/cv"' in rtr_src, True, "GET /cv (list)")
ck('@router.get("/cv/{cv_id}"' in rtr_src, True, "GET /cv/{cv_id}")
ck('@router.post("/cv/{cv_id}/activate"' in rtr_src, True, "POST /cv/{cv_id}/activate")
ck('@router.get("/cv/{cv_id}/transferrable"' in rtr_src, True, "GET /cv/{cv_id}/transferrable")
ck('@router.get("/referential/jobs"' in rtr_src, True, "GET /referential/jobs")
ck("Depends(get_current_user)" in rtr_src, True, "isolation via get_current_user (pas tenant)")
ck("profile.user_id != user_id" in rtr_src, True, "isolation stricte CV (profile.user_id == user.id)")
ck("HTTP_403_FORBIDDEN" in rtr_src, True, "403 si CV inaccessible (isolation)")

# ─── Source : schemas.py (P34 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P34 schemas ===")
ck("class CareerProfileRequest" in sch_src, True, "CareerProfileRequest")
ck("class CareerProfileOut" in sch_src, True, "CareerProfileOut")
ck("class CVTextImport" in sch_src, True, "CVTextImport")
ck("class CVOut" in sch_src, True, "CVOut")
ck("class JobReferenceOut" in sch_src, True, "JobReferenceOut")
ck("class TransferrableSkillsOut" in sch_src, True, "TransferrableSkillsOut")

# ─── Source : main.py (router career inclus) ─────────────────────────────────
main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : router career inclus ===")
ck("    career," in main_src, True, "main.py import career")
ck("career.router" in main_src, True, "main.py include career.router")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : profile + CV + parse + transferrable + referential ===")
try:
    import sqlalchemy  # noqa: F401
    import bcrypt  # noqa: F401
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
    from app.services import career_cv, career_referential

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_career", email="career@test.com", name="Cand", password_hash="h"))
    s.commit()

    # ── CareerProfile : upsert (create) ──
    print("--- CareerProfile : create (isolation user_id, pas tenant) ---")
    profile = M.CareerProfile(
        user_id="usr_career", target_job_title="Product Owner",
        preferred_location="Paris, France", mobility=True,
        contract_types=["CDI", "freelance"], salary_min=60000, salary_max=80000,
        tjm_min=500, currency="EUR", languages=["fr", "en"])
    s.add(profile)
    s.commit()
    s.refresh(profile)
    ck(profile.user_id, "usr_career", "profile.user_id (isolation — pas tenant_id)")
    ck(profile.target_job_title, "Product Owner", "profile.target_job_title")
    ck(profile.contract_types, ["CDI", "freelance"], f"profile.contract_types={profile.contract_types}")
    ck(profile.tjm_min, 500, "profile.tjm_min (freelance)")

    # ── CV : import text + parse heuristique ──
    print("--- CV : import text + parse heuristique (skills/experiences) ---")
    cv_text = """
Sophie Martin — Product Owner
Paris, France — sophie.martin@email.com

Expérience
- Product Owner chez TechCorp (2020-2026) : gestion backlog, priorisation, agile, scrum
- Business Analyst chez DataInc (2017-2020) : analyse besoins, SQL, Excel, tableau

Compétences
- Python, SQL, Agile, Scrum, Jira, Tableau, Excel
- Leadership, communication, négociation, gestion de projet

Formation
- Master MIAGE — Université Paris Dauphine (2017)

Certifications
- PSM I (Scrum.org)
- SAFe Agilist
"""
    cv = career_cv.import_cv_text(s, profile, "CV PO 2026", cv_text, "fr")
    ck(cv.source_type, "text", "cv.source_type=text")
    ck(cv.is_active, True, "cv.is_active=True (auto-activé)")
    ck(cv.version, 1, f"cv.version={cv.version} (1er CV)")
    parsed = cv.parsed_data
    ck(parsed.get("heuristic"), True, "parsed_data.heuristic=True (v1 keyword-based)")
    # Skills détectés : Python, SQL, Agile, Scrum, Jira, Tableau, Excel (match _TECH_KEYWORDS).
    skills = parsed.get("skills", [])
    ck("python" in skills, True, f"parse_cv détecte 'python' dans skills : {skills}")
    ck("sql" in skills, True, "parse_cv détecte 'sql'")
    ck("scrum" in skills, True, "parse_cv détecte 'scrum'")
    ck("tableau" in skills, True, "parse_cv détecte 'tableau'")
    # Experiences : au moins 2 items (TechCorp + DataInc).
    ck(len(parsed.get("experiences", [])) >= 2, True, f"parse_cv extrait >=2 experiences : {len(parsed.get('experiences',[]))}")
    # Certifications : au moins 1 (PSM I / SAFe).
    ck(len(parsed.get("certifications", [])) >= 1, True, f"parse_cv extrait >=1 certification : {len(parsed.get('certifications',[]))}")

    # ── CV : 2e import (versionning + set_active) ──
    print("--- CV : 2e import (versionning 2 + set_active désactive le 1er) ---")
    cv2 = career_cv.import_cv_text(s, profile, "CV PO 2026 v2", "Version 2 du CV avec Python et SQL.", "fr")
    ck(cv2.version, 2, f"cv2.version={cv2.version} (2e CV)")
    ck(cv2.is_active, True, "cv2.is_active=True (auto-activé)")
    s.refresh(cv)
    ck(cv.is_active, False, "cv1.is_active=False (désactivé par set_active_cv)")

    # ── Transferrable skills : soft skills cross-domain ──
    print("--- detect_transferrable_skills (soft skills cross-domain) ---")
    transf = career_cv.detect_transferrable_skills(parsed)
    ck("leadership" in transf, True, f"transferrable : 'leadership' détecté : {transf}")
    ck("communication" in transf, True, "transferrable : 'communication' détecté")
    ck("gestion de projet" in transf, True, "transferrable : 'gestion de projet' détecté")
    ck("négociation" in transf, True, "transferrable : 'négociation' détecté")

    # ── Référentiel : seed MVP + search ──
    print("--- Référentiel métiers : seed MVP (28) + search by category ---")
    added = career_referential.seed_referential(s)
    ck(added, 27, f"seed_referential ajoute {added} métiers MVP (27)")
    # Idempotent : 2e appel ajoute 0.
    added2 = career_referential.seed_referential(s)
    ck(added2, 0, f"seed_referential idempotent : 2e appel ajoute {added2} (0)")
    # Search by category=data → Data Scientist, Data Engineer, Data Analyst.
    data_jobs = career_referential.search_jobs(s, category="data")
    labels = [j.label for j in data_jobs]
    ck(len(data_jobs) >= 3, True, f"search category=data → {len(data_jobs)} métiers (>=3)")
    ck(any("Data Scientist" in l for l in labels), True, f"search data contient Data Scientist : {labels}")
    # Search by query "product".
    prod_jobs = career_referential.search_jobs(s, query="product")
    ck(len(prod_jobs) >= 1, True, f"search query=product → {len(prod_jobs)} (>=1 : Product Owner)")
    # Search by source=rome.
    rome_jobs = career_referential.search_jobs(s, source="rome")
    ck(len(rome_jobs), 27, f"search source=rome → {len(rome_jobs)} (27 — tous MVP sont ROME)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
