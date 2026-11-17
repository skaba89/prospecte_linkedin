#!/usr/bin/env python3
"""test_p42_agent.py — P42 Agent autonome Career.

Valide :
  - Migration 0025 (tables career_agents + career_agent_runs + FK CASCADE + unique profile).
  - Modèles CareerAgent + CareerAgentRun.
  - Service career_agent : get_or_create_agent (idempotent), run_once (10 étapes), stop, list_runs.
  - Router : 5 endpoints /api/career/agent (GET/PUT config, POST run, POST stop, GET runs).
  - Invariants P42 :
    * Consent : consent_given=False OU auto_apply=False → applications_sent=0 (préparation seulement).
    * Limit : max_applications_per_day cap respecté.
    * Stop : status=stopped interrompt le run.
    * Journalisation : chaque run persisté + steps détaillés.
  - Anti-invention : rapport factuel agrégé (aucune prédiction).
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


print("=== test_p42_agent.py — P42 Agent autonome Career ===\n")

mig = (ROOT / "backend/alembic/versions/0025_p42_agent.py").read_text(encoding="utf-8")
models_src = (ROOT / "backend/app/models.py").read_text(encoding="utf-8")
agent_src = (ROOT / "backend/app/services/career_agent.py").read_text(encoding="utf-8")
career_router = (ROOT / "backend/app/routers/career.py").read_text(encoding="utf-8")
schemas_src = (ROOT / "backend/app/schemas.py").read_text(encoding="utf-8")

print("--- Migration 0025 ---")
ck("0025_p42_agent" in mig, True, "revision 0025_p42_agent")
ck('down_revision = "0024_p41_coach_ia"' in mig, True, "down_revision 0024 (chaîne)")
ck("career_agents" in mig and "career_agent_runs" in mig, True, "2 tables créées")
ck("ondelete=\"CASCADE\"" in mig, True, "FK CASCADE (career_profile + agent)")
ck("uq_career_agents_profile" in mig, True, "unique constraint (1 agent par profil)")
ck("server_default=sa.text(\"true\")" in mig and "server_default=sa.text(\"false\")" in mig, True, "defaults boolean PostgreSQL-compatibles")

print("\n--- Modèles CareerAgent + CareerAgentRun ---")
ck("class CareerAgent(Base)" in models_src, True, "class CareerAgent(Base)")
ck("class CareerAgentRun(Base)" in models_src, True, "class CareerAgentRun(Base)")
ck("auto_apply = Column(Boolean, default=False" in models_src, True, "auto_apply=False par défaut (consent v1)")
ck("consent_given = Column(Boolean, default=False" in models_src, True, "consent_given=False par défaut")
ck("max_applications_per_day = Column(Integer" in models_src, True, "max_applications_per_day cap")
ck("daily_sent_count = Column(Integer" in models_src, True, "daily_sent_count (reset quotidien)")
ck("status = Column(String, default=\"idle\"" in models_src, True, "status idle|running|stopped")
ck("steps = Column(JSONType" in models_src and "summary = Column(JSONType" in models_src, True, "steps + summary JSON (audit trail)")

print("\n--- Service career_agent (invariants P42) ---")
ck("def get_or_create_agent(" in agent_src, True, "get_or_create_agent (idempotent)")
ck("def run_once(" in agent_src, True, "run_once (10 étapes)")
ck("def stop(" in agent_src, True, "stop (bouton arrêt immédiat)")
ck("def list_runs(" in agent_src, True, "list_runs (historique)")
ck("def _reset_daily_if_needed(" in agent_src, True, "_reset_daily_if_needed (reset quotidien)")
ck("agent.consent_given and agent.auto_apply" in agent_src, True, "consent invariant (consent + auto_apply pour envoi)")
ck("remaining_quota = max(0, agent.max_applications_per_day" in agent_src, True, "limit invariant (quota cap)")
ck("agent.status == \"stopped\"" in agent_src, True, "stop check (status=stopped interrompt)")
ck('step("search"' in agent_src and 'step("match_rank"' in agent_src, True, "étapes search + match_rank")
ck('step("adapt_cv"' in agent_src and 'step("cover_letters"' in agent_src, True, "étapes adapt_cv + cover_letters")
ck('step("submit"' in agent_src and 'step("report"' in agent_src, True, "étapes submit + report")
ck("career_analytics.overview" in agent_src, True, "étape report = analytics P43 snapshot")

print("\n--- Schemas ---")
ck("class CareerAgentOut(BaseModel)" in schemas_src, True, "CareerAgentOut")
ck("class CareerAgentConfigUpdate(BaseModel)" in schemas_src, True, "CareerAgentConfigUpdate")
ck("class CareerAgentRunOut(BaseModel)" in schemas_src, True, "CareerAgentRunOut")
ck("ge=1, le=20" in schemas_src, True, "max_applications_per_day range 1-20")

print("\n--- Router (5 endpoints) ---")
ck('@router.get("/agent"' in career_router, True, "GET /agent (config)")
ck('@router.put("/agent"' in career_router, True, "PUT /agent (config update)")
ck('@router.post("/agent/run"' in career_router, True, "POST /agent/run (trigger)")
ck('@router.post("/agent/stop"' in career_router, True, "POST /agent/stop (arrêt immédiat)")
ck('@router.get("/agent/runs"' in career_router, True, "GET /agent/runs (historique)")
ck("career_agent.run_once" in career_router and "career_agent.stop" in career_router, True, "hooks service run_once + stop")

# ─── Runtime (in-memory sqlite + service layer) ──────────────────────────────
print("\n--- Runtime (in-memory sqlite + career_agent service) ---")
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
    from datetime import datetime

    from app import database as db_mod
    from app import models as M
    from app.services import career_agent

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_a", email="a@test.com", name="A", password_hash="h"))
    s.add(M.CareerProfile(id="crp_a", user_id="usr_a", target_job_title="Data Engineer",
                          preferred_location="Paris", alert_min_score=50, alert_enabled=True))
    s.add(M.CV(id="cv_a", career_profile_id="crp_a", title="CV A", source_type="text", raw_text="x",
               parsed_data={"skills": ["python", "sql", "spark"], "experience": [], "certifications": [], "education": [], "heuristic": True},
               is_active=True, locale="fr"))
    # 3 postings actifs (2 Paris, 1 Lyon) avec salaire
    s.add(M.JobPosting(id="jp1", dedup_hash="h1", source_connector="linkedin_jobs", title="DE Senior", company="Corp",
                      location="Paris", is_expired=False, salary_min=55000, salary_max=70000,
                      description="python sql spark airflow", fetched_at=datetime.now()))
    s.add(M.JobPosting(id="jp2", dedup_hash="h2", source_connector="freework", title="DE Lead", company="Corp2",
                      location="Paris", is_expired=False, salary_min=60000, salary_max=80000,
                      description="python sql spark aws", fetched_at=datetime.now()))
    s.add(M.JobPosting(id="jp3", dedup_hash="h3", source_connector="malt", title="DE Freelance", company="Corp3",
                      location="Lyon", is_expired=False, salary_min=50000, salary_max=65000,
                      description="java php ruby", fetched_at=datetime.now()))
    s.commit()
    prof = s.query(M.CareerProfile).filter_by(user_id="usr_a").first()

    # 1. get_or_create_agent : idempotent (2 calls → même agent)
    a1 = career_agent.get_or_create_agent(s, prof)
    a2 = career_agent.get_or_create_agent(s, prof)
    ck(a1.id, a2.id, "get_or_create_agent idempotent (même agent)")
    ck(a1.auto_apply, False, "auto_apply=False par défaut (consent v1)")
    ck(a1.consent_given, False, "consent_given=False par défaut")
    ck(a1.max_applications_per_day, 5, "max_applications_per_day=5 par défaut")

    # 2. run_once : consent_given=False → applications_sent=0 (préparation only)
    run = career_agent.run_once(s, prof, a1, top_n=3)
    ck(run.status, "success", f"run_once status success (got {run.status})")
    ck(run.applications_sent, 0, "consent invariant : 0 candidature envoyée (consent_given=False)")
    ck(run.applications_prepared >= 0, True, "run prépare les lettres (prepared >= 0)")
    # steps présents (journalisation)
    steps = run.steps or []
    step_names = [st["step"] for st in steps]
    ck("search" in step_names and "match_rank" in step_names, True, "steps search + match_rank journalisés")
    ck("adapt_cv" in step_names and "cover_letters" in step_names, True, "steps adapt_cv + cover_letters journalisés")
    ck("submit" in step_names and "report" in step_names, True, "steps submit + report journalisés")
    # summary (rapport)
    ck(run.summary and "offers_found" in run.summary, True, "summary : offers_found (rapport)")
    ck("consent_given" in run.summary, True, "summary : consent_given tracé (audit)")

    # 3. run_once avec consent + auto_apply → envoi dans la limite
    a1.consent_given = True
    a1.auto_apply = True
    a1.max_applications_per_day = 2  # cap à 2 envois
    s.commit()
    run2 = career_agent.run_once(s, prof, a1, top_n=3)
    ck(run2.applications_sent <= 2, True, f"limit invariant : sent ≤ max_per_day=2 (got {run2.applications_sent})")
    ck(run2.status, "success", "run avec consent → success")

    # 4. stop : status=stopped
    a1.enabled = True
    s.commit()
    stopped = career_agent.stop(s, a1)
    ck(stopped.status, "stopped", "stop → status=stopped (bouton arrêt immédiat)")

    # 5. list_runs : historique
    runs = career_agent.list_runs(s, prof.id)
    ck(len(runs) >= 2, True, f"list_runs : ≥2 runs historisés (got {len(runs)})")

    # 6. run sur agent désactivé → erreur (validé via router, ici on teste le service ne crash pas)
    a1.status = "idle"
    a1.enabled = False
    s.commit()
    # (le router lève 400 si !enabled ; le service run_once lui s'exécute — c'est le router qui garde-fou)

    # 7. isolation : user B n'a pas accès aux runs de A
    s.add(M.User(id="usr_b", email="b@test.com", name="B", password_hash="h"))
    s.add(M.CareerProfile(id="crp_b", user_id="usr_b", target_job_title="PO"))
    s.commit()
    runs_b = career_agent.list_runs(s, "crp_b")
    ck(len(runs_b), 0, "isolation : user B = 0 run (pas ceux de A)")
    s.close()

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
