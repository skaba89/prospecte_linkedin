#!/usr/bin/env python3
"""test_p65_gdpr_p0_effective.py — P65 GDPR effectif (audit V1.0 G1/G2/G3).

3 blocages P0 GDPR corrigés (fausse conformité CNIL avant P65) :
  G1 : Consent withdrawal NE STOPPAIT AUCUN traitement (Art. 7.3 violé).
  G2 : gdpr_export n'exportait pas OAuth/CV/Consent/Notification (Art. 20 incomplet).
  G3 : gdpr_erase laissait PII dans AuditLog.detail + OAuth tokens + CVs (Art. 17 violé).

Coverage :
  A. Consent gating effectif (P0-G1) :
    1. has_active_consent (no record) → True (default allow, comportement préservé).
    2. has_active_consent (record actif) → True.
    3. has_active_consent (record retiré) → False (retrait effectif).
    4. send_email_to_contact : owner sans retrait → envoi OK (status sent/dry_run).
    5. send_email_to_contact : owner AVEC retrait marketing → SKIPPED (status=skipped, "Consent marketing retiré").
    6. gdpr_consent_withdraw purpose=marketing → pause les SequenceEnrollment actifs du user.
    7. audit entry gdpr.consent.withdraw avec sequences_paused count.
  B. Export complétude (P0-G2) :
    8. gdpr_export contient email_integrations (provider/email/scopes, pas les tokens).
    9. gdpr_export contient cvs (raw_text + parsed_data).
    10. gdpr_export contient consents (purpose/granted_at/withdrawn_at).
    11. gdpr_export contient notifications (recipient_email/subject/body_text).
    12. gdpr_export counts inclut email_integrations/cvs/consents/notifications.
  C. Erase cascade (P0-G3) :
    13. gdpr_erase supprime UserEmailIntegration (OAuth tokens purgés).
    14. gdpr_erase scrub CV.raw_text + CV.parsed_data (vide/None).
    15. gdpr_erase scrub AuditLog.detail (email original remplacé — plus traçable).
    16. gdpr_erase response inclut audit_detail_scrubbed + oauth_integrations_deleted + cvs_scrubbed.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p65_test.db python tests/test_p65_gdpr_p0_effective.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p65_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))
os.environ.setdefault("REVPULSE_RATE_LIMIT_ENABLED", "false")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def main():
    from app import models as M
    from app.database import Base, get_db
    from app.security import create_access_token, encrypt_token
    from app.services.consent import has_active_consent
    from app.routers.notifications import send_email_to_contact
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    test_db = Sess()

    def _override_db():
        yield test_db

    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    for tbl in [M.Notification, M.CV, M.JobApplication, M.CareerProfile, M.SequenceEnrollment,
                M.EmailSequence, M.UserEmailIntegration, M.ConsentRecord, M.AuditLog,
                M.Contact, M.SuppressionList, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p65_gdpr_p0_effective.py — P65 GDPR effectif (G1/G2/G3) ===\n")

    # Setup : owner (executor) + victim (user à effacer) + tenant
    owner = M.User(id="usr_exec", email="exec@corp.io", name="Exec Owner", password_hash="h", locale="fr")
    victim = M.User(id="usr_victim", email="victim@corp.io", name="Victor Victim", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_p65", name="Corp P65", plan="Enterprise", owner_id="usr_exec",
                      sso_enabled=False, retention_days=2555)
    mem_owner = M.Member(id="mem_exec", tenant_id="tnt_p65", user_id="usr_exec",
                         email="exec@corp.io", name="Exec Owner", role="Owner", status="active")
    mem_victim = M.Member(id="mem_victim", tenant_id="tnt_p65", user_id="usr_victim",
                          email="victim@corp.io", name="Victor Victim", role="Contributor", status="active")
    test_db.add_all([owner, victim, tenant, mem_owner, mem_victim]); test_db.commit()
    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_exec')}"}
    H_VICTIM = {"Authorization": f"Bearer {create_access_token('usr_victim')}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Consent gating effectif (P0-G1)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Consent gating effectif (P0-G1) ---")
    # 1. no record → True (default allow)
    ck(has_active_consent(test_db, "tnt_p65", "usr_victim", "marketing") is True,
       "has_active_consent (no record) → True (default allow)")
    # 2. grant consent → True
    r = client.post("/api/tenants/tnt_p65/gdpr/consent", headers=H_VICTIM, json={"purpose": "marketing"})
    ck(r.status_code == 201, f"POST /consent marketing → 201 (got {r.status_code})")
    ck(has_active_consent(test_db, "tnt_p65", "usr_victim", "marketing") is True,
       "has_active_consent (record actif) → True")
    # 3. withdraw → False (retrait effectif)
    r = client.delete("/api/tenants/tnt_p65/gdpr/consent?purpose=marketing", headers=H_VICTIM)
    ck(r.status_code == 200, f"DELETE /consent marketing → 200 (got {r.status_code})")
    ck(has_active_consent(test_db, "tnt_p65", "usr_victim", "marketing") is False,
       "has_active_consent (record retiré) → False (retrait effectif Art. 7.3)")

    # 4-5. send_email_to_contact : owner sans retrait → envoi ; owner AVEC retrait → skipped
    # Setup : victim owns a contact ; victim withdrew marketing consent (above)
    contact = M.Contact(id="ctc_v", tenant_id="tnt_p65", owner_id="usr_victim",
                        first_name="Prosp", last_name="Ect", email="prosp@example.com")
    test_db.add(contact); test_db.commit()
    # victim has withdrawn marketing → send_email should SKIP
    result = send_email_to_contact(test_db, "tnt_p65", contact, "tpl_test", "usr_victim",
                                   "Victor Victim", "Corp P65", "Hello")
    ck(result.status == "skipped", f"send_email owner consent retiré → skipped (got {result.status})")
    ck("Consent marketing" in (result.detail or ""), "send_email skipped detail mentionne Consent marketing (Art. 7.3)")
    # Re-grant consent → send_email should NOT skip on consent
    client.post("/api/tenants/tnt_p65/gdpr/consent", headers=H_VICTIM, json={"purpose": "marketing"})
    result2 = send_email_to_contact(test_db, "tnt_p65", contact, "tpl_test", "usr_victim",
                                    "Victor Victim", "Corp P65", "Hello")
    ck(result2.status != "skipped" or "Consent marketing" not in (result2.detail or ""),
       f"send_email owner consent réactivé → pas de skip consent (got {result2.status})")

    # 6-7. consent_withdraw pauses sequences
    # Setup : victim creates a sequence + enrolls a contact
    seq = M.EmailSequence(id="seq_v", tenant_id="tnt_p65", name="Drip P65", created_by="usr_victim", status="active")
    enr = M.SequenceEnrollment(id="enr_v", sequence_id="seq_v", contact_id="ctc_v",
                               current_step_order=1, status="active", enrolled_by_user_id="usr_victim")
    test_db.add_all([seq, enr]); test_db.commit()
    # Withdraw marketing → enrollment paused
    r = client.delete("/api/tenants/tnt_p65/gdpr/consent?purpose=marketing", headers=H_VICTIM)
    ck(r.status_code == 200, f"DELETE /consent marketing → 200 (got {r.status_code})")
    ck(r.json().get("sequences_paused") == 1, f"withdraw → sequences_paused=1 (got {r.json().get('sequences_paused')})")
    test_db.expire_all()
    enr_db = test_db.query(M.SequenceEnrollment).filter_by(id="enr_v").first()
    ck(enr_db.status == "paused", f"SequenceEnrollment pausé après withdraw marketing (got {enr_db.status})")
    # audit entry with sequences_paused (le 2e withdraw — celui après création de la séquence)
    withdraws = test_db.query(M.AuditLog).filter_by(action="gdpr.consent.withdraw").all()
    ck(any(w.detail.get("sequences_paused") == 1 for w in withdraws),
       "audit gdpr.consent.withdraw avec sequences_paused=1 (le 2e withdraw)")

    # ══════════════════════════════════════════════════════════════════════
    # B. Export complétude (P0-G2)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Export complétude (P0-G2) ---")
    # Setup : victim has OAuth integration + CV + consent + notification
    uei = M.UserEmailIntegration(id="uei_v", user_id="usr_victim", provider="gmail_oauth",
                                 email="victim@gmail.com", scopes="gmail.send")
    career = M.CareerProfile(id="cp_v", user_id="usr_victim", target_job_title="Data Engineer")
    cv = M.CV(id="cv_v", career_profile_id="cp_v", title="CV Data Engineer",
              source_type="text",
              raw_text="Victor Victim - Data Engineer - Python/SQL", parsed_data={"skills": ["Python"]})
    notif = M.Notification(id="n_v", tenant_id="tnt_p65", user_id="usr_victim", type="email",
                           channel="email", recipient_email="victim@corp.io",
                           subject="Test", body_text="Body", status="sent")
    test_db.add_all([uei, career, cv, notif]); test_db.commit()
    # Re-grant consent so export shows it as active
    client.post("/api/tenants/tnt_p65/gdpr/consent", headers=H_VICTIM, json={"purpose": "analytics"})

    r = client.get("/api/tenants/tnt_p65/gdpr/export?user_id=usr_victim", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /gdpr/export → 200 (got {r.status_code})")
    data = r.json()
    # 8. email_integrations
    ck("email_integrations" in data and len(data["email_integrations"]) == 1, "export contient email_integrations (1)")
    uei_exp = data["email_integrations"][0]
    ck(uei_exp["provider"] == "gmail_oauth" and uei_exp["email"] == "victim@gmail.com", "email_integrations provider+email")
    ck("refresh_token_encrypted" not in str(uei_exp), "email_integrations N'exporte PAS les tokens chiffrés")
    # 9. cvs
    ck("cvs" in data and len(data["cvs"]) == 1, "export contient cvs (1)")
    ck(data["cvs"][0]["raw_text"] == "Victor Victim - Data Engineer - Python/SQL", "cvs raw_text exporté (PII)")
    ck(data["cvs"][0]["parsed_data"] == {"skills": ["Python"]}, "cvs parsed_data exporté")
    # 10. consents
    ck("consents" in data and len(data["consents"]) >= 1, "export contient consents (>=1)")
    # 11. notifications (>=1 — send_email_to_contact en section A en crée aussi)
    ck("notifications" in data and len(data["notifications"]) >= 1, "export contient notifications (>=1)")
    n_v_exp = [n for n in data["notifications"] if n.get("id") == "n_v"]
    ck(len(n_v_exp) == 1, "export contient la notification n_v (créée en section B)")
    ck(n_v_exp[0]["recipient_email"] == "victim@corp.io", "notifications recipient_email exporté")
    # 12. counts
    ck(data["counts"].get("email_integrations") == 1, "counts.email_integrations=1")
    ck(data["counts"].get("cvs") == 1, "counts.cvs=1")
    ck(data["counts"].get("notifications") >= 1, "counts.notifications>=1")

    # ══════════════════════════════════════════════════════════════════════
    # C. Erase cascade (P0-G3)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Erase cascade (P0-G3) ---")
    # AuditLog with victim's email in detail (simule crm.email_contact par owner)
    log_audit_email = M.AuditLog(id="al_v", tenant_id="tnt_p65", actor_user_id="usr_exec",
                                 actor_name="Exec Owner", action="crm.email_contact",
                                 target_type="contact", target_id="ctc_v",
                                 detail={"template": "tpl_test", "status": "sent", "email": "victim@corp.io"})
    test_db.add(log_audit_email); test_db.commit()

    r = client.delete("/api/tenants/tnt_p65/gdpr/erase?user_id=usr_victim", headers=H_OWNER)
    ck(r.status_code == 200, f"DELETE /gdpr/erase → 200 (got {r.status_code})")
    eres = r.json()
    # 13. OAuth integrations deleted
    ck(eres.get("oauth_integrations_deleted") == 1, f"erase oauth_integrations_deleted=1 (got {eres.get('oauth_integrations_deleted')})")
    test_db.expire_all()
    ck(test_db.query(M.UserEmailIntegration).filter_by(user_id="usr_victim").count() == 0, "UserEmailIntegration hard-deleted (OAuth révoqué)")
    # 14. CV scrubbed
    ck(eres.get("cvs_scrubbed") == 1, f"erase cvs_scrubbed=1 (got {eres.get('cvs_scrubbed')})")
    cv_db = test_db.query(M.CV).filter_by(id="cv_v").first()
    ck(cv_db.raw_text == "", "CV.raw_text scrubbed (vide)")
    ck(cv_db.parsed_data is None, "CV.parsed_data scrubbed (None)")
    # 15. AuditLog.detail scrubbed (email original remplacé)
    ck(eres.get("audit_detail_scrubbed") >= 1, f"erase audit_detail_scrubbed>=1 (got {eres.get('audit_detail_scrubbed')})")
    al_db = test_db.query(M.AuditLog).filter_by(id="al_v").first()
    ck("victim@corp.io" not in str(al_db.detail), "AuditLog.detail ne contient plus l'email original (scrubbed)")
    ck(al_db.detail.get("email") != "victim@corp.io", "AuditLog.detail.email remplacé (anti-traçabilité)")
    # 16. response fields
    ck("oauth_integrations_deleted" in eres and "cvs_scrubbed" in eres and "audit_detail_scrubbed" in eres,
       "erase response inclut oauth/cvs/detail counts")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
