#!/usr/bin/env python3
"""test_p46_admin_sso_crud.py — P46 Admin SSO CRUD + lookup by email.

Coverage (TestClient, AUCUN réseau IdP externe en CI) :
  A. Admin SSO CRUD (Owner-only via workspace.manage) :
    1. GET /api/tenants/{tid}/sso : 404 si aucun SSO configuré.
    2. PUT /api/tenants/{tid}/sso : upsert (create) — client_secret chiffré Fernet, domains nettoyés, tenant.sso_enabled sync.
    3. GET après PUT : config retournée, has_secret=True, client_secret JAMAIS dans la réponse.
    4. PUT (update) : modifie client_id + domains, has_secret reste True.
    5. DELETE : supprime config + tenant.sso_enabled=False. GET → 404 après.
  B. Permission gate :
    6. Admin role (pas workspace.manage) → PUT 403.
    7. Non-member → GET 403 (get_tenant refuse).
    8. Owner → PUT 200 (workspace.manage OK).
  C. SSO lookup by email (public) :
    9. POST /api/auth/sso/lookup {email} : retourne initiate_url si domaine match.
    10. Lookup email domaine non configuré → 404.
    11. Lookup email invalide → 400.
    12. Lookup après DELETE → 404 (plus de config).
  D. Sécurité :
    13. client_secret JAMAIS dans GET response (grep "secret" dans JSON).
    14. domains nettoyés (lowercase, pas de @).

Sécurité P46 : client_secret chiffré Fernet + JAMAIS retourné (has_secret seulement). Permission
workspace.manage (Owner-only). Lookup public (pas d'auth) mais ne leak que tenant_id + initiate_url.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p46_test.db python tests/test_p46_admin_sso_crud.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p46_test.db")
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
    from app.security import create_access_token, encrypt_token
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

    # Clean
    for tbl in [M.EnterpriseSSO, M.OAuthState, M.Subscription, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p46_admin_sso_crud.py — P46 Admin SSO CRUD + lookup by email ===\n")

    # Seed : owner + tenant + admin member
    owner = M.User(id="usr_owner", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_admin", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    outsider = M.User(id="usr_out", email="out@other.io", name="Outsider", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_corp", name="Corp SSO", plan="Enterprise", owner_id="usr_owner", sso_enabled=False)
    # Admin member (role Admin — pas workspace.manage)
    member_admin = M.Member(id="mem_admin", tenant_id="tnt_corp", user_id="usr_admin",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, outsider, tenant, member_admin])
    test_db.commit()

    owner_tok = create_access_token("usr_owner")
    admin_tok = create_access_token("usr_admin")
    out_tok = create_access_token("usr_out")
    H_OWNER = {"Authorization": f"Bearer {owner_tok}"}
    H_ADMIN = {"Authorization": f"Bearer {admin_tok}"}
    H_OUT = {"Authorization": f"Bearer {out_tok}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Admin SSO CRUD (Owner)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Admin SSO CRUD (Owner) ---")

    # 1. GET : 404 si aucun SSO
    r = client.get("/api/tenants/tnt_corp/sso", headers=H_OWNER)
    ck(r.status_code == 404, f"GET no config → 404 (got {r.status_code})")

    # 2. PUT : upsert (create)
    cfg = {
        "client_id": "oidc_client_123", "client_secret": "oidc_secret_xyz",
        "issuer_url": "https://login.microsoftonline.com/corp/v2.0",
        "authorize_url": "https://login.microsoftonline.com/corp/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/corp/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scopes": "openid profile email", "domains": ["Corp.IO", "@revpulse.io"],
        "enabled": True,
    }
    r = client.put("/api/tenants/tnt_corp/sso", json=cfg, headers=H_OWNER)
    ck(r.status_code == 200, f"PUT create → 200 (got {r.status_code}: {r.text[:120]})")
    body = r.json()
    ck(body["client_id"] == "oidc_client_123", "client_id retourné")
    ck(body["has_secret"] is True, "has_secret=True après create")
    ck(set(body["domains"]) == {"corp.io", "revpulse.io"}, f"domains nettoyés lowercase/sans @ (got {body['domains']})")
    ck(body["enabled"] is True, "enabled=True")
    # tenant.sso_enabled sync
    test_db.refresh(tenant)
    ck(tenant.sso_enabled is True, "tenant.sso_enabled sync à True après PUT enabled=True")
    # client_secret chiffré en DB (pas la valeur plain)
    sso_db = test_db.query(M.EnterpriseSSO).filter_by(tenant_id="tnt_corp").first()
    ck(sso_db.client_secret_encrypted != "oidc_secret_xyz", "client_secret chiffré en DB (pas plain)")

    # 3. GET après PUT : config retournée, has_secret=True, client_secret JAMAIS dans la réponse
    r = client.get("/api/tenants/tnt_corp/sso", headers=H_OWNER)
    ck(r.status_code == 200, f"GET after PUT → 200 (got {r.status_code})")
    resp_text = r.text
    ck("oidc_secret_xyz" not in resp_text, "client_secret plain JAMAIS dans GET response")
    ck("client_secret" not in resp_text, "champ client_secret absent de la response")
    ck(r.json()["has_secret"] is True, "has_secret=True dans GET")

    # 4. PUT (update) : modifie client_id + domains
    cfg2 = {**cfg, "client_id": "oidc_client_NEW", "domains": ["corp.io", "revpulse.io", "sub.corp.io"]}
    r = client.put("/api/tenants/tnt_corp/sso", json=cfg2, headers=H_OWNER)
    ck(r.status_code == 200, f"PUT update → 200 (got {r.status_code})")
    ck(r.json()["client_id"] == "oidc_client_NEW", "client_id updaté")
    ck(len(r.json()["domains"]) == 3, f"domains updaté (3) (got {len(r.json()['domains'])})")

    # ══════════════════════════════════════════════════════════════════════
    # B. Permission gate
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Permission gate (workspace.manage = Owner-only) ---")

    # 6. Admin role → PUT 403
    r = client.put("/api/tenants/tnt_corp/sso", json=cfg, headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin PUT → 403 (workspace.manage Owner-only) (got {r.status_code})")

    # 7. Non-member → GET 403
    r = client.get("/api/tenants/tnt_corp/sso", headers=H_OUT)
    ck(r.status_code == 403, f"Outsider GET → 403 (non-member) (got {r.status_code})")

    # 8. Owner → PUT 200 (déjà vérifié en A.4, re-confirm)
    r = client.put("/api/tenants/tnt_corp/sso", json=cfg, headers=H_OWNER)
    ck(r.status_code == 200, "Owner PUT → 200 (workspace.manage OK)")

    # ══════════════════════════════════════════════════════════════════════
    # C. SSO lookup by email (public — pas d'auth)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. SSO lookup by email (public) ---")

    # 9. Lookup email domaine match → initiate_url
    r = client.post("/api/auth/sso/lookup", json={"email": "alice@corp.io"})
    ck(r.status_code == 200, f"lookup alice@corp.io → 200 (got {r.status_code})")
    d = r.json()
    ck(d["tenant_id"] == "tnt_corp", f"tenant_id retourné (got {d.get('tenant_id')})")
    ck(d["tenant_name"] == "Corp SSO", "tenant_name retourné")
    ck(d["initiate_url"] == "/api/auth/sso/tnt_corp/initiate", f"initiate_url correct (got {d.get('initiate_url')})")

    # 10. Lookup email domaine non configuré → 404
    r = client.post("/api/auth/sso/lookup", json={"email": "bob@evil.com"})
    ck(r.status_code == 404, f"lookup bob@evil.com → 404 (domaine non configuré) (got {r.status_code})")

    # 11. Lookup email invalide → 400
    r = client.post("/api/auth/sso/lookup", json={"email": "not-an-email"})
    ck(r.status_code == 400, f"lookup email invalide → 400 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # D. Sécurité + DELETE
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. Sécurité + DELETE ---")

    # 13. client_secret JAMAIS dans GET (re-check after update)
    r = client.get("/api/tenants/tnt_corp/sso", headers=H_OWNER)
    ck("oidc_secret_xyz" not in r.text and "client_secret" not in r.text, "client_secret absent de GET (sécurité)")

    # 14. domains nettoyés (déjà vérifié en A.2, re-confirm avec update)
    ck(set(r.json()["domains"]) == {"corp.io", "revpulse.io"}, "domains nettoyés (lowercase, sans @)")

    # 5. DELETE : supprime config + tenant.sso_enabled=False
    r = client.delete("/api/tenants/tnt_corp/sso", headers=H_OWNER)
    ck(r.status_code == 200, f"DELETE → 200 (got {r.status_code})")
    ck(r.json()["deleted"] is True, "deleted=True")
    test_db.refresh(tenant)
    ck(tenant.sso_enabled is False, "tenant.sso_enabled=False après DELETE")
    # GET → 404 après DELETE
    r = client.get("/api/tenants/tnt_corp/sso", headers=H_OWNER)
    ck(r.status_code == 404, f"GET après DELETE → 404 (got {r.status_code})")

    # 12. Lookup après DELETE → 404
    r = client.post("/api/auth/sso/lookup", json={"email": "alice@corp.io"})
    ck(r.status_code == 404, f"lookup après DELETE → 404 (got {r.status_code})")

    # Permission gate DELETE : Admin → 403 (re-configurer d'abord)
    client.put("/api/tenants/tnt_corp/sso", json=cfg, headers=H_OWNER)
    r = client.delete("/api/tenants/tnt_corp/sso", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin DELETE → 403 (got {r.status_code})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
