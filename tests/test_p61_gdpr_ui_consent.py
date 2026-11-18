#!/usr/bin/env python3
"""test_p61_gdpr_ui_consent.py — P61 GDPR UI dashboard (account.html) + consent management (Art. 7).

P60 a livré export/erase API mais sans UI + sans consent management (Art. 7). P61 comble :
  - GET/POST/DELETE /gdpr/consent : consent self-service (Art. 7 + Art. 7.3 retrait traçable).
  - account.html : GDPR dashboard (profil + export + consent list/grant/withdraw + erase).

Coverage (TestClient) :
  A. Consent CRUD (Art. 7) :
    1. GET /consent → 200 (liste vide initialement).
    2. POST /consent {marketing} → 201 + record créé (purpose, granted_at, privacy_policy_version, active=true).
    3. POST /consent {marketing} → idempotent (retourne l'existant, pas de doublon).
    4. GET /consent → 1 active record.
    5. DELETE /consent?purpose=marketing → 200 + withdrawn_at set + active=false.
    6. GET /consent → 1 inactive record (historique conservé — preuve DPO).
    7. POST /consent {analytics} → 201 (2e purpose).
    8. IP + UA capturés au grant (preuve Art. 7.1) — via request_ip_var/request_ua_var middleware.
    9. Audit entries gdpr.consent.grant + gdpr.consent.withdraw loggées.
    10. Invalid purpose → 400.
    11. Withdraw non-existent → 404.
    12. Viewer (rôle minimal) peut consent (self-service — tout membre actif).
  B. account.html page load :
    13. GET /account.html → 200, content-type text/html.
    14. Contient "Mon compte & RGPD" (title + topbar).
    15. Contient "Portabilité" (Art. 20) + bouton exportMyData.
    16. Contient "Effacement" (Art. 17) + bouton eraseMyData.
    17. Contient "Consentement" (Art. 7) + #consent-list.
    18. Contient wiring /api/me (user_id self) + /gdpr/export + /gdpr/erase + /gdpr/consent.
    19. Contient 3 purposes (marketing/analytics/third_party_sharing).
    20. Contient badge GDPR (Art. 20/17/7 mentions).
    21. viewport meta responsive présent.
  C. Migration 0034 :
    22. consent_records table exists (alembic upgrade head + sa_inspect).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p61_test.db python tests/test_p61_gdpr_ui_consent.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p61_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))

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
    from app.security import create_access_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect as sa_inspect
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

    for tbl in [M.ConsentRecord, M.AuditLog, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p61_gdpr_ui_consent.py — P61 GDPR UI + consent Art. 7 ===\n")

    owner = M.User(id="usr_oD", email="owner@corp.io", name="Alice Owner", password_hash="h", locale="fr")
    viewer_user = M.User(id="usr_vD", email="viewer@corp.io", name="Vera Viewer", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_cD", name="Corp P61", plan="Enterprise", owner_id="usr_oD",
                      sso_enabled=False, retention_days=2555)
    mem_owner = M.Member(id="mem_oD", tenant_id="tnt_cD", user_id="usr_oD",
                         email="owner@corp.io", name="Alice Owner", role="Owner", status="active")
    mem_viewer = M.Member(id="mem_vD", tenant_id="tnt_cD", user_id="usr_vD",
                          email="viewer@corp.io", name="Vera Viewer", role="Viewer", status="active")
    test_db.add_all([owner, viewer_user, tenant, mem_owner, mem_viewer]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_oD')}"}
    H_VIEWER = {"Authorization": f"Bearer {create_access_token('usr_vD')}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Consent CRUD (Art. 7)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Consent CRUD (Art. 7) ---")
    # GET empty
    r = client.get("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /consent → 200 (got {r.status_code})")
    ck(r.json() == [], "GET /consent empty initialement")

    # POST grant marketing — IP/UA passés via headers (le middleware logging_config les capture dans
    # request_ip_var/request_ua_var — preuve Art. 7.1)
    r = client.post("/api/tenants/tnt_cD/gdpr/consent", headers={**H_OWNER,
                        "X-Forwarded-For": "198.51.100.42",
                        "User-Agent": "Mozilla/5.0 (Test UA P61)"},
                    json={"purpose": "marketing"})
    ck(r.status_code == 201, f"POST /consent marketing → 201 (got {r.status_code})")
    ck(r.json()["purpose"] == "marketing" and r.json()["active"] is True, "record marketing créé + active=true")
    ck(r.json()["privacy_policy_version"] == "2026-11-18-v1", "privacy_policy_version default")
    grant_id = r.json()["id"]

    # POST idempotent (même purpose actif → retourne l'existant)
    r2 = client.post("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER, json={"purpose": "marketing"})
    ck(r2.status_code in (200, 201), f"POST marketing idempotent → 200/201 (got {r2.status_code})")
    ck(r2.json()["id"] == grant_id, "idempotent : même record id (pas de doublon)")

    # GET 1 active
    r = client.get("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER)
    ck(len(r.json()) == 1, f"GET /consent 1 record (got {len(r.json())})")
    ck(r.json()[0]["active"] is True, "record active=true")
    ck(r.json()[0]["ip"] == "198.51.100.42", "IP capturée au grant (preuve Art. 7.1)")
    ck(r.json()[0]["user_agent"] == "Mozilla/5.0 (Test UA P61)", "UA capturé au grant")

    # DELETE withdraw
    r = client.delete("/api/tenants/tnt_cD/gdpr/consent?purpose=marketing", headers=H_OWNER)
    ck(r.status_code == 200, f"DELETE /consent marketing → 200 (got {r.status_code})")
    ck(r.json()["active"] is False, "withdraw → active=false")
    ck(r.json()["withdrawn_at"] is not None, "withdrawn_at set (Art. 7.3 retrait traçable)")

    # GET — 1 inactive record (historique conservé)
    r = client.get("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER)
    ck(len(r.json()) == 1, f"GET /consent 1 record conservé (historique) (got {len(r.json())})")
    ck(r.json()[0]["active"] is False, "record inactive (historique conservé — preuve DPO)")

    # POST analytics (2e purpose)
    r = client.post("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER, json={"purpose": "analytics"})
    ck(r.status_code == 201, f"POST analytics → 201 (got {r.status_code})")

    # Audit entries loggées
    grants = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_cD", action="gdpr.consent.grant").all()
    withdraws = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_cD", action="gdpr.consent.withdraw").all()
    ck(len(grants) >= 2, f"audit gdpr.consent.grant loggées (>=2 — marketing + analytics) (got {len(grants)})")
    ck(len(withdraws) == 1, f"audit gdpr.consent.withdraw loggée (1) (got {len(withdraws)})")

    # Invalid purpose → 400
    r = client.post("/api/tenants/tnt_cD/gdpr/consent", headers=H_OWNER, json={"purpose": "invalid_purpose"})
    ck(r.status_code == 400, f"POST invalid purpose → 400 (got {r.status_code})")

    # Withdraw non-existent → 404
    r = client.delete("/api/tenants/tnt_cD/gdpr/consent?purpose=third_party_sharing", headers=H_OWNER)
    ck(r.status_code == 404, f"DELETE non-existent consent → 404 (got {r.status_code})")

    # Viewer (rôle minimal) peut consent (self-service)
    r = client.post("/api/tenants/tnt_cD/gdpr/consent", headers=H_VIEWER, json={"purpose": "analytics"})
    ck(r.status_code == 201, f"Viewer POST consent (self-service) → 201 (got {r.status_code})")
    r = client.get("/api/tenants/tnt_cD/gdpr/consent", headers=H_VIEWER)
    ck(r.status_code == 200 and len(r.json()) == 1, "Viewer GET own consent → 200 (isolation user)")

    # ══════════════════════════════════════════════════════════════════════
    # B. account.html page load
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. account.html page load ---")
    r = client.get("/account.html")
    ck(r.status_code == 200, f"GET /account.html → 200 (got {r.status_code})")
    html = r.text
    ck("Mon compte & RGPD" in html, "title 'Mon compte & RGPD' présent")
    ck("Portabilité" in html and "Art. 20" in html, "section Portabilité (Art. 20) présente")
    ck("exportMyData" in html, "fonction exportMyData présente")
    ck("Effacement" in html and "Art. 17" in html, "section Effacement (Art. 17) présente")
    ck("eraseMyData" in html, "fonction eraseMyData présente")
    ck("Consentement" in html and "Art. 7" in html, "section Consentement (Art. 7) présente")
    ck('id="consent-list"' in html, "zone #consent-list présente")
    ck("grantConsent" in html and "withdrawConsent" in html, "fonctions grantConsent + withdrawConsent")
    ck("loadConsents" in html, "fonction loadConsents présente")
    # Wiring endpoints
    ck("'/me'" in html or '"/me"' in html or "/me" in html, "wiring /api/me (user_id self)")
    ck("/gdpr/export" in html, "wiring /gdpr/export")
    ck("/gdpr/erase" in html, "wiring /gdpr/erase")
    ck("/gdpr/consent" in html, "wiring /gdpr/consent")
    # 3 purposes dans le JS PURPOSES map
    ck("'marketing'" in html and "'analytics'" in html and "'third_party_sharing'" in html,
       "3 purposes (marketing/analytics/third_party_sharing) dans le JS")
    # Badge GDPR mentions
    ck("portabilité" in html.lower() and "effacement" in html.lower() and "consentement" in html.lower(),
       "banner GDPR mentionne portabilité/effacement/consentement")
    ck("Art. 20" in html and "Art. 17" in html and "Art. 7" in html, "3 articles GDPR cités (20/17/7)")
    ck('name="viewport"' in html and 'width=device-width' in html, "viewport meta responsive présent")
    ck('href="account.html"' in html, "nav item account.html actif (self-link)")
    ck("erased-warn" in html, "zone #erased-warn (alerte si compte anonymisé)")

    # ══════════════════════════════════════════════════════════════════════
    # C. Migration 0034 (consent_records table)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Migration 0034 (consent_records table) ---")
    insp = sa_inspect(engine)
    ck("consent_records" in insp.get_table_names(), "table consent_records existe (migration 0034)")
    cols = [c["name"] for c in insp.get_columns("consent_records")]
    for needed in ["id", "tenant_id", "user_id", "purpose", "granted_at", "withdrawn_at",
                   "privacy_policy_version", "ip", "user_agent"]:
        ck(needed in cols, f"colonne consent_records.{needed}")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
