#!/usr/bin/env python3
"""test_p36_matching.py — validation statique + runtime de P36 Career : matching intelligent CV↔offre.

Couvre :
  - services/matching.py : compute_match (score hybride 0-100), WEIGHTS (somme=1.0), 6 critères pondérés
    (skills 40%, experience 20%, certifications/location/salary/languages 10% chacun), extract_required_skills,
    estimate_experience_years (heuristic "X ans" + comptage), estimate_seniority_years (Senior/Lead/Junior),
    _score_skills/experience/certifications/location/salary/languages, DISCLAIMER (anti-probabilité-recrutement).
  - routers/career.py : 2 nouveaux endpoints (POST /matching/{cv_id}/{posting_id}, POST /matching/search).
  - schemas.py : MatchingCriterionOut, MatchingBreakdownOut, MatchingRankedItemOut, MatchingSearchRequest.
  - Runtime : compute_match sur CV + posting réels → total_score 0-100, breakdown 6 critères, missing_skills,
    eliminatory (cap 60), disclaimer présent (anti-sur-déclaration).
  - Anti-sur-déclaration (P36 invariant) : score = compatibilité, DISCLAIMER explicite "NE PAS présenter
    comme probabilité de recrutement".
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


print("=== test_p36_matching.py — P36 Career : matching intelligent CV↔offre ===\n")

# ─── Source : matching.py ────────────────────────────────────────────────────
m_src = (ROOT / "backend" / "app" / "services" / "matching.py").read_text(encoding="utf-8")
print("=== matching.py : moteur hybride explicable ===")
ck("def compute_match(" in m_src, True, "compute_match(cv, posting, profile)")
ck("WEIGHTS = {" in m_src, True, "WEIGHTS dict (pondération configurable)")
ck('"skills": 0.40' in m_src, True, "skills weight 40% (critère dominant)")
ck('"experience": 0.20' in m_src, True, "experience weight 20%")
ck('"certifications": 0.10' in m_src, True, "certifications weight 10%")
ck('"location": 0.10' in m_src, True, "location weight 10%")
ck('"salary": 0.10' in m_src, True, "salary weight 10%")
ck('"languages": 0.10' in m_src, True, "languages weight 10%")
ck("SALARY_ELIMINATORY_RATIO = 0.70" in m_src, True, "seuil eliminatory salaire 70%")
ck("ELIMINATORY_SCORE_CAP = 60.0" in m_src, True, "cap score 60 si éliminatoire")
ck("def extract_required_skills(" in m_src, True, "extract_required_skills (heuristic keywords)")
ck("def estimate_experience_years(" in m_src, True, "estimate_experience_years (heuristic)")
ck("def estimate_seniority_years(" in m_src, True, "estimate_seniority_years (Senior/Lead/Junior)")
ck("def _score_skills(" in m_src, True, "_score_skills (missing + eliminatory)")
ck("def _score_experience(" in m_src, True, "_score_experience")
ck("def _score_certifications(" in m_src, True, "_score_certifications")
ck("def _score_location(" in m_src, True, "_score_location (remote/mobility/match)")
ck("def _score_salary(" in m_src, True, "_score_salary (overlap + eliminatory)")
ck("def _score_languages(" in m_src, True, "_score_languages")
ck("class CriterionScore" in m_src, True, "CriterionScore dataclass (sub-score + detail + eliminatory)")
ck("class MatchingBreakdown" in m_src, True, "MatchingBreakdown dataclass (total + criteria + missing + flags)")
# Anti-sur-déclaration (P36 invariant).
ck("DISCLAIMER" in m_src, True, "DISCLAIMER constant")
ck("NE PAS présenter comme une probabilité de recrutement" in m_src, True, "disclaimer anti-probabilité-recrutement")
ck("compatibilité" in m_src.lower(), True, "score = compatibilité (pas prédictif)")

# ─── Source : routers/career.py (2 endpoints P36) ────────────────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "career.py").read_text(encoding="utf-8")
print("\n=== routers/career.py : 2 endpoints P36 ===")
ck('@router.post("/matching/{cv_id}/{posting_id}"' in rtr_src, True, "POST /matching/{cv_id}/{posting_id} (single)")
ck('@router.post("/matching/search"' in rtr_src, True, "POST /matching/search (ranked)")
ck(("from ..services import" in rtr_src and "matching" in rtr_src), True, "import matching")
ck("matching.compute_match(cv, posting, profile)" in rtr_src, True, "appel compute_match dans single")
ck("results.sort(key=lambda r: r.total_score, reverse=True)" in rtr_src, True, "search ranked par score décroissant")
ck("is_active=True" in rtr_src, True, "search : CV actif si cv_id omis")

# ─── Source : schemas.py (P36 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P36 schemas ===")
for s in ["MatchingCriterionOut", "MatchingBreakdownOut", "MatchingRankedItemOut", "MatchingSearchRequest"]:
    ck(f"class {s}" in sch_src, True, f"schema {s}")
ck("disclaimer: str" in sch_src, True, "MatchingBreakdownOut.disclaimer (anti-sur-déclaration)")
ck("eliminatory: bool" in sch_src, True, "MatchingCriterionOut.eliminatory")
ck("missing_skills: list[str]" in sch_src, True, "missing_skills (compétences absentes)")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : compute_match + 6 critères + eliminatory + disclaimer ===")
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
    from app.services import matching

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p36", email="p36@test.com", name="Cand", password_hash="h"))
    s.add(M.CareerProfile(id="crp_p36", user_id="usr_p36", target_job_title="Product Owner",
                          preferred_location="Paris", mobility=True, salary_min=50000, salary_max=70000,
                          languages=["fr", "en"]))
    s.commit()

    # CV avec skills + experience + certifications.
    cv = M.CV(id="cv_p36", career_profile_id="crp_p36", title="CV PO 2026", source_type="text",
              raw_text="Product Owner, 8 ans d'expérience. Python, SQL, Scrum, Jira, Agile. "
                       "Certification Scrum Master. Anglais courant.",
              parsed_data={"skills": ["python", "sql", "scrum", "jira", "agile"],
                            "experience": ["Product Owner chez TechCorp (2018-2026, 8 ans)"],
                            "certifications": ["Scrum Master"], "education": [], "heuristic": True},
              is_active=True)
    s.add(cv)

    # Posting : PO Senior à Paris, salaire compatible, requiert python+scrum+agile (présents) + aws (manquant).
    posting = M.JobPosting(id="jbp_p36", dedup_hash="h1", source_connector="linkedin_jobs",
                           title="Senior Product Owner", company="TechCorp", location="Paris",
                           description="Seeking Senior Product Owner. Required : python, sql, scrum, "
                                       "agile, aws. English fluent. Certification Scrum Master obligatoire.",
                           contract_type="CDI", salary_min=55000, salary_max=75000, currency="EUR",
                           is_expired=False, fetched_at=__import__("datetime").datetime.utcnow())
    s.add(posting)
    s.commit()

    # ── compute_match : score 0-100 + 6 critères ──
    print("--- compute_match : total 0-100 + 6 critères pondérés ---")
    profile = s.query(M.CareerProfile).filter_by(id="crp_p36").first()
    bd = matching.compute_match(cv, posting, profile)
    ck(0 <= bd.total_score <= 100, True, f"total_score dans [0,100] : {bd.total_score}")
    ck(len(bd.criteria), 6, f"{len(bd.criteria)} critères (skills/experience/certifications/location/salary/languages)")
    ck(bd.cv_id, "cv_p36", "bd.cv_id")
    ck(bd.posting_id, "jbp_p36", "bd.posting_id")
    # Poids somme = 1.0.
    total_weight = sum(c.weight for c in bd.criteria)
    ck(round(total_weight, 2), 1.0, f"somme poids critères = {total_weight} (1.0)")
    # Chaque critère a un score 0-100 + détail non vide.
    for c in bd.criteria:
        ck(0 <= c.score <= 100, True, f"critère {c.name} score {c.score} dans [0,100]")
        ck(bool(c.detail), True, f"critère {c.name} détail non vide")
    # Skills : python+sql+scrum+agile présents (4/5, aws manquant) → score 80%.
    skills_c = next(c for c in bd.criteria if c.name == "skills")
    ck(round(skills_c.score), 80, f"skills score {skills_c.score} (4/5 présents → 80%)")
    ck("aws" in bd.missing_skills, True, f"aws dans missing_skills : {bd.missing_skills}")
    # Experience : CV 8 ans / Senior requis 5 ans → 100%.
    exp_c = next(c for c in bd.criteria if c.name == "experience")
    ck(round(exp_c.score), 100, f"experience score {exp_c.score} (8 ans / 5 ans requis → 100%)")
    # Certifications : Scrum Master requis + présent → 100%.
    cert_c = next(c for c in bd.criteria if c.name == "certifications")
    ck(round(cert_c.score), 100, f"certifications score {cert_c.score} (Scrum Master requis + présent → 100%)")
    # Location : Paris matche Paris → 100%.
    loc_c = next(c for c in bd.criteria if c.name == "location")
    ck(round(loc_c.score), 100, f"location score {loc_c.score} (Paris=Paris → 100%)")
    # Salary : overlap 55-75k vs CV 50-70k → 100%.
    sal_c = next(c for c in bd.criteria if c.name == "salary")
    ck(round(sal_c.score), 100, f"salary score {sal_c.score} (overlap ranges → 100%)")
    # Languages : anglais requis + fr,en présent → 100%.
    lang_c = next(c for c in bd.criteria if c.name == "languages")
    ck(round(lang_c.score), 100, f"languages score {lang_c.score} (en requis + présent → 100%)")

    # ── Disclaimer anti-probabilité-recrutement ──
    print("--- disclaimer : anti-sur-déclaration (compatibilité, pas probabilité) ---")
    ck("probabilité de recrutement" in bd.disclaimer, True, "disclaimer mentionne 'probabilité de recrutement'")
    ck("NE PAS" in bd.disclaimer, True, "disclaimer dit 'NE PAS présenter comme'")
    ck("compatibilité" in bd.disclaimer.lower(), True, "disclaimer = compatibilité (pas prédictif)")

    # ── Eliminatory : salaire < 70% du CV min → cap 60 ──
    print("--- eliminatory : salaire < 70% CV min → cap 60 ---")
    posting_low = M.JobPosting(id="jbp_low", dedup_hash="h2", source_connector="linkedin_jobs",
                               title="Junior PO", company="LowCorp", location="Paris",
                               description="Junior Product Owner. python, scrum.",
                               contract_type="CDI", salary_min=20000, salary_max=30000, currency="EUR",
                               is_expired=False, fetched_at=__import__("datetime").datetime.utcnow())
    s.add(posting_low)
    s.commit()
    bd_low = matching.compute_match(cv, posting_low, profile)
    # Salaire 20-30k < 70% de 50k (35k) → éliminatoire + cap 60.
    sal_low = next(c for c in bd_low.criteria if c.name == "salary")
    ck(sal_low.eliminatory, True, f"salary eliminatory (20-30k < 70% de 50k CV min)")
    ck(any("salary" in f for f in bd_low.eliminatory_flags), True, "flag eliminatory salary présent")
    ck(bd_low.total_score <= 60, True, f"total_score capé à 60 (éliminatoire) : {bd_low.total_score}")

    # ── estimate_experience_years : heuristic "X ans" ──
    print("--- estimate_experience_years : heuristic 'X ans' ---")
    ck(matching.estimate_experience_years({"experience": ["5 ans d'expérience", "autre"]}), 5, "parse '5 ans' → 5")
    ck(matching.estimate_experience_years({"experience": ["poste 1", "poste 2", "poste 3"]}), 6, "3 postes × 2 ans → 6")
    ck(matching.estimate_experience_years({"experience": []}), 0, "0 expérience → 0 ans")

    # ── estimate_seniority_years : title heuristic ──
    print("--- estimate_seniority_years : title heuristic ---")
    ck(matching.estimate_seniority_years("Senior Product Owner"), 5, "Senior → 5 ans")
    ck(matching.estimate_seniority_years("Lead Data Engineer"), 7, "Lead → 7 ans")
    ck(matching.estimate_seniority_years("Junior Developer"), 1, "Junior → 1 an")
    ck(matching.estimate_seniority_years("Product Owner"), 3, "default mid → 3 ans")

    # ── extract_required_skills : TECH_KEYWORDS match ──
    print("--- extract_required_skills : TECH_KEYWORDS match dans description ---")
    req = matching.extract_required_skills("Looking for python, sql, scrum, aws, kubernetes expert.")
    ck("python" in req, True, f"python détecté : {req}")
    ck("sql" in req, True, "sql détecté")
    ck("scrum" in req, True, "scrum détecté")
    ck(matching.extract_required_skills(None), [], "description None → []")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
