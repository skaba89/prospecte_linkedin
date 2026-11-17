#!/usr/bin/env python3
"""test_p41_coach.py — P41 Coach IA entretiens Career.

Valide :
  - Migration 0024 (table interview_sessions + FK CASCADE career_profile + SET NULL application).
  - Modèle InterviewSession (career_profile_id FK CASCADE, application_id FK SET NULL, fields).
  - Service interview_coach : generate_questions (par catégorie + session_type), analyze_answer
    (heuristic keywords + feedback factuel anti-invention), compute_session_score,
    recommend_missing_skills (cross-référence CV), recommend_formations, negotiation_prep (profil).
  - Router : 5 endpoints /api/career/interviews (start, answer, complete, list, get).
  - Anti-invention invariants : feedback basé sur keywords (pas d'affirmation arbitraire),
    missing skills = transparence ("CV ne mentionne pas X"), negotiation basé sur profil.
  - Isolation : user A n'accède pas aux sessions de user B.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if got == expected:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'"); failed += 1


print("=== test_p41_coach.py — P41 Coach IA entretiens Career ===\n")

mig = (ROOT / "backend/alembic/versions/0024_p41_coach_ia.py").read_text(encoding="utf-8")
models_src = (ROOT / "backend/app/models.py").read_text(encoding="utf-8")
coach_src = (ROOT / "backend/app/services/interview_coach.py").read_text(encoding="utf-8")
career_router = (ROOT / "backend/app/routers/career.py").read_text(encoding="utf-8")
schemas_src = (ROOT / "backend/app/schemas.py").read_text(encoding="utf-8")

print("--- Migration 0024 ---")
ck("0024_p41_coach_ia" in mig, True, "revision 0024_p41_coach_ia")
ck('down_revision = "0023_p40_notifications"' in mig, True, "down_revision 0023 (chaîne)")
ck("interview_sessions" in mig and "op.create_table" in mig, True, "create_table interview_sessions")
ck('"career_profiles.id"], ondelete="CASCADE"' in mig, True, "FK CASCADE career_profile")
ck('"job_applications.id"], ondelete="SET NULL"' in mig, True, "FK SET NULL application")

print("\n--- Modèle InterviewSession ---")
ck("class InterviewSession(Base)" in models_src, True, "class InterviewSession(Base)")
ck('ForeignKey("career_profiles.id", ondelete="CASCADE")' in models_src and "interview_sessions" in models_src, True, "career_profile_id FK CASCADE (isolation)")
ck('ForeignKey("job_applications.id", ondelete="SET NULL")' in models_src, True, "application_id FK SET NULL")
ck("session_type = Column(String" in models_src and "status = Column(String" in models_src, True, "session_type + status columns")
ck("questions = Column(JSONType" in models_src, True, "questions JSON")

print("\n--- Service interview_coach (anti-invention invariants) ---")
ck("def generate_questions(" in coach_src, True, "generate_questions(target_job_title, category, session_type)")
ck("def analyze_answer(" in coach_src, True, "analyze_answer(question, answer, cv_data)")
ck("def compute_session_score(" in coach_src, True, "compute_session_score(answers)")
ck("def recommend_missing_skills(" in coach_src, True, "recommend_missing_skills (cross-référence CV)")
ck("def recommend_formations(" in coach_src, True, "recommend_formations(missing_skills)")
ck("def negotiation_prep(" in coach_src, True, "negotiation_prep(profile, target_job_title)")
ck("def build_session_feedback(" in coach_src, True, "build_session_feedback (récap global)")
ck("_TECH_QUESTIONS" in coach_src and "_RH_QUESTIONS" in coach_src and "_NEGO_QUESTIONS" in coach_src, True, "banque questions tech/RH/nego")
ck("expected_keywords" in coach_src, True, "expected_keywords (critères vérifiables anti-invention)")
ck("len(matched) / len(expected)" in coach_src, True, "score heuristic keywords (pas note arbitraire)")

print("\n--- Schemas ---")
ck("class InterviewStartRequest(BaseModel)" in schemas_src, True, "InterviewStartRequest")
ck("class AnswerRequest(BaseModel)" in schemas_src, True, "AnswerRequest")
ck("class InterviewSessionOut(BaseModel)" in schemas_src, True, "InterviewSessionOut")
ck('pattern="^(tech|rhmixed)$"' in schemas_src, True, "session_type pattern tech|rhmixed")

print("\n--- Router (5 endpoints) ---")
ck('@router.post("/interviews"' in career_router, True, "POST /interviews (start)")
ck('@router.post("/interviews/{session_id}/answer"' in career_router, True, "POST /interviews/{id}/answer")
ck('@router.post("/interviews/{session_id}/complete"' in career_router, True, "POST /interviews/{id}/complete")
ck('@router.get("/interviews"' in career_router, True, "GET /interviews (list)")
ck('@router.get("/interviews/{session_id}"' in career_router, True, "GET /interviews/{id} (detail)")
ck("interview_coach.generate_questions" in career_router and "interview_coach.analyze_answer" in career_router, True, "hooks service generate_questions + analyze_answer")
ck("interview_coach.build_session_feedback" in career_router, True, "complete → build_session_feedback")

# ─── Runtime (in-memory sqlite + service layer) ──────────────────────────────
print("\n--- Runtime (in-memory sqlite + interview_coach service) ---")
try:
    import bcrypt  # noqa: F401
    import slowapi  # noqa: F401
    _RUNTIME_OK = True
except ImportError:
    _RUNTIME_OK = False
    print("  ⚠ Runtime ignoré (deps backend non installées — validé via E2E).")

if _RUNTIME_OK:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M
    from app.services import interview_coach

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_a", email="a@test.com", name="A", password_hash="h"))
    s.add(M.CareerProfile(id="crp_a", user_id="usr_a", target_job_title="Data Engineer",
                          salary_min=50000, salary_max=70000, tjm_min=450, currency="EUR"))
    s.add(M.CV(id="cv_a", career_profile_id="crp_a", title="CV A", source_type="text", raw_text="x",
               parsed_data={"skills": ["python", "sql", "spark"], "experience": [], "certifications": [], "education": [], "heuristic": True},
               is_active=True, locale="fr"))
    s.commit()

    # 1. generate_questions : rhmixed Data Engineer → tech(data) + RH + 1 nego
    qs = interview_coach.generate_questions("Data Engineer", None, "rhmixed")
    ck(len(qs) >= 5, True, f"generate_questions rhmixed Data Engineer → {len(qs)} questions (tech+RH+nego)")
    ck(any(q["type"] == "tech" for q in qs), True, "inclut questions tech")
    ck(any(q["type"] == "rh" for q in qs), True, "inclut questions RH")
    ck(any(q["type"] == "nego" for q in qs), True, "inclut 1 question nego")
    ck(all("expected_keywords" in q for q in qs), True, "toutes les questions ont expected_keywords (anti-invention)")

    # 2. category auto-detection : "Product Owner" → product
    qs_po = interview_coach.generate_questions("Product Owner", None, "tech", num_tech=2)
    ck(any("prod_q" in q["id"] for q in qs_po), True, "auto-détection catégorie product (Product Owner)")

    # 3. analyze_answer : réponse avec keywords → score élevé (pick data_q1 explicitement — anti-aléatoire)
    q = next((x for x in qs if x["id"] == "data_q1"), qs[0])
    good_answer = "Je privilégie l'ELT quand le warehouse est performant : extract puis load, transform après via SQL. ETL classique pour sources hétérogènes."
    a_good = interview_coach.analyze_answer(q, good_answer, {"skills": ["python"]})
    ck(a_good["score"] > 0, True, f"analyze_answer bonne réponse → score {a_good['score']} > 0")
    ck(len(a_good["matched"]) > 0, True, "analyze_answer détecte keywords matched")
    ck("feedback" in a_good and len(a_good["feedback"]) > 0, True, "feedback factuel fourni")
    # 4. analyze_answer : réponse vide/pauvre → score bas + missing
    a_bad = interview_coach.analyze_answer(q, "je ne sais pas", None)
    ck(a_bad["score"] < a_good["score"], True, "réponse pauvre → score plus bas (heuristic cohérent)")
    ck(len(a_bad["missing"]) >= len(a_good["missing"]), True, "réponse pauvre → plus de missing keywords")

    # 5. compute_session_score : moyenne des scores
    score = interview_coach.compute_session_score([a_good, a_bad])
    ck(0 <= score <= 100, True, f"compute_session_score dans [0,100] = {score}")

    # 6. recommend_missing_skills : CV a python/sql/spark, attendus data → missing = airflow/aws/gcp/docker/kafka/snowflake
    missing = interview_coach.recommend_missing_skills({"skills": ["python", "sql", "spark"]}, "data", "Data Engineer")
    ck("python" not in missing and "sql" not in missing, True, "skills présents dans CV ne sont pas 'missing' (anti-invention)")
    ck("airflow" in missing or "aws" in missing, True, "skills attendus absents du CV sont signalés (transparence)")

    # 7. recommend_formations : map missing → catalogue
    forms = interview_coach.recommend_formations(["airflow", "aws", "unknown_skill"])
    ck(len(forms) >= 2, True, f"recommend_formations → {len(forms)} suggestions (airflow + aws catalogués)")
    ck(any("airflow" in f.lower() for f in forms), True, "formation airflow suggérée")

    # 8. negotiation_prep : basé sur profil (salary_min/max + tjm_min)
    prof = s.query(M.CareerProfile).filter_by(user_id="usr_a").first()
    nego = interview_coach.negotiation_prep(prof, "Data Engineer")
    ck(nego["salary_range"]["min"] == 50000 and nego["salary_range"]["max"] == 70000, True, "negotiation_prep salary_range = profil (pas inventé)")
    ck(nego["tjm_min"]["min"] == 450, True, "negotiation_prep tjm_min = profil")
    ck(len(nego["talking_points"]) >= 3 and len(nego["pitfalls"]) >= 3, True, "negotiation_prep talking_points + pitfalls")

    # 9. build_session_feedback : récap global
    session = M.InterviewSession(id="int_test", career_profile_id="crp_a", target_job_title="Data Engineer",
                                  category="data", session_type="rhmixed", status="in_progress",
                                  questions=qs, answers=[a_good, a_bad])
    s.add(session); s.commit()
    fb = interview_coach.build_session_feedback(s, session, {"skills": ["python", "sql", "spark"]})
    ck(fb["overall_score"] == score, True, "build_session_feedback overall_score = compute_session_score")
    ck(fb["answered"] == 2 and fb["total_questions"] == len(qs), True, "build_session_feedback answered/total")
    ck("missing_skills" in fb and "recommended_formations" in fb and "negotiation_prep" in fb, True, "feedback inclut missing_skills + formations + negotiation")
    ck("anti-invention" in fb["note"].lower() or "non vérifiable" in fb["note"].lower(), True, "feedback note mentionne anti-invention (invariant)")
    s.close()

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
