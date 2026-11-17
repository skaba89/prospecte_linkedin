#!/usr/bin/env python3
"""test_p37_cv_adaptatif.py — validation statique + runtime de P37 Career : CV adaptatif.

Couvre :
  - models.py : AdaptedCV (cv_source_id FK CASCADE, career_profile_id FK CASCADE, posting_id FK SET NULL,
    adapted_data JSON, match_score Float).
  - migration 0021 : 1 nouvelle table (adapted_cvs) + 3 FK + 3 indexes.
  - services/cv_adapter.py : adapt_cv (anti-invention), _build_summary (templates + keywords réels),
    _reorder_skills (required en premier), _select_experiences (top 3 alignées), _matched_certifications,
    _assert_no_invention (Invariant P37 — vérifie runtime que rien n'est inventé).
  - services/cv_generator.py : generate_pdf (reportlab), generate_docx (python-docx).
  - routers/career.py : 4 nouveaux endpoints (POST /cv/{id}/adapt, GET /cv-adapted, GET /cv-adapted/{id},
    GET /cv-adapted/{id}/download?format=pdf|docx).
  - schemas.py : AdaptCVRequest, AdaptedCVOut.
  - Runtime : adapt_cv sur CV + posting réels → adapted_data complet. Invariant P37 vérifié (rien inventé —
    tous skills/expériences/certs de adapted_data sont dans le CV source). PDF + DOCX bytes > 0.
  - Anti-invention (P37 invariant) : _assert_no_invention lève ValueError si un item inventé.
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


print("=== test_p37_cv_adaptatif.py — P37 Career : CV adaptatif ===\n")

# ─── Source : models.py (AdaptedCV) ───────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : AdaptedCV ===")
ck("class AdaptedCV(Base):" in models_src, True, "AdaptedCV model")
ck('__tablename__ = "adapted_cvs"' in models_src, True, "table adapted_cvs")
ck('cv_source_id = Column(String, ForeignKey("cvs.id", ondelete="CASCADE")' in models_src, True, "cv_source_id FK CASCADE")
ck('career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE")' in models_src, True, "career_profile_id FK CASCADE (isolation)")
ck('posting_id = Column(String, ForeignKey("job_postings.id", ondelete="SET NULL")' in models_src, True, "posting_id FK SET NULL (nullable)")
ck("adapted_data = Column(JSONType" in models_src, True, "adapted_data JSON (structure adaptée)")
ck("match_score = Column(Float" in models_src, True, "match_score Float (score P36 référence)")
ck("Invariant P37" in models_src, True, "docstring mentionne Invariant P37 (anti-invention)")

# ─── Source : migration 0021 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0021_p37_cv_adaptatif.py").read_text(encoding="utf-8")
print("\n=== migration 0021_p37_cv_adaptatif ===")
ck('revision = "0021_p37_cv_adaptatif"' in mig, True, "revision 0021_p37_cv_adaptatif")
ck('down_revision = "0020_p35_job_search"' in mig, True, "down_revision 0020 (chaîne)")
ck('"adapted_cvs"' in mig, True, "nouvelle table adapted_cvs")
ck("ondelete=\"CASCADE\"" in mig and "ondelete=\"SET NULL\"" in mig, True, "FK CASCADE (cv_source/profile) + SET NULL (posting)")
ck("ix_adapted_cvs_cv_source_id" in mig, True, "index cv_source_id")
ck("ix_adapted_cvs_career_profile_id" in mig, True, "index career_profile_id")

# ─── Source : cv_adapter.py (anti-invention) ─────────────────────────────────
ad_src = (ROOT / "backend" / "app" / "services" / "cv_adapter.py").read_text(encoding="utf-8")
print("\n=== cv_adapter.py : adaptateur anti-invention ===")
ck("def adapt_cv(" in ad_src, True, "adapt_cv(cv, posting, profile)")
ck("def _build_summary(" in ad_src, True, "_build_summary (templates + keywords réels)")
ck("def _reorder_skills(" in ad_src, True, "_reorder_skills (required en premier)")
ck("def _select_experiences(" in ad_src, True, "_select_experiences (top 3 alignées)")
ck("def _matched_certifications(" in ad_src, True, "_matched_certifications")
ck("def _assert_no_invention(" in ad_src, True, "_assert_no_invention (Invariant P37 runtime)")
ck("_SUMMARY_TEMPLATES" in ad_src, True, "_SUMMARY_TEMPLATES (templates fr/en)")
ck("from . import matching" in ad_src, True, "import matching (P36 — extract_required_skills + compute_match)")
ck("Invariant P37" in ad_src, True, "docstring Invariant P37 (anti-invention)")
ck("raise ValueError" in ad_src, True, "raise ValueError si invention détectée")
ck("JAMAIS" in ad_src, True, "docstring JAMAIS d'invention")
# Le summary est assemblé à partir de keywords RÉELS du CV (pas de génération libre).
ck("{top_skills}" in ad_src, True, "summary template utilise top_skills (issus du CV)")

# ─── Source : cv_generator.py (PDF + Word) ───────────────────────────────────
gen_src = (ROOT / "backend" / "app" / "services" / "cv_generator.py").read_text(encoding="utf-8")
print("\n=== cv_generator.py : PDF reportlab + Word python-docx ===")
ck("def generate_pdf(" in gen_src, True, "generate_pdf (reportlab)")
ck("def generate_docx(" in gen_src, True, "generate_docx (python-docx)")
ck("from reportlab" in gen_src, True, "import reportlab")
ck("from docx" in gen_src, True, "import python-docx")
ck("SimpleDocTemplate" in gen_src, True, "SimpleDocTemplate (layout PDF)")
ck("ListFlowable" not in gen_src, True, "imports unused nettoyés (ListFlowable retiré)")
ck("_SECTION_LABELS" in gen_src, True, "_SECTION_LABELS (labels localisés fr/en)")
ck("Content-Disposition" not in gen_src, True, "Content-Disposition géré par le router (pas le generator)")

# ─── Source : routers/career.py (4 endpoints P37) ────────────────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "career.py").read_text(encoding="utf-8")
print("\n=== routers/career.py : 4 endpoints P37 ===")
ck('@router.post("/cv/{cv_id}/adapt"' in rtr_src, True, "POST /cv/{cv_id}/adapt (génère CV adapté)")
ck('@router.get("/cv-adapted"' in rtr_src, True, "GET /cv-adapted (list)")
ck('@router.get("/cv-adapted/{adapted_id}"' in rtr_src, True, "GET /cv-adapted/{id} (detail)")
ck('@router.get("/cv-adapted/{adapted_id}/download"' in rtr_src, True, "GET /cv-adapted/{id}/download?format=pdf|docx")
ck("StreamingResponse" in rtr_src, True, "StreamingResponse (download file)")
ck("cv_adapter.adapt_cv(cv, posting, profile)" in rtr_src, True, "appel adapt_cv dans endpoint")
ck("cv_generator.generate_pdf" in rtr_src, True, "appel generate_pdf dans download")
ck("cv_generator.generate_docx" in rtr_src, True, "appel generate_docx dans download")
ck("from ..services import" in rtr_src and "cv_adapter" in rtr_src and "cv_generator" in rtr_src, True, "imports cv_adapter + cv_generator")

# ─── Source : schemas.py (P37 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P37 schemas ===")
for s in ["AdaptCVRequest", "AdaptedCVOut"]:
    ck(f"class {s}" in sch_src, True, f"schema {s}")
ck("adapted_data: dict" in sch_src, True, "AdaptedCVOut.adapted_data (structure adaptée)")
ck("match_score: float" in sch_src, True, "AdaptedCVOut.match_score (référence P36)")

# ─── Source : requirements.txt (reportlab) ───────────────────────────────────
req = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
print("\n=== requirements.txt : reportlab ajouté ===")
ck("reportlab==4.4.9" in req, True, "reportlab==4.4.9 (génération PDF P37)")
ck("python-docx==1.2.0" in req, True, "python-docx==1.2.0 (extraction P34 + génération Word P37)")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : adapt_cv + anti-invention + PDF/DOCX ===")
try:
    import sqlalchemy  # noqa: F401
    import bcrypt  # noqa: F401
    import slowapi  # noqa: F401
    import reportlab  # noqa: F401
    import docx  # noqa: F401
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
    from app.services import cv_adapter, cv_generator

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p37", email="p37@test.com", name="Cand", password_hash="h"))
    s.add(M.CareerProfile(id="crp_p37", user_id="usr_p37", target_job_title="Product Owner",
                          preferred_location="Paris", mobility=True, salary_min=50000, salary_max=70000,
                          languages=["fr", "en"]))
    s.commit()

    # CV source avec skills + experience + certifications.
    cv = M.CV(id="cv_p37", career_profile_id="crp_p37", title="CV PO 2026", source_type="text",
              raw_text="Product Owner, 8 ans. Python, SQL, Scrum, Jira, Agile. Scrum Master certifié.",
              parsed_data={"skills": ["python", "sql", "scrum", "jira", "agile"],
                            "experience": ["Product Owner chez TechCorp (2018-2026, 8 ans)",
                                           "Business Analyst chez DataCorp (2015-2018)",
                                           "Stage analyste chez StartUp (2014)"],
                            "certifications": ["Scrum Master", "PMP"], "education": [], "heuristic": True},
              is_active=True, locale="fr")
    s.add(cv)

    # Posting cible : PO Senior requiert python+scrum+agile (présents) + aws (manquant).
    posting = M.JobPosting(id="jbp_p37", dedup_hash="h1", source_connector="linkedin_jobs",
                           title="Senior Product Owner", company="TechCorp", location="Paris",
                           description="Seeking Senior Product Owner. Required : python, sql, scrum, "
                                       "agile, aws. Scrum Master certification a plus.",
                           contract_type="CDI", salary_min=55000, salary_max=75000, currency="EUR",
                           is_expired=False, fetched_at=__import__("datetime").datetime.utcnow())
    s.add(posting)
    s.commit()
    profile = s.query(M.CareerProfile).filter_by(id="crp_p37").first()

    # ── adapt_cv : adapted_data complet ──
    print("--- adapt_cv : adapted_data complet (targeted_title + summary + reordered + selected + certs + missing) ---")
    adapted = cv_adapter.adapt_cv(cv, posting, profile)
    ck(adapted.get("targeted_title"), "Senior Product Owner", f"targeted_title = posting.title : {adapted.get('targeted_title')}")
    ck(bool(adapted.get("summary")), True, "summary non vide (assemblé depuis keywords CV)")
    ck("8 ans" in adapted.get("summary", ""), True, f"summary mentionne '8 ans' (years du CV) : {adapted.get('summary')[:60]}...")
    ck("python" in adapted.get("summary", ""), True, "summary mentionne python (skill du CV)")
    ck(len(adapted.get("reordered_skills", [])), 5, f"reordered_skills : {len(adapted.get('reordered_skills', []))} (5 skills du CV)")
    # Reorder : required (python, sql, scrum, agile) en premier, puis jira.
    reordered = adapted.get("reordered_skills", [])
    ck(reordered[0] in ["python", "sql", "scrum", "agile"], True, f"premier skill = required : {reordered[0]}")
    ck("jira" in reordered, True, "jira présent (skill non-required mais du CV)")
    ck(len(adapted.get("selected_experiences", [])) <= 3, True, f"selected_experiences ≤ 3 : {len(adapted.get('selected_experiences', []))}")
    # L'expérience TechCorp (mentionne PO) doit être sélectionnée (alignée avec titre PO).
    selected = adapted.get("selected_experiences", [])
    ck(any("TechCorp" in e for e in selected), True, f"expérience TechCorp (alignée PO) sélectionnée : {selected}")
    ck("Scrum Master" in adapted.get("matched_certifications", []), True, "Scrum Master cert (pertinente) dans matched")
    ck("aws" in adapted.get("missing_skills", []), True, f"aws dans missing_skills (requis, absent CV) : {adapted.get('missing_skills')}")
    ck(adapted.get("match_score") is not None, True, f"match_score présent (P36 référence) : {adapted.get('match_score')}")

    # ── Invariant P37 : rien inventé ──
    print("--- Invariant P37 : tous skills/expériences/certs de adapted_data sont dans le CV source ---")
    cv_skills_set = {x.lower() for x in cv.parsed_data["skills"]}
    cv_exp_set = {x.lower() for x in cv.parsed_data["experience"]}
    cv_certs_set = {x.lower() for x in cv.parsed_data["certifications"]}
    for sk in adapted.get("reordered_skills", []):
        ck(sk.lower() in cv_skills_set, True, f"skill '{sk}' dans CV source (pas inventé)")
    for exp in adapted.get("selected_experiences", []):
        ck(exp.lower() in cv_exp_set, True, f"expérience dans CV source (pas inventée)")
    for cert in adapted.get("matched_certifications", []):
        ck(cert.lower() in cv_certs_set, True, f"cert '{cert}' dans CV source (pas inventée)")

    # ── _assert_no_invention : lève ValueError si invention ──
    print("--- _assert_no_invention : lève ValueError si item inventé ---")
    fake_adapted = {"reordered_skills": ["python", "INVENTED_SKILL_XYZ"],
                    "selected_experiences": [], "matched_certifications": []}
    try:
        cv_adapter._assert_no_invention(fake_adapted, cv.parsed_data)
        ck(False, True, "_assert_no_invention devrait lever ValueError pour skill inventé")
    except ValueError as e:
        ck("INVENTED_SKILL_XYZ" in str(e), True, f"ValueError levée pour skill inventé : {str(e)[:50]}")

    # ── adapt_cv sans posting (critères libres) ──
    print("--- adapt_cv sans posting (critères libres : target_job_title) ---")
    adapted_free = cv_adapter.adapt_cv(cv, None, profile)
    ck(adapted_free.get("match_score"), None, f"match_score=None sans posting : {adapted_free.get('match_score')}")
    ck(len(adapted_free.get("reordered_skills", [])), 5, f"reordered_skills toujours 5 (ordre original) : {len(adapted_free.get('reordered_skills', []))}")
    ck(adapted_free.get("missing_skills"), [], f"missing_skills=[] sans posting : {adapted_free.get('missing_skills')}")

    # ── generate_pdf : bytes > 0 ──
    print("--- generate_pdf : bytes PDF > 0 (reportlab) ---")
    pdf_bytes = cv_generator.generate_pdf(adapted, profile, cv)
    ck(len(pdf_bytes) > 100, True, f"PDF généré : {len(pdf_bytes)} bytes (> 100)")
    ck(pdf_bytes[:4], b"%PDF", f"PDF magic bytes : {pdf_bytes[:4]}")

    # ── generate_docx : bytes > 0 ──
    print("--- generate_docx : bytes Word > 0 (python-docx) ---")
    docx_bytes = cv_generator.generate_docx(adapted, profile, cv)
    ck(len(docx_bytes) > 100, True, f"DOCX généré : {len(docx_bytes)} bytes (> 100)")
    ck(docx_bytes[:2], b"PK", f"DOCX magic bytes (ZIP) : {docx_bytes[:2]}")

    # ── Persistence AdaptedCV ──
    print("--- Persistence AdaptedCV (isolation career_profile_id) ---")
    acv = M.AdaptedCV(cv_source_id=cv.id, career_profile_id=profile.id, posting_id=posting.id,
                      target_job_title="Senior Product Owner", target_company="TechCorp",
                      adapted_data=adapted, match_score=adapted.get("match_score"))
    s.add(acv)
    s.commit()
    s.refresh(acv)
    ck(acv.cv_source_id, "cv_p37", "acv.cv_source_id")
    ck(acv.career_profile_id, "crp_p37", "acv.career_profile_id (isolation)")
    ck(acv.posting_id, "jbp_p37", "acv.posting_id")
    ck(acv.match_score is not None, True, f"acv.match_score persisté : {acv.match_score}")
    ck(acv.adapted_data.get("targeted_title"), "Senior Product Owner", "acv.adapted_data.targeted_title")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
