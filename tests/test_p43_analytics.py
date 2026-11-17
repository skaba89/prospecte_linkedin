#!/usr/bin/env python3
"""test_p43_analytics.py — P43 Career Analytics.

Valide :
  - Service career_analytics : overview (KPIs : offres, candidatures, score moyen, taux réponse/entretien,
    refus, propositions, sessions entretien), pipeline, performance_by_platform, performance_by_cv,
    salary_trends. Anti-invention : statistiques factuelles, aucune prédiction.
  - Router : 5 endpoints /api/career/analytics (overview, pipeline, performance-by-platform,
    performance-by-cv, salary-trends).
  - Isolation : KPIs scopés par career_profile_id (user A n'a pas les stats de user B).
  - Agrégation correcte : taux réponse = (interview+offer+rejected)/(total-withdrawn),
    taux entretien = (interview+offer)/total.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if got == expected:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'"); failed += 1


print("=== test_p43_analytics.py — P43 Career Analytics ===\n")

models_src = (ROOT / "backend/app/models.py").read_text(encoding="utf-8")
analytics_src = (ROOT / "backend/app/services/career_analytics.py").read_text(encoding="utf-8")
career_router = (ROOT / "backend/app/routers/career.py").read_text(encoding="utf-8")

print("--- Service career_analytics ---")
ck("def overview(" in analytics_src, True, "overview(db, profile_id)")
ck("def performance_by_platform(" in analytics_src, True, "performance_by_platform (group by source_connector)")
ck("def performance_by_cv(" in analytics_src, True, "performance_by_cv (group by adapted_cv_id)")
ck("def salary_trends(" in analytics_src, True, "salary_trends (avg salary par mois)")
ck("def pipeline(" in analytics_src, True, "pipeline (distribution statuts)")
ck("is_expired=False" in analytics_src, True, "offres_détectées = JobPosting actifs (is_expired=False)")
ck("type=\"new_match\"" in analytics_src or "type='new_match'" in analytics_src, True, "offres_compatibles = notifs new_match")
ck("response_rate" in analytics_src and "interview_rate" in analytics_src, True, "taux réponse + taux entretien")
ck("anti-invention" in analytics_src.lower() or "factuelle" in analytics_src.lower(), True, "anti-invariant (statistiques factuelles, pas prédiction)")

print("\n--- Router (5 endpoints) ---")
ck('@router.get("/analytics/overview"' in career_router, True, "GET /analytics/overview")
ck('@router.get("/analytics/pipeline"' in career_router, True, "GET /analytics/pipeline")
ck('@router.get("/analytics/performance-by-platform"' in career_router, True, "GET /analytics/performance-by-platform")
ck('@router.get("/analytics/performance-by-cv"' in career_router, True, "GET /analytics/performance-by-cv")
ck('@router.get("/analytics/salary-trends"' in career_router, True, "GET /analytics/salary-trends")
ck("career_analytics.overview" in career_router, True, "hook service career_analytics")

# ─── Runtime (in-memory sqlite + service layer) ──────────────────────────────
print("\n--- Runtime (in-memory sqlite + career_analytics service) ---")
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
    from app.services import career_analytics

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()

    # User A : profile + 2 CV + 4 postings + 5 applications (statuts variés) + 3 new_match notifs + 2 interview sessions
    s.add(M.User(id="usr_a", email="a@test.com", name="A", password_hash="h"))
    s.add(M.CareerProfile(id="crp_a", user_id="usr_a", target_job_title="Data Engineer"))
    s.add(M.CV(id="cv_a1", career_profile_id="crp_a", title="CV v1", source_type="text", raw_text="x",
               parsed_data={"skills": ["python"], "experience": [], "certifications": [], "education": [], "heuristic": True}, is_active=True, locale="fr"))
    # Postings (2 active + salary)
    s.add(M.JobPosting(id="jp1", dedup_hash="h1", source_connector="linkedin_jobs", title="DE Senior", company="Corp",
                      location="Paris", is_expired=False, salary_min=55000, salary_max=70000))
    s.add(M.JobPosting(id="jp2", dedup_hash="h2", source_connector="freework", title="DE Lead", company="Corp2",
                      location="Lyon", is_expired=False, salary_min=60000, salary_max=80000))
    # Applications : 2 submitted, 1 interview, 1 offer, 1 rejected (total 5)
    now = datetime.now()  # local (avoid DTZ003 utcnow lint on tests/ — timestamps just need to be recent)
    for i, (aid, pid, st, days_ago) in enumerate([
        ("app1", "jp1", "submitted", 1), ("app2", "jp2", "submitted", 2),
        ("app3", "jp1", "interview", 5), ("app4", "jp2", "offer", 10), ("app5", "jp1", "rejected", 15)]):
        s.add(M.JobApplication(id=aid, career_profile_id="crp_a", posting_id=pid, company="C", job_title="DE",
                              status=st, application_method="manual", applied_at=now - timedelta(days=days_ago)))
    # 3 new_match notifs (scores 80, 75, 65 → avg 73)
    for i, (nid, pid, sc) in enumerate([("n1", "jp1", 80), ("n2", "jp2", 75), ("n3", "jp1", 65)]):
        s.add(M.CareerNotification(id=nid, user_id="usr_a", career_profile_id="crp_a", type="new_match",
                                   channel="in_app", title="t", body="b", payload={"posting_id": pid, "score": sc}))
    # 2 interview sessions (1 completed score 70, 1 in_progress)
    s.add(M.InterviewSession(id="is1", career_profile_id="crp_a", target_job_title="DE", session_type="rhmixed",
                             status="completed", score=70, questions=[], answers=[], created_at=now))
    s.add(M.InterviewSession(id="is2", career_profile_id="crp_a", target_job_title="DE", session_type="tech",
                             status="in_progress", questions=[], answers=[], created_at=now))
    # User B (isolation)
    s.add(M.User(id="usr_b", email="b@test.com", name="B", password_hash="h"))
    s.add(M.CareerProfile(id="crp_b", user_id="usr_b", target_job_title="PO"))
    s.add(M.JobApplication(id="app_b", career_profile_id="crp_b", company="X", job_title="PO", status="submitted",
                          application_method="manual", applied_at=now))
    s.commit()

    # 1. overview : KPIs principaux
    ov = career_analytics.overview(s, "crp_a")
    ck(ov["offers_detected"], 2, "offers_detected = 2 (postings actifs, global)")
    ck(ov["offers_compatible"], 3, "offers_compatible = 3 (new_match notifs)")
    ck(ov["applications_total"], 5, "applications_total = 5")
    ck(ov["avg_match_score"], 73, f"avg_match_score = 73 (avg 80+75+65) — got {ov['avg_match_score']}")
    # taux réponse = (interview+offer+rejected)/(total-withdrawn) = (1+1+1)/(5-0) = 60%
    ck(ov["response_rate"], 60, "response_rate = 60% (3 réponses / 5 non-withdrawn)")
    # taux entretien = (interview+offer)/total = (1+1)/5 = 40%
    ck(ov["interview_rate"], 40, "interview_rate = 40% (2 en entretien/offre / 5)")
    ck(ov["rejections"], 1, "rejections = 1")
    ck(ov["offers_received"], 1, "offers_received = 1")
    ck(ov["interview_sessions"], 2, "interview_sessions = 2")
    ck(ov["interview_sessions_completed"], 1, "interview_sessions_completed = 1")
    ck(ov["avg_interview_score"], 70, "avg_interview_score = 70")
    ck(ov["cvs_count"], 1, "cvs_count = 1")
    ck("anti-invention" in ov["note"].lower() or "factuelle" in ov["note"].lower(), True, "overview note anti-invention")

    # 2. pipeline
    pl = career_analytics.pipeline(s, "crp_a")
    ck(pl["submitted"], 2, "pipeline submitted = 2")
    ck(pl["interview"], 1, "pipeline interview = 1")
    ck(pl["offer"], 1, "pipeline offer = 1")

    # 3. performance_by_platform
    pbp = career_analytics.performance_by_platform(s, "crp_a")
    linkedin = next((p for p in pbp if p["platform"] == "linkedin_jobs"), None)
    ck(linkedin is not None and linkedin["total"], 3, "performance_by_platform linkedin_jobs = 3 apps (jp1)")
    freework = next((p for p in pbp if p["platform"] == "freework"), None)
    ck(freework is not None and freework["total"], 2, "performance_by_platform freework = 2 apps (jp2)")

    # 4. performance_by_cv
    pbc = career_analytics.performance_by_cv(s, "crp_a")
    ck(len(pbc), 1, "performance_by_cv = 1 groupe (toutes 'original' — pas d'adapted_cv)")
    ck(pbc[0]["total"], 5, "performance_by_cv 'original' total = 5")

    # 5. salary_trends
    st = career_analytics.salary_trends(s, "crp_a", months=6)
    ck(len(st) >= 1, True, f"salary_trends ≥ 1 mois avec données ({len(st)} mois)")
    if st:
        ck(st[0]["avg_salary_min"] is not None or st[0]["avg_salary_max"] is not None, True, "salary_trends avg_salary renseigné")

    # 6. isolation : user B overview
    ov_b = career_analytics.overview(s, "crp_b")
    ck(ov_b["applications_total"], 1, "isolation : user B applications_total = 1 (pas 5 de A)")
    ck(ov_b["offers_compatible"], 0, "isolation : user B offers_compatible = 0 (pas les notifs de A)")
    s.close()

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
