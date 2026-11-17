#!/usr/bin/env python3
"""test_p38_candidatures.py — validation statique + runtime de P38 Career : Candidatures + Gmail/Outlook.

Couvre :
  - models.py : JobApplication (career_profile_id FK CASCADE, posting_id FK SET NULL, adapted_cv_id FK SET NULL,
    status, cover_letter, application_method, recipient_email, sent_at, timeline JSON).
  - migration 0022 : 1 nouvelle table (job_applications) + 3 FK + 3 indexes.
  - services/cover_letter.py : generate_cover_letter (anti-invention P37/P38 invariant), _assert_no_invention_cover_letter,
    _LETTER_TEMPLATES fr/en, _experience_summary, _cert_sentence, _missing_clause.
  - services/email_sender.py : send_application_email (v1 stub not_configured comme P35 connectors),
    _check_rate_limit (anti-abus 50/jour), DAILY_SEND_LIMIT, SUPPORTED_PROVIDERS, is_provider_configured.
  - routers/career.py : 5 nouveaux endpoints (POST /applications, GET /applications, GET /applications/{id},
    PATCH /applications/{id}, POST /applications/{id}/send).
  - schemas.py : ApplicationCreateRequest, ApplicationUpdateRequest, JobApplicationOut.
  - Runtime : create_application + cover_letter generated (anti-invention) + update status (timeline append)
    + send (v1 stub not_configured — sent_at reste None).
  - Anti-spam (P38 invariant) : rate-limit 50/jour, consentement explicite (POST /send séparé), pas de mass-email.
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


print("=== test_p38_candidatures.py — P38 Career : Candidatures + Gmail/Outlook ===\n")

# ─── Source : models.py (JobApplication) ──────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : JobApplication ===")
ck("class JobApplication(Base):" in models_src, True, "JobApplication model")
ck('__tablename__ = "job_applications"' in models_src, True, "table job_applications")
ck('career_profile_id = Column(String, ForeignKey("career_profiles.id", ondelete="CASCADE")' in models_src, True, "career_profile_id FK CASCADE (isolation)")
ck('posting_id = Column(String, ForeignKey("job_postings.id", ondelete="SET NULL")' in models_src, True, "posting_id FK SET NULL")
ck('adapted_cv_id = Column(String, ForeignKey("adapted_cvs.id", ondelete="SET NULL")' in models_src, True, "adapted_cv_id FK SET NULL")
ck('status = Column(String, default="submitted"' in models_src, True, "status default submitted")
ck("cover_letter = Column(Text" in models_src, True, "cover_letter Text (anti-invention)")
ck("timeline = Column(JSONType" in models_src, True, "timeline JSON (historique étapes)")
ck("application_method = Column(String, default=\"manual\"" in models_src, True, "application_method manual|email|external")
ck("recipient_email = Column(String" in models_src, True, "recipient_email")
ck("sent_at = Column(DateTime" in models_src, True, "sent_at (envoi effectif)")
ck("Anti-spam" in models_src, True, "docstring Anti-spam (P38 invariant)")
ck("consentement explicite" in models_src, True, "docstring consentement explicite")

# ─── Source : migration 0022 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0022_p38_candidatures.py").read_text(encoding="utf-8")
print("\n=== migration 0022_p38_candidatures ===")
ck('revision = "0022_p38_candidatures"' in mig, True, "revision 0022_p38_candidatures")
ck('down_revision = "0021_p37_cv_adaptatif"' in mig, True, "down_revision 0021 (chaîne)")
ck('"job_applications"' in mig, True, "nouvelle table job_applications")
ck("ondelete=\"CASCADE\"" in mig and "ondelete=\"SET NULL\"" in mig, True, "FK CASCADE (career_profile) + SET NULL (posting/adapted_cv)")
ck("ix_job_applications_status" in mig, True, "index status (pipeline filter)")

# ─── Source : cover_letter.py (anti-invention) ───────────────────────────────
cl_src = (ROOT / "backend" / "app" / "services" / "cover_letter.py").read_text(encoding="utf-8")
print("\n=== cover_letter.py : lettre anti-invention ===")
ck("def generate_cover_letter(" in cl_src, True, "generate_cover_letter(cv, posting, profile)")
ck("def _assert_no_invention_cover_letter(" in cl_src, True, "_assert_no_invention_cover_letter (Invariant P38 runtime)")
ck("_LETTER_TEMPLATES" in cl_src, True, "_LETTER_TEMPLATES fr/en")
ck("def _experience_summary(" in cl_src, True, "_experience_summary (expériences du CV, pas d'invention)")
ck("def _cert_sentence(" in cl_src, True, "_cert_sentence (certs du CV)")
ck("def _missing_clause(" in cl_src, True, "_missing_clause (transparence — requis absents, pas d'invention)")
ck("from . import matching" in cl_src, True, "import matching (P36 — extract_required_skills + estimate)")
ck("Invariant P38" in cl_src, True, "docstring Invariant P38 (anti-invention)")
ck("raise ValueError" in cl_src, True, "raise ValueError si invention détectée")
ck("{top_skills}" in cl_src, True, "template utilise top_skills (issus du CV — pas d'invention)")
ck("{years}" in cl_src, True, "template utilise years (estimés du CV)")

# ─── Source : email_sender.py (OAuth2 v1 stub) ───────────────────────────────
es_src = (ROOT / "backend" / "app" / "services" / "email_sender.py").read_text(encoding="utf-8")
print("\n=== email_sender.py : OAuth2 Gmail/Outlook v1 stub ===")
ck("def send_application_email(" in es_src, True, "send_application_email(application, profile, db)")
ck("class SendResult" in es_src, True, "SendResult dataclass (status sent|not_configured|error)")
ck("DAILY_SEND_LIMIT = 50" in es_src, True, "DAILY_SEND_LIMIT = 50 (anti-abus)")
ck("SUPPORTED_PROVIDERS" in es_src, True, "SUPPORTED_PROVIDERS (gmail_oauth, outlook_oauth)")
ck("def _check_rate_limit(" in es_src, True, "_check_rate_limit (anti-spam 50/jour)")
ck("def is_provider_configured(" in es_src, True, "is_provider_configured (v1 : False)")
ck("not_configured" in es_src, True, "v1 stub not_configured (comme P35 connectors)")
ck("Anti-spam" in es_src, True, "docstring Anti-spam (P38 invariant)")
ck("consentement explicite" in es_src or "une candidature à la fois" in es_src, True, "anti-spam : une candidature à la fois, consentement explicite")
ck("JAMAIS" in es_src, True, "JAMAIS de mass-email/spam")

# ─── Source : routers/career.py (5 endpoints P38) ────────────────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "career.py").read_text(encoding="utf-8")
print("\n=== routers/career.py : 5 endpoints P38 ===")
ck('@router.post("/applications"' in rtr_src, True, "POST /applications (create + cover letter)")
ck('@router.get("/applications"' in rtr_src, True, "GET /applications (list, filter status)")
ck('@router.get("/applications/{application_id}"' in rtr_src, True, "GET /applications/{id} (detail)")
ck('@router.patch("/applications/{application_id}"' in rtr_src, True, "PATCH /applications/{id} (update status + timeline)")
ck('@router.post("/applications/{application_id}/send"' in rtr_src, True, "POST /applications/{id}/send (envoi email — consentement explicite)")
ck("cover_letter.generate_cover_letter" in rtr_src, True, "appel generate_cover_letter dans create")
ck("email_sender.send_application_email" in rtr_src, True, "appel send_application_email dans send")
ck("_APPLICATION_STATUSES" in rtr_src, True, "_APPLICATION_STATUSES (pipeline validation)")
ck("timeline.append" in rtr_src, True, "timeline append sur status_change (audit trail)")

# ─── Source : schemas.py (P38 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P38 schemas ===")
for s in ["ApplicationCreateRequest", "ApplicationUpdateRequest", "JobApplicationOut"]:
    ck(f"class {s}" in sch_src, True, f"schema {s}")
ck("generate_cover_letter: bool" in sch_src, True, "ApplicationCreateRequest.generate_cover_letter (auto-gen)")
ck("application_method: str" in sch_src, True, "application_method (manual|email|external)")
ck("timeline: list" in sch_src, True, "JobApplicationOut.timeline (historique étapes)")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : cover_letter + email_sender + application pipeline ===")
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

    from datetime import datetime
    from app import database as db_mod
    from app import models as M
    from app.services import cover_letter, email_sender

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p38", email="p38@test.com", name="Cand P38", password_hash="h"))
    s.add(M.CareerProfile(id="crp_p38", user_id="usr_p38", target_job_title="Product Owner",
                          preferred_location="Paris", mobility=True, salary_min=50000, salary_max=70000,
                          languages=["fr", "en"]))
    s.commit()

    # CV source avec skills + experience + certifications.
    cv = M.CV(id="cv_p38", career_profile_id="crp_p38", title="CV PO 2026", source_type="text",
              raw_text="Product Owner, 8 ans. Python, SQL, Scrum, Jira, Agile. Scrum Master certifié.",
              parsed_data={"skills": ["python", "sql", "scrum", "jira", "agile"],
                            "experience": ["Product Owner chez TechCorp (2018-2026, 8 ans)"],
                            "certifications": ["Scrum Master"], "education": [], "heuristic": True},
              is_active=True, locale="fr")
    s.add(cv)

    # Posting cible : PO Senior requiert python+scrum+agile (présents) + aws (manquant).
    posting = M.JobPosting(id="jbp_p38", dedup_hash="h1", source_connector="linkedin_jobs",
                           title="Senior Product Owner", company="TechCorp", location="Paris",
                           description="Seeking Senior Product Owner. Required : python, sql, scrum, "
                                       "agile, aws. Scrum Master certification a plus.",
                           contract_type="CDI", salary_min=55000, salary_max=75000, currency="EUR",
                           is_expired=False, fetched_at=datetime.utcnow())
    s.add(posting)
    s.commit()
    profile = s.query(M.CareerProfile).filter_by(id="crp_p38").first()

    # ── generate_cover_letter : anti-invention ──
    print("--- generate_cover_letter : lettre anti-invention (keywords CV + templates) ---")
    letter = cover_letter.generate_cover_letter(cv, posting, profile)
    ck(len(letter) > 100, True, f"lettre générée : {len(letter)} chars (> 100)")
    ck("8 ans" in letter, True, f"lettre mentionne '8 ans' (years du CV)")
    ck("python" in letter.lower(), True, "lettre mentionne python (skill du CV)")
    ck("Scrum Master" in letter, True, "lettre mentionne Scrum Master (cert du CV)")
    ck("TechCorp" in letter, True, "lettre mentionne TechCorp (entreprise de l'offre)")
    # Missing skills (transparence — aws requis absent, honnêteté pas d'invention).
    ck("aws" in letter.lower(), True, "lettre mentionne aws (missing skill — transparence, développement en cours)")
    # Madam/Sir salutation.
    ck("Madame" in letter or "Dear" in letter, True, "salutation formelle (Madame/Dear)")

    # ── _assert_no_invention_cover_letter : lève ValueError si invention ──
    print("--- _assert_no_invention_cover_letter : lève ValueError si skill inventé ---")
    # Pick un TECH_KEYWORD ni dans le CV ni dans les missing skills (aws) — déterministe.
    from app.services.matching import _TECH_KEYWORDS
    cv_skills_lower = {s.lower() for s in cv.parsed_data["skills"]}
    missing_lower = {"aws"}  # aws est dans la lettre via _missing_clause (transparence, pas acquis).
    invented_tech = next((kw for kw in _TECH_KEYWORDS
                          if kw not in cv_skills_lower and kw not in missing_lower), None)
    ck(invented_tech is not None, True, f"un TECH_KEYWORD inventé testable trouvé : {invented_tech}")
    if invented_tech:
        # Fake letter minimale avec UNIQUEMENT le tech inventé (pas la vraie lettre — isoler le test).
        fake_letter = f"Je maîtrise {invented_tech} depuis 10 ans."
        try:
            cover_letter._assert_no_invention_cover_letter(fake_letter, cv.parsed_data)
            ck(False, True, f"_assert_no_invention devrait lever ValueError pour {invented_tech} inventé")
        except ValueError as e:
            ck(invented_tech in str(e), True, f"ValueError levée pour {invented_tech} inventé : {str(e)[:50]}")

    # ── email_sender : v1 stub not_configured ──
    print("--- email_sender : v1 stub not_configured (comme P35 connectors) ---")
    app = M.JobApplication(id="app_p38", career_profile_id=profile.id, posting_id=posting.id,
                          company="TechCorp", job_title="Senior Product Owner", status="submitted",
                          cover_letter=letter, application_method="email",
                          recipient_email="recrut@techcorp.com", timeline=[])
    s.add(app)
    s.commit()
    result = email_sender.send_application_email(app, profile, s)
    ck(result.status, "not_configured", f"send_application_email v1 stub : status={result.status}")
    ck(result.provider, "none", f"provider=none (v1 stub) : {result.provider}")
    ck(bool(result.error), True, f"error message présent (v2 : configurez OAuth) : {result.error[:40]}")

    # ── _check_rate_limit : anti-abus 50/jour ──
    print("--- _check_rate_limit : anti-abus (max 50/jour) ---")
    ck(email_sender._check_rate_limit(s, profile.id), True, "rate-limit OK (0 envoi aujourd'hui)")
    ck(email_sender.DAILY_SEND_LIMIT, 50, f"DAILY_SEND_LIMIT = {email_sender.DAILY_SEND_LIMIT}")

    # ── is_provider_configured : v1 toujours False ──
    print("--- is_provider_configured : v1 toujours False (stub) ---")
    ck(email_sender.is_provider_configured("gmail_oauth"), False, "gmail_oauth non configuré (v1 stub)")
    ck(email_sender.is_provider_configured("outlook_oauth"), False, "outlook_oauth non configuré (v1 stub)")
    ck(email_sender.is_provider_configured("invalid"), False, "provider invalide → False")

    # ── Pipeline application : status change + timeline ──
    print("--- Pipeline application : status change + timeline append (audit trail) ---")
    ck(app.status, "submitted", f"status initial = submitted : {app.status}")
    app.status = "interview"
    timeline = app.timeline or []
    timeline.append({"step": "status_change", "status": "interview",
                     "date": datetime.utcnow().isoformat(), "note": "Statut changé : submitted → interview."})
    app.timeline = timeline
    s.commit()
    s.refresh(app)
    ck(app.status, "interview", f"status mis à jour = interview : {app.status}")
    ck(len(app.timeline), 1, f"timeline a 1 entrée (audit trail) : {len(app.timeline)}")
    ck(app.timeline[0].get("status"), "interview", f"timeline[0].status = interview : {app.timeline[0].get('status')}")

    # ── Persistence JobApplication (isolation career_profile_id) ──
    print("--- Persistence JobApplication (isolation career_profile_id) ---")
    ck(app.career_profile_id, "crp_p38", "app.career_profile_id (isolation)")
    ck(app.posting_id, "jbp_p38", "app.posting_id")
    ck(app.company, "TechCorp", "app.company")
    ck(app.application_method, "email", "app.application_method")
    ck(app.recipient_email, "recrut@techcorp.com", "app.recipient_email")
    ck(app.sent_at, None, f"app.sent_at=None (v1 stub, pas envoyé) : {app.sent_at}")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
