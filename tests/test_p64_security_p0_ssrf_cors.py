#!/usr/bin/env python3
"""test_p64_security_p0_ssrf_cors.py — P64 Sécurité P0 (audit V1.0 blocages O1/O2/O3).

3 blocages P0 sécurité corrigés :
  O1 : SSRF SSO Enterprise — token_url/userinfo_url/authorize_url non validés → exfil client_secret + takeover.
  O2 : SSRF webhook alertes audit — webhook_url non validée → POST vers metadata/interne.
  O3 : CORS '*' + allow_credentials=True — API callable depuis toute origine (CSRF credentialed).

Coverage :
  A. SSRF SSO (P0-O1) :
    1. PUT /sso avec token_url=http://169.254.169.254/... → 400 (metadata bloqué).
    2. PUT /sso avec userinfo_url=http://127.0.0.1:8080/... → 400 (loopback bloqué).
    3. PUT /sso avec authorize_url=http://10.0.0.1/... → 400 (private bloqué).
    4. PUT /sso avec URLs valides https → 200 (config acceptée).
  B. SSRF audit webhook (P0-O2) :
    5. PUT /audit/alerts/webhook avec webhook_url=http://169.254.169.254/... → 400.
    6. PUT /audit/alerts/webhook avec webhook_url=http://127.0.0.1/... → 400.
    7. PUT /audit/alerts/webhook avec webhook_url=https://hooks.slack.com/... → 200.
    8. PUT /audit/alerts/webhook avec webhook_url=None → 200 (désactivé, pas de validation).
  C. CORS fail-fast prod (P0-O3) :
    9. Settings(prod, cors_origins="*") → ValueError.
    10. Settings(prod, cors_origins="") → ValueError.
    11. Settings(prod, cors_origins="https://app.io") → OK.
    12. Settings(test, cors_origins="") → OK (dev permissif).
    13. Settings(test, cors_origins="*") → OK (dev permissif).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p64_test.db python tests/test_p64_security_p0_ssrf_cors.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p64_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
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
    from app.security import create_access_token, hash_password
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

    for tbl in [M.AuditLog, M.ConsentRecord, M.EnterpriseSSO, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    owner = M.User(id="usr_oF", email="owner@corp.io", name="Alice Owner",
                   password_hash=hash_password("Secret123!"), locale="fr")
    tenant = M.Tenant(id="tnt_cF", name="Corp P64", plan="Enterprise", owner_id="usr_oF",
                      sso_enabled=False, retention_days=2555)
    mem = M.Member(id="mem_oF", tenant_id="tnt_cF", user_id="usr_oF",
                   email="owner@corp.io", name="Alice Owner", role="Owner", status="active")
    test_db.add_all([owner, tenant, mem]); test_db.commit()
    H = {"Authorization": f"Bearer {create_access_token('usr_oF')}"}

    print("=== test_p64_security_p0_ssrf_cors.py — P64 Sécurité P0 (O1/O2/O3) ===\n")

    # ══════════════════════════════════════════════════════════════════════
    # A. SSRF SSO (P0-O1)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. SSRF SSO (P0-O1) ---")
    SSO_VALID = {
        "client_id": "cid", "client_secret": "csec",
        "issuer_url": "https://idp.corp.io",
        "authorize_url": "https://idp.corp.io/authorize",
        "token_url": "https://idp.corp.io/token",
        "userinfo_url": "https://idp.corp.io/userinfo",
        "domains": ["corp.io"], "enabled": True,
    }

    def _put_sso(overrides):
        body = {**SSO_VALID, **overrides}
        return client.put("/api/tenants/tnt_cF/sso", headers=H, json=body)

    # 1. token_url metadata → 400
    r = _put_sso({"token_url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"})
    ck(r.status_code == 400, f"PUT /sso token_url=metadata → 400 (got {r.status_code})")
    ck("SSRF" in r.json().get("detail", ""), "400 detail mentionne SSRF")
    # 2. userinfo_url loopback → 400
    r = _put_sso({"userinfo_url": "http://127.0.0.1:8080/userinfo"})
    ck(r.status_code == 400, f"PUT /sso userinfo_url=loopback → 400 (got {r.status_code})")
    # 3. authorize_url private → 400
    r = _put_sso({"authorize_url": "http://10.0.0.1/authorize"})
    ck(r.status_code == 400, f"PUT /sso authorize_url=private → 400 (got {r.status_code})")
    # 4. URLs valides https → 200
    r = _put_sso({})
    ck(r.status_code == 200, f"PUT /sso URLs valides https → 200 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # B. SSRF audit webhook (P0-O2)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. SSRF audit webhook (P0-O2) ---")

    def _put_webhook(url):
        return client.put("/api/tenants/tnt_cF/audit/alerts/webhook", headers=H,
                          json={"webhook_url": url, "min_severity": "high"})

    # 5. metadata → 400
    r = _put_webhook("http://169.254.169.254/latest/meta-data/")
    ck(r.status_code == 400, f"PUT webhook url=metadata → 400 (got {r.status_code})")
    ck("SSRF" in r.json().get("detail", ""), "400 detail mentionne SSRF")
    # 6. loopback → 400
    r = _put_webhook("http://127.0.0.1:9000/hook")
    ck(r.status_code == 400, f"PUT webhook url=loopback → 400 (got {r.status_code})")
    # 7. URL valide https → 200
    r = _put_webhook("https://hooks.slack.com/services/T000/B000/XXX")
    ck(r.status_code == 200, f"PUT webhook url=https://hooks.slack.com → 200 (got {r.status_code})")
    # 8. webhook_url=None (désactivé) → 200 (pas de validation)
    r = _put_webhook(None)
    ck(r.status_code == 200, f"PUT webhook url=None → 200 (désactivé, pas de validation) (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # C. CORS fail-fast prod (P0-O3)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. CORS fail-fast prod (P0-O3) ---")
    from app.config import Settings

    JWT_OK = "x" * 32
    FERNET_OK = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI="
    STRIPE_SK = "sk_live_x"
    STRIPE_WH = "whsec_x"
    # 9. prod + cors_origins="*" → ValueError
    try:
        Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                 stripe_secret_key=STRIPE_SK, stripe_webhook_secret=STRIPE_WH, cors_origins="*")
        ck(False, "prod cors_origins='*' → ValueError")
    except ValueError:
        ck(True, "prod cors_origins='*' → ValueError (P0-O3)")
    # 10. prod + cors_origins="" → ValueError
    try:
        Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                 stripe_secret_key=STRIPE_SK, stripe_webhook_secret=STRIPE_WH, cors_origins="")
        ck(False, "prod cors_origins='' → ValueError")
    except ValueError:
        ck(True, "prod cors_origins='' → ValueError (P0-O3)")
    # 11. prod + cors_origins explicite → OK
    try:
        s = Settings(env="production", jwt_secret=JWT_OK, token_encryption_key=FERNET_OK,
                     stripe_secret_key=STRIPE_SK, stripe_webhook_secret=STRIPE_WH,
                     cors_origins="https://app.revpulse.io,https://www.revpulse.io")
        ck(s.is_production and "app.revpulse.io" in s.cors_origins, "prod cors_origins explicite → OK (P0-O3)")
    except ValueError:
        ck(False, "prod cors_origins explicite → OK")
    # 12. test + cors_origins="" → OK (dev permissif, fallback ["*"] côté main.py)
    try:
        s = Settings(env="test", jwt_secret=JWT_OK, cors_origins="")
        ck(not s.is_production, "test cors_origins='' → OK (dev permissif)")
    except ValueError:
        ck(False, "test cors_origins='' → OK")
    # 13. test + cors_origins="*" → OK (dev permissif)
    try:
        s = Settings(env="test", jwt_secret=JWT_OK, cors_origins="*")
        ck(not s.is_production, "test cors_origins='*' → OK (dev permissif)")
    except ValueError:
        ck(False, "test cors_origins='*' → OK")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
