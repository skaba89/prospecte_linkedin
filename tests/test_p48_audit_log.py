#!/usr/bin/env python3
"""test_p48_audit_log.py — P48 Audit log enterprise : IP/UA capture + instrumentation sso/billing + READ endpoint.

Coverage (TestClient, AUCUN réseau externe en CI) :
  A. IP + User-Agent capturés (context vars middleware → log_audit) :
    1. Mutation (member.invite) → audit entry a ip + user_agent non vides.
    2. X-Forwarded-For header respecté (premier IP extrait).
  B. Instrumentation sso (P48 — était non-audité) :
    3. PUT /api/tenants/{tid}/sso (create) → audit entry action="sso.config.upsert", before=None, after=domains.
    4. PUT (update) → before=ancienne config, after=nouvelle.
    5. DELETE → audit entry action="sso.config.delete", detail.deleted présent.
    6. client_secret JAMAIS dans l'audit detail (sécurité — before/after redactés).
  C. Instrumentation billing (P48 — était non-audité) :
    7. PATCH /billing (plan change) → audit action="billing.plan.change", before/after plan.
    8. PATCH /billing/currency → audit action="billing.currency.change", before/after currency.
  D. READ endpoint (existant GET /api/tenants/{tid}/audit — vérifie qu'il expose ip/user_agent P48) :
    9. GET → 200, liste contient les entries, ip + user_agent présents dans la réponse.
    10. Filtre action=sso.config.upsert → ne retourne que les entries SSO upsert.
    11. Admin (pas workspace.manage) → GET 403.
    12. Outsider → GET 403 (get_tenant refuse).
  E. Immutabilité :
    13. Aucun endpoint POST/PUT/PATCH/DELETE sur /audit (seule log_audit crée des lignes).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p48_test.db python tests/test_p48_audit_log.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p48_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")

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

    for tbl in [M.AuditLog, M.EnterpriseSSO, M.Subscription, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p48_audit_log.py — P48 Audit log enterprise ===\n")

    # Seed : owner + admin + outsider + tenant (plan Free)
    owner = M.User(id="usr_o3", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_a3", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    out = M.User(id="usr_x3", email="out@other.io", name="Outsider", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_c3", name="Corp Audit", plan="Free", owner_id="usr_o3", sso_enabled=False, currency="EUR")
    member_admin = M.Member(id="mem_a3", tenant_id="tnt_c3", user_id="usr_a3",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, out, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o3')}", "X-Forwarded-For": "203.0.113.42, 10.0.0.1",
               "User-Agent": "TestClient-P48/1.0"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_a3')}"}
    H_OUT = {"Authorization": f"Bearer {create_access_token('usr_x3')}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. IP + User-Agent capturés (via member.invite — déjà instrumenté)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. IP + User-Agent capturés (member.invite) ---")
    r = client.post("/api/tenants/tnt_c3/members/invite",
                    json={"email": "new1@corp.io", "role": "Editor"}, headers=H_OWNER)
    ck(r.status_code == 201, f"member.invite → 201 (got {r.status_code})")
    aud = test_db.query(M.AuditLog).filter_by(action="member.invite").first()
    ck(aud is not None, "audit entry member.invite créée")
    ck(aud.ip == "203.0.113.42", f"ip = 203.0.113.42 (X-Forwarded-For premier) (got '{aud.ip}')")
    ck(aud.user_agent == "TestClient-P48/1.0", f"user_agent capturé (got '{aud.user_agent}')")

    # ══════════════════════════════════════════════════════════════════════
    # B. Instrumentation sso (P48)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Instrumentation sso (upsert + delete) ---")
    cfg = {"client_id": "oidc_c1", "client_secret": "SECRET_VALUE_123",
           "issuer_url": "https://idp/v2.0", "authorize_url": "https://idp/auth",
           "token_url": "https://idp/token", "userinfo_url": "https://idp/userinfo",
           "scopes": "openid email", "domains": ["corp.io"], "enabled": True}
    r = client.put("/api/tenants/tnt_c3/sso", json=cfg, headers=H_OWNER)
    ck(r.status_code == 200, f"PUT sso create → 200 (got {r.status_code})")
    aud = test_db.query(M.AuditLog).filter_by(action="sso.config.upsert").order_by(M.AuditLog.created_at.desc()).first()
    ck(aud is not None, "audit entry sso.config.upsert créée")
    ck(aud.target_type == "sso_config", f"target_type=sso_config (got {aud.target_type})")
    ck(aud.detail.get("before") is None, "before=None (create)")
    ck(aud.detail.get("after", {}).get("client_id") == "oidc_c1", "after.client_id capturé")
    ck("SECRET_VALUE_123" not in str(aud.detail), "client_secret JAMAIS dans audit detail (sécurité)")

    # 4. PUT update → before=ancienne config
    cfg2 = {**cfg, "client_id": "oidc_c2", "domains": ["corp.io", "sub.corp.io"]}
    r = client.put("/api/tenants/tnt_c3/sso", json=cfg2, headers=H_OWNER)
    ck(r.status_code == 200, f"PUT sso update → 200 (got {r.status_code})")
    aud = test_db.query(M.AuditLog).filter_by(action="sso.config.upsert").order_by(M.AuditLog.created_at.desc()).first()
    ck(aud.detail.get("before", {}).get("client_id") == "oidc_c1", "before.client_id=oidc_c1 (update)")
    ck(aud.detail.get("after", {}).get("client_id") == "oidc_c2", "after.client_id=oidc_c2 (update)")
    ck("SECRET_VALUE_123" not in str(aud.detail), "client_secret JAMAIS dans audit detail (update)")

    # 5. DELETE → audit sso.config.delete
    r = client.delete("/api/tenants/tnt_c3/sso", headers=H_OWNER)
    ck(r.status_code == 200, f"DELETE sso → 200 (got {r.status_code})")
    aud = test_db.query(M.AuditLog).filter_by(action="sso.config.delete").first()
    ck(aud is not None, "audit entry sso.config.delete créée")
    ck("deleted" in aud.detail, "detail.deleted présent (config supprimée capturée)")
    ck(aud.detail["deleted"].get("client_id") == "oidc_c2", "deleted.client_id capturé")

    # ══════════════════════════════════════════════════════════════════════
    # C. Instrumentation billing (P48)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Instrumentation billing (plan + currency) ---")
    # 7. PATCH plan change (Free → Pro, si Pro existe dans PLANS)
    from app.services import billing_service
    plans = list(billing_service.PLANS.keys())
    target_plan = "Pro" if "Pro" in plans else (plans[1] if len(plans) > 1 else plans[0])
    r = client.patch("/api/tenants/tnt_c3/billing", json={"plan": target_plan}, headers=H_OWNER)
    ck(r.status_code == 200, f"PATCH plan → 200 (got {r.status_code}: {r.text[:100]})")
    aud = test_db.query(M.AuditLog).filter_by(action="billing.plan.change").first()
    ck(aud is not None, "audit entry billing.plan.change créée")
    ck(aud.detail.get("before", {}).get("plan") == "Free", "before.plan=Free")
    ck(aud.detail.get("after", {}).get("plan") == target_plan, f"after.plan={target_plan}")

    # 8. PATCH currency change
    r = client.patch("/api/tenants/tnt_c3/billing/currency", json={"currency": "USD"}, headers=H_OWNER)
    ck(r.status_code == 200, f"PATCH currency → 200 (got {r.status_code})")
    aud = test_db.query(M.AuditLog).filter_by(action="billing.currency.change").first()
    ck(aud is not None, "audit entry billing.currency.change créée")
    ck(aud.detail.get("before", {}).get("currency") == "EUR", "before.currency=EUR")
    ck(aud.detail.get("after", {}).get("currency") == "USD", "after.currency=USD")

    # ══════════════════════════════════════════════════════════════════════
    # D. READ endpoint (existant GET /api/tenants/{tid}/audit)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. READ endpoint GET /api/tenants/{tid}/audit ---")
    r = client.get("/api/tenants/tnt_c3/audit", headers=H_OWNER)
    ck(r.status_code == 200, f"GET audit → 200 (got {r.status_code})")
    entries = r.json()
    ck(len(entries) >= 5, f"≥5 entries (invite + 2 sso upsert + sso delete + plan + currency) (got {len(entries)})")
    # 9. ip + user_agent présents dans la réponse (P48)
    has_ip = any(e.get("ip") for e in entries)
    has_ua = any(e.get("user_agent") for e in entries)
    ck(has_ip, "ip présent dans au moins 1 entry de la réponse")
    ck(has_ua, "user_agent présent dans au moins 1 entry de la réponse")
    # Tri descendant (plus récent d'abord)
    createds = [e["created_at"] for e in entries]
    ck(createds == sorted(createds, reverse=True), "tri descendant (plus récent d'abord)")

    # 10. Filtre action=sso.config.upsert
    r = client.get("/api/tenants/tnt_c3/audit?action=sso.config", headers=H_OWNER)
    ck(r.status_code == 200, f"GET audit?action=sso.config → 200 (got {r.status_code})")
    filtered = r.json()
    ck(all("sso.config" in e["action"] for e in filtered), f"filtre action prefix sso.config (got {len(filtered)} entries)")
    ck(len(filtered) == 3, f"3 entries sso.config (2 upsert + 1 delete) (got {len(filtered)})")

    # 11. Admin (pas workspace.manage) → 403
    r = client.get("/api/tenants/tnt_c3/audit", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin GET audit → 403 (got {r.status_code})")

    # 12. Outsider → 403
    r = client.get("/api/tenants/tnt_c3/audit", headers=H_OUT)
    ck(r.status_code == 403, f"Outsider GET audit → 403 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # E. Immutabilité (aucun endpoint write sur /audit)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- E. Immutabilité (aucun endpoint write sur /audit) ---")
    methods_405 = 0
    for method in ["post", "put", "patch", "delete"]:
        r = getattr(client, method)("/api/tenants/tnt_c3/audit", headers=H_OWNER)
        if r.status_code in (405, 404):
            methods_405 += 1
    ck(methods_405 == 4, f"aucun endpoint write sur /audit (POST/PUT/PATCH/DELETE → 405/404) (got {methods_405}/4)")

    # 13. stats endpoint (existant) fonctionne toujours
    r = client.get("/api/tenants/tnt_c3/audit/stats", headers=H_OWNER)
    ck(r.status_code == 200, f"GET audit/stats → 200 (got {r.status_code})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
