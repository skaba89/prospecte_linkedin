#!/usr/bin/env python3
"""test_p40_notifications.py — P40 Notifications intelligentes Career.

Valide :
  - Migration 0023 (table career_notifications + 4 cols prefs sur career_profiles).
  - Modèle CareerNotification (user_id + career_profile_id FK CASCADE — isolation Career/Business).
  - Service career_notifier : notify_new_match (opt-out + seuil + dedup), notify_application_status
    (statuts notables interview/offer/rejected + dedup), list/unread_count/mark_read/mark_all_read.
  - Router : 6 endpoints /api/career/notifications (list, unread-count, read, read-all, prefs GET/PUT).
  - Hooks intégration : matching/search génère new_match ; application PATCH génère application_status.
  - Invariants anti-spam : alert_enabled=False → 0 notif ; dedup → 1 notif par posting/transition.
  - Isolation : user A n'accède pas aux notifs de user B (mark_read 404 sur notif d'autrui).
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


print("=== test_p40_notifications.py — P40 Notifications intelligentes Career ===\n")

# ─── Static checks : migration + modèle + service + router ────────────────────
mig = (ROOT / "backend/alembic/versions/0023_p40_notifications.py").read_text(encoding="utf-8")
models_src = (ROOT / "backend/app/models.py").read_text(encoding="utf-8")
notifier_src = (ROOT / "backend/app/services/career_notifier.py").read_text(encoding="utf-8")
career_router = (ROOT / "backend/app/routers/career.py").read_text(encoding="utf-8")
schemas_src = (ROOT / "backend/app/schemas.py").read_text(encoding="utf-8")

print("--- Migration 0023 ---")
ck("0023_p40_notifications" in mig, True, "revision 0023_p40_notifications")
ck('down_revision = "0022_p38_candidatures"' in mig, True, "down_revision 0022 (chaîne)")
ck("career_notifications" in mig and "op.create_table" in mig, True, "create_table career_notifications")
ck("alert_enabled" in mig and "alert_min_score" in mig and "alert_channels" in mig and "alert_frequency" in mig, True, "4 cols prefs ajoutées à career_profiles")
ck("ondelete=\"CASCADE\"" in mig, True, "FK CASCADE (user_id + career_profile_id)")

print("\n--- Modèle CareerNotification ---")
ck("class CareerNotification(Base)" in models_src, True, "class CareerNotification(Base)")
ck('ForeignKey("users.id", ondelete="CASCADE")' in models_src and "career_notifications" in models_src, True, "user_id FK CASCADE (isolation)")
ck('ForeignKey("career_profiles.id", ondelete="CASCADE")' in models_src, True, "career_profile_id FK CASCADE")
ck("alert_enabled = Column(Boolean" in models_src, True, "CareerProfile.alert_enabled (opt-in)")
ck("alert_min_score = Column(Integer" in models_src, True, "CareerProfile.alert_min_score (seuil P36)")

print("\n--- Service career_notifier (anti-spam invariants) ---")
ck("def notify_new_match(" in notifier_src, True, "notify_new_match(profile, cv_id, posting, score)")
ck("def notify_application_status(" in notifier_src, True, "notify_application_status(profile, app, old, new)")
ck("def _dedup_exists(" in notifier_src, True, "_dedup_exists (anti-spam dedup par payload key)")
ck("if not profile.alert_enabled:" in notifier_src, True, "opt-out explicite (alert_enabled False → None)")
ck("if score < (profile.alert_min_score or 0):" in notifier_src, True, "seuil P36 (score < alert_min_score → None)")
ck('"interview" not in' not in notifier_src and '"interview"' in notifier_src, True, "statuts notables (interview/offer/rejected)")
ck("def unread_count(" in notifier_src and "def mark_read(" in notifier_src and "def mark_all_read(" in notifier_src, True, "helpers list/unread_count/mark_read/mark_all_read")

print("\n--- Schemas ---")
ck("class CareerNotificationOut(BaseModel)" in schemas_src, True, "CareerNotificationOut")
ck("class NotificationPrefsOut(BaseModel)" in schemas_src, True, "NotificationPrefsOut")
ck("class NotificationPrefsUpdate(BaseModel)" in schemas_src, True, "NotificationPrefsUpdate")
ck('pattern="^(daily|weekly|instant)$"' in schemas_src, True, "alert_frequency pattern daily|weekly|instant")

print("\n--- Router (6 endpoints) ---")
ck('@router.get("/notifications"' in career_router, True, "GET /notifications (list)")
ck('@router.get("/notifications/unread-count"' in career_router, True, "GET /notifications/unread-count (badge)")
ck('@router.post("/notifications/{notif_id}/read"' in career_router, True, "POST /notifications/{id}/read")
ck('@router.post("/notifications/read-all"' in career_router, True, "POST /notifications/read-all")
ck('@router.get("/notifications/prefs"' in career_router, True, "GET /notifications/prefs")
ck('@router.put("/notifications/prefs"' in career_router, True, "PUT /notifications/prefs")

print("\n--- Hooks intégration ---")
ck("career_notifier.notify_new_match(db, profile, cv.id, p, scored[p.id])" in career_router, True, "hook matching/search → notify_new_match")
ck("career_notifier.notify_application_status(db, profile, app," in career_router, True, "hook application PATCH → notify_application_status")
ck("alert_enabled" in career_router and "alert_min_score" in career_router, True, "upsert_profile persiste prefs (alert_enabled/min_score)")

# ─── Runtime checks (in-memory sqlite + service layer) ───────────────────────
print("\n--- Runtime (in-memory sqlite + career_notifier service) ---")
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
    from app.services import career_notifier

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()

    # User A + profile (alert_enabled=True, min_score=70) + CV + 2 postings
    s.add(M.User(id="usr_a", email="a@test.com", name="A", password_hash="h"))
    s.add(M.CareerProfile(id="crp_a", user_id="usr_a", target_job_title="Data Engineer",
                          alert_enabled=True, alert_min_score=70, alert_channels=["in_app"], alert_frequency="daily"))
    s.add(M.CV(id="cv_a", career_profile_id="crp_a", title="CV A", source_type="text", raw_text="x",
               parsed_data={"skills": ["python", "sql", "spark"], "experience": [], "certifications": [], "education": [], "heuristic": True},
               is_active=True, locale="fr"))
    s.add(M.JobPosting(id="jp_high", dedup_hash="h1", source_connector="linkedin_jobs", title="Senior DE", company="Corp",
                      location="Paris", description="python sql spark", is_expired=False))
    s.add(M.JobPosting(id="jp_low", dedup_hash="h2", source_connector="linkedin_jobs", title="Junior DE", company="Corp2",
                      location="Lyon", description="java php", is_expired=False))
    s.add(M.JobApplication(id="app_a", career_profile_id="crp_a", company="Corp", job_title="Senior DE",
                          status="submitted", application_method="manual", applied_at=__import__("datetime").datetime.utcnow()))
    # User B (isolation)
    s.add(M.User(id="usr_b", email="b@test.com", name="B", password_hash="h"))
    s.add(M.CareerProfile(id="crp_b", user_id="usr_b", target_job_title="PO",
                          alert_enabled=True, alert_min_score=50, alert_channels=["in_app"], alert_frequency="daily"))
    s.commit()
    prof_a = s.query(M.CareerProfile).filter_by(user_id="usr_a").first()
    prof_b = s.query(M.CareerProfile).filter_by(user_id="usr_b").first()
    jp_high = s.query(M.JobPosting).filter_by(id="jp_high").first()
    jp_low = s.query(M.JobPosting).filter_by(id="jp_low").first()
    app_a = s.query(M.JobApplication).filter_by(id="app_a").first()

    # 1. notify_new_match : score 85 ≥ 70 → notif créée
    n1 = career_notifier.notify_new_match(s, prof_a, "cv_a", jp_high, 85)
    ck(n1 is not None and n1.type == "new_match", True, "notify_new_match score 85 ≥ 70 → notif créée")
    # 2. dedup : même posting 2e fois → None (anti-spam)
    n1b = career_notifier.notify_new_match(s, prof_a, "cv_a", jp_high, 85)
    ck(n1b is None, True, "dedup new_match (même posting → None, anti-spam)")
    # 3. seuil : score 50 < 70 → None
    n2 = career_notifier.notify_new_match(s, prof_a, "cv_a", jp_low, 50)
    ck(n2 is None, True, "seuil P36 (score 50 < 70 → None)")
    # 4. opt-out : prof_b.alert_enabled=False → 0 notif
    prof_b.alert_enabled = False
    n3 = career_notifier.notify_new_match(s, prof_b, None, jp_high, 90)
    ck(n3 is None, True, "opt-out (alert_enabled=False → None)")
    prof_b.alert_enabled = True  # reset

    # 5. notify_application_status : submitted→interview (notable) → notif
    na = career_notifier.notify_application_status(s, prof_a, app_a, "submitted", "interview")
    ck(na is not None and na.type == "application_status", True, "notify_application_status submitted→interview → notif")
    # 6. dedup application_status : même transition 2e fois → None
    na2 = career_notifier.notify_application_status(s, prof_a, app_a, "submitted", "interview")
    ck(na2 is None, True, "dedup application_status (même transition → None)")
    # 7. statut non-notable (submitted→withdrawn) → None
    app_a.status = "withdrawn"
    nw = career_notifier.notify_application_status(s, prof_a, app_a, "submitted", "withdrawn")
    ck(nw is None, True, "statut non-notable (withdrawn → None, anti-bruit)")
    # 8. opt-out application_status
    prof_a.alert_enabled = False
    no = career_notifier.notify_application_status(s, prof_a, app_a, "withdrawn", "offer")
    ck(no is None, True, "opt-out application_status (alert_enabled=False → None)")
    prof_a.alert_enabled = True

    # 9. unread_count : user A a n1 + na = 2 non lues
    ck(career_notifier.unread_count(s, "usr_a"), 2, "unread_count user A = 2 (new_match + application_status)")
    # 10. mark_read : marque n1 → read_at non null
    nr = career_notifier.mark_read(s, "usr_a", n1.id)
    ck(nr is not None and nr.read_at is not None, True, "mark_read n1 → read_at set")
    # 11. mark_read isolation : user B marque notif de user A → None (404 côté router)
    nb = career_notifier.mark_read(s, "usr_b", n1.id)
    ck(nb is None, True, "isolation mark_read (user B sur notif user A → None)")
    # 12. unread_count après mark_read : 1
    ck(career_notifier.unread_count(s, "usr_a"), 1, "unread_count après mark_read = 1")
    # 13. mark_all_read
    cnt = career_notifier.mark_all_read(s, "usr_a")
    ck(cnt, 1, "mark_all_read marque 1 notif restante")
    ck(career_notifier.unread_count(s, "usr_a"), 0, "unread_count après mark_all_read = 0")
    # 14. list_notifications (toutes)
    lst = career_notifier.list_notifications(s, "usr_a", unread_only=False, limit=100)
    ck(len(lst), 2, "list_notifications user A = 2 (historique complet)")
    # 15. isolation list : user B a 0 notif
    ck(len(career_notifier.list_notifications(s, "usr_b", limit=100)), 0, "isolation list (user B = 0 notif)")
    s.close()

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
