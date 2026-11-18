#!/usr/bin/env python3
"""test_p47_admin_sso_ui.py — P47 Admin SSO UI frontend + i18n admin endpoints.

Coverage (TestClient, AUCUN réseau IdP externe en CI) :
  A. Page admin SSO se charge (GET /admin/sso.html) :
    1. GET → 200, content-type text/html.
    2. Contient les champs du form (client_id, client_secret, issuer, authorize, token, userinfo, scopes, domains, enabled).
    3. Contient les badges (secret-badge, status-badge) + boutons (btn-save, btn-delete).
    4. Contient le lien de retour + le titre SSO.
    5. Référence ../assets/app.css + ../assets/app.js (paths relatifs corrects pour sous-dossier admin/).
  B. Lien SSO présent dans index.html :
    6. GET / → 200, contient href="admin/sso.html".
  C. i18n admin endpoints (Accept-Language → t()) :
    7. GET /api/tenants/{tid}/sso 404 → message FR par défaut.
    8. GET avec Accept-Language: en → message EN ("No SSO configuration").
    9. GET avec Accept-Language: es → message ES ("Sin configuración SSO").
    10. POST /api/auth/sso/lookup email invalide → FR/EN/ES selon header.
    11. PUT sans domains → message i18n (FR/EN/ES).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p47_test.db python tests/test_p47_admin_sso_ui.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p47_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
# P47 : sert les fichiers statiques depuis frontend/public (le mount n'a lieu que si _FE_DIR existe).
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

    for tbl in [M.EnterpriseSSO, M.OAuthState, M.Subscription, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p47_admin_sso_ui.py — P47 Admin SSO UI frontend + i18n admin endpoints ===\n")

    # Seed owner + tenant
    owner = M.User(id="usr_owner2", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_corp2", name="Corp SSO", plan="Enterprise", owner_id="usr_owner2", sso_enabled=False)
    test_db.add_all([owner, tenant]); test_db.commit()
    owner_tok = create_access_token("usr_owner2")
    H_OWNER = {"Authorization": f"Bearer {owner_tok}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Page admin SSO se charge
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Page admin SSO (/admin/sso.html) ---")
    r = client.get("/admin/sso.html")
    ck(r.status_code == 200, f"GET /admin/sso.html → 200 (got {r.status_code})")
    ck("text/html" in r.headers.get("content-type", ""), "content-type text/html")
    html = r.text
    # 2. Champs form
    for fid in ["f-client-id", "f-client-secret", "f-issuer", "f-authorize", "f-token", "f-userinfo", "f-scopes", "f-domains", "f-enabled"]:
        ck(f'id="{fid}"' in html, f"champ form #{fid} présent")
    # 3. Badges + boutons
    ck('id="secret-badge"' in html, "badge secret-badge présent")
    ck('id="status-badge"' in html, "badge status-badge présent")
    ck('id="btn-save"' in html, "bouton btn-save présent")
    ck('id="btn-delete"' in html, "bouton btn-delete présent")
    # 4. Titre + retour
    ck("SSO Enterprise" in html, "titre SSO Enterprise présent")
    ck('href="/"' in html, "lien retour vers / présent")
    # 5. Paths relatifs ../assets/
    ck('href="../assets/app.css"' in html, "référence ../assets/app.css (path relatif sous-dossier admin/)")
    ck('src="../assets/app.js"' in html, "référence ../assets/app.js (path relatif)")
    # viewport meta (responsive)
    ck('name="viewport"' in html and 'width=device-width' in html, "viewport meta responsive présent")

    # ══════════════════════════════════════════════════════════════════════
    # B. Lien SSO dans index.html
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Lien SSO dans index.html (/) ---")
    r = client.get("/")
    ck(r.status_code == 200, f"GET / → 200 (got {r.status_code})")
    ck('href="admin/sso.html"' in r.text or 'href="/admin/sso.html"' in r.text, "lien admin/sso.html présent dans index.html")

    # ══════════════════════════════════════════════════════════════════════
    # C. i18n admin endpoints (Accept-Language → t())
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. i18n admin endpoints (FR/EN/ES via Accept-Language) ---")

    # 7-9. GET 404 → FR/EN/ES
    r = client.get("/api/tenants/tnt_corp2/sso", headers={**H_OWNER})
    ck(r.status_code == 404, f"GET no config → 404 (got {r.status_code})")
    ck("Aucune config SSO" in r.text, f"FR default : 'Aucune config SSO' (got {r.text[:80]})")
    r = client.get("/api/tenants/tnt_corp2/sso", headers={**H_OWNER, "Accept-Language": "en"})
    ck("No SSO configuration" in r.text, f"EN : 'No SSO configuration' (got {r.text[:80]})")
    r = client.get("/api/tenants/tnt_corp2/sso", headers={**H_OWNER, "Accept-Language": "es-ES"})
    ck("Sin configuración SSO" in r.text, f"ES : 'Sin configuración SSO' (got {r.text[:80]})")

    # 10. POST lookup email invalide → FR/EN/ES
    r = client.post("/api/auth/sso/lookup", json={"email": "not-an-email"})
    ck(r.status_code == 400, f"lookup invalide → 400 (got {r.status_code})")
    ck("E-mail invalide" in r.text, "FR : 'E-mail invalide'")
    r = client.post("/api/auth/sso/lookup", json={"email": "not-an-email"}, headers={"Accept-Language": "en"})
    ck("Invalid email" in r.text, f"EN : 'Invalid email' (got {r.text[:80]})")
    r = client.post("/api/auth/sso/lookup", json={"email": "not-an-email"}, headers={"Accept-Language": "es"})
    ck("Correo inválido" in r.text, f"ES : 'Correo inválido' (got {r.text[:80]})")

    # 11. PUT sans domains → FR/EN/ES
    bad_cfg = {"client_id": "x", "client_secret": "y", "issuer_url": "u", "authorize_url": "a",
               "token_url": "t", "userinfo_url": "u", "domains": []}
    r = client.put("/api/tenants/tnt_corp2/sso", json=bad_cfg, headers=H_OWNER)
    ck(r.status_code == 400, f"PUT sans domains → 400 (got {r.status_code})")
    ck("Au moins 1 domaine requis" in r.text, "FR : 'Au moins 1 domaine requis'")
    r = client.put("/api/tenants/tnt_corp2/sso", json=bad_cfg, headers={**H_OWNER, "Accept-Language": "en"})
    ck("At least 1 domain required" in r.text, f"EN : 'At least 1 domain required' (got {r.text[:80]})")
    r = client.put("/api/tenants/tnt_corp2/sso", json=bad_cfg, headers={**H_OWNER, "Accept-Language": "es"})
    ck("Al menos 1 dominio requerido" in r.text, f"ES : 'Al menos 1 dominio requerido' (got {r.text[:80]})")

    # PUT sans credentials → i18n
    bad_cfg2 = {**bad_cfg, "domains": ["corp.io"], "client_id": "", "client_secret": ""}
    r = client.put("/api/tenants/tnt_corp2/sso", json=bad_cfg2, headers={**H_OWNER, "Accept-Language": "en"})
    ck("client_id and client_secret are required" in r.text, f"EN credentials_required (got {r.text[:80]})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
