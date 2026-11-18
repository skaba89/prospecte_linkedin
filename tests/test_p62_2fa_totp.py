#!/usr/bin/env python3
"""test_p62_2fa_totp.py — P62 2FA/TOTP (RFC 6238) durcissement auth.

RevPulse gère des données PII (contacts candidats, GDPR) → l'authentification forte (2FA) protège
les comptes Owner/Admin contre le credential stuffing. P62 livre le cycle complet TOTP :
  setup (génération secret Fernet) → verify (activation) → login gate (requires_2fa + temp_token) →
  login/2fa (valide code + émet token) → disable (avec code anti-désactivation accidentelle).

Coverage (TestClient) :
  A. Setup (POST /auth/2fa/setup) :
    1. setup → 200 avec {secret (base32 32 chars), otpauth_uri, enabled=false}.
    2. otpauth_uri contient "otpauth://totp/" + issuer RevPulse + email.
    3. secret stocké chiffré Fernet en DB (totp_secret_encrypted != secret en clair).
    4. totp_enabled reste False après setup (anti-lockout : setup sans verify = no-op).
  B. Verify (POST /auth/2fa/verify) :
    5. verify avec code valide → 200 {enabled=true}.
    6. totp_enabled passe à True en DB.
    7. audit entry auth.2fa.enable loggée.
    8. verify sans setup préalable → 400.
    9. verify avec code invalide → 401 + totp_enabled reste False.
  C. Login gate (2FA activé) :
    10. POST /login (bon password) → 403 {requires_2fa: true, temp_token}.
    11. temp_token décodable (JWT claim 2fa=true, TTL 5 min).
    12. login avec mauvais password → 401 (pas de leak requires_2fa).
    13. login utilisateur sans 2FA → 200 Token normal.
  D. Login/2fa (POST /auth/login/2fa) :
    14. login/2fa {temp_token, code valide} → 200 Token (token != temp_token).
    15. login/2fa avec code invalide → 401.
    16. login/2fa avec temp_token expiré/invalide → 401.
    17. login/2fa avec temp_token non-2fa (token session normal) → 401 (claim 2fa absent).
  E. Disable (DELETE /auth/2fa) :
    18. disable avec code valide → 200 {enabled=false} + secret purgé (totp_secret_encrypted=None).
    19. disable avec code invalide → 401 + totp_enabled reste True.
    20. disable sans 2FA activé → 400.
    21. audit entry auth.2fa.disable loggée.
    22. Après disable, login retourne 200 Token normal (plus de gate 2FA).
  F. Migration 0035 :
    23. users.totp_secret_encrypted + users.totp_enabled colonnes existent.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p62_test.db python tests/test_p62_2fa_totp.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p62_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_RATE_LIMIT_ENABLED", "false")  # P62 : login x12 dans le test → rate-limit OFF

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
    from app.security import decode_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.orm import sessionmaker

    import pyotp

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    test_db = Sess()

    def _override_db():
        yield test_db

    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    for tbl in [M.AuditLog, M.ConsentRecord, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p62_2fa_totp.py — P62 2FA/TOTP (RFC 6238) ===\n")

    from app.security import hash_password
    owner = M.User(id="usr_oE", email="owner@corp.io", name="Alice Owner",
                   password_hash=hash_password("Secret123!"), locale="fr")
    plain_user = M.User(id="usr_pE", email="plain@corp.io", name="Plain User",
                        password_hash=hash_password("Secret123!"), locale="fr")
    tenant = M.Tenant(id="tnt_cE", name="Corp P62", plan="Enterprise", owner_id="usr_oE",
                      sso_enabled=False, retention_days=2555)
    mem_owner = M.Member(id="mem_oE", tenant_id="tnt_cE", user_id="usr_oE",
                         email="owner@corp.io", name="Alice Owner", role="Owner", status="active")
    test_db.add_all([owner, plain_user, tenant, mem_owner]); test_db.commit()

    # ══════════════════════════════════════════════════════════════════════
    # A. Setup
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Setup (POST /auth/2fa/setup) ---")
    # D'abord login normal (owner n'a pas encore 2FA) pour obtenir un token
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "Secret123!"})
    ck(r.status_code == 200, f"login owner (sans 2FA) → 200 (got {r.status_code})")
    H_OWNER = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.post("/api/auth/2fa/setup", headers=H_OWNER)
    ck(r.status_code == 200, f"POST /2fa/setup → 200 (got {r.status_code})")
    setup = r.json()
    ck(len(setup["secret"]) == 32, f"secret base32 (32 chars) (got {len(setup['secret'])})")
    ck(setup["enabled"] is False, "enabled=False au setup (anti-lockout)")
    ck("otpauth://totp/" in setup["otpauth_uri"], "otpauth_uri contient otpauth://totp/")
    ck("RevPulse" in setup["otpauth_uri"] and "owner" in setup["otpauth_uri"] and "corp.io" in setup["otpauth_uri"],
       "otpauth_uri contient issuer RevPulse + email (URL-encodé)")
    # Secret chiffré en DB (Fernet — différent du clair retourné)
    u = test_db.query(M.User).filter_by(id="usr_oE").first()
    ck(u.totp_secret_encrypted is not None, "totp_secret_encrypted stocké en DB")
    ck(u.totp_secret_encrypted != setup["secret"], "secret chiffré Fernet (pas en clair en DB)")
    ck(u.totp_enabled is False, "totp_enabled reste False après setup")

    # ══════════════════════════════════════════════════════════════════════
    # B. Verify
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Verify (POST /auth/2fa/verify) ---")
    totp = pyotp.TOTP(setup["secret"])
    valid_code = totp.now()
    r = client.post("/api/auth/2fa/verify", headers=H_OWNER, json={"code": valid_code})
    ck(r.status_code == 200, f"verify code valide → 200 (got {r.status_code})")
    ck(r.json()["enabled"] is True, "verify → enabled=true")
    u = test_db.query(M.User).filter_by(id="usr_oE").first()
    ck(u.totp_enabled is True, "totp_enabled=True en DB après verify")
    # audit entry
    enable_log = test_db.query(M.AuditLog).filter_by(action="auth.2fa.enable").first()
    ck(enable_log is not None, "audit entry auth.2fa.enable loggée")
    ck(enable_log.target_id == "usr_oE" if enable_log else False, "audit auth.2fa.enable target=user_id")

    # verify sans setup (plain_user n'a pas setup) → 400
    r2 = client.post("/api/auth/login", json={"email": "plain@corp.io", "password": "Secret123!"})
    H_PLAIN = {"Authorization": f"Bearer {r2.json()['token']}"}
    r = client.post("/api/auth/2fa/verify", headers=H_PLAIN, json={"code": "000000"})
    ck(r.status_code == 400, f"verify sans setup → 400 (got {r.status_code})")
    # verify code invalide (owner a setup mais code faux) → 401 + reste True (déjà activé)
    r = client.post("/api/auth/2fa/verify", headers=H_OWNER, json={"code": "000000"})
    ck(r.status_code == 401, f"verify code invalide → 401 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # C. Login gate (2FA activé)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Login gate (2FA activé) ---")
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "Secret123!"})
    ck(r.status_code == 403, f"login owner (2FA on, bon password) → 403 requires_2fa (got {r.status_code})")
    detail = r.json().get("detail", {})
    ck(isinstance(detail, dict) and detail.get("requires_2fa") is True, "403 detail.requires_2fa=true")
    ck("temp_token" in detail, "403 detail.temp_token présent")
    temp_token = detail.get("temp_token", "")
    # temp_token décodable + claim 2fa
    payload = decode_token(temp_token)
    ck(payload is not None and payload.get("2fa") is True, "temp_token JWT claim 2fa=true")
    ck(payload.get("sub") == "usr_oE", "temp_token sub=user_id")
    # login mauvais password → 401 (pas de leak requires_2fa)
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "WRONG"})
    ck(r.status_code == 401, f"login mauvais password → 401 (got {r.status_code})")
    ck(not (isinstance(r.json().get("detail"), dict) and r.json()["detail"].get("requires_2fa")),
       "mauvais password ne leak pas requires_2fa")
    # login plain user (sans 2FA) → 200 Token normal
    r = client.post("/api/auth/login", json={"email": "plain@corp.io", "password": "Secret123!"})
    ck(r.status_code == 200, f"login plain user (sans 2FA) → 200 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # D. Login/2fa
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. Login/2fa (POST /auth/login/2fa) ---")
    valid_code = totp.now()
    r = client.post("/api/auth/login/2fa", json={"temp_token": temp_token, "code": valid_code})
    ck(r.status_code == 200, f"login/2fa code valide → 200 Token (got {r.status_code})")
    ck(r.json()["token"] != temp_token, "token session != temp_token")
    ck(r.json()["user"]["id"] == "usr_oE", "login/2fa user.id correct")
    # code invalide → 401
    r = client.post("/api/auth/login/2fa", json={"temp_token": temp_token, "code": "000000"})
    ck(r.status_code == 401, f"login/2fa code invalide → 401 (got {r.status_code})")
    # temp_token invalide → 401
    r = client.post("/api/auth/login/2fa", json={"temp_token": "invalid.jwt.token", "code": valid_code})
    ck(r.status_code == 401, f"login/2fa temp_token invalide → 401 (got {r.status_code})")
    # temp_token non-2fa (token session normal) → 401 (claim 2fa absent)
    full_token = client.post("/api/auth/login/2fa", json={"temp_token": temp_token, "code": totp.now()}).json()["token"]
    r = client.post("/api/auth/login/2fa", json={"temp_token": full_token, "code": valid_code})
    ck(r.status_code == 401, f"login/2fa avec token session (non-2fa) → 401 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # E. Disable
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- E. Disable (DELETE /auth/2fa) ---")
    # disable avec code valide (query param — DELETE = pas de body)
    valid_code = totp.now()
    r = client.delete("/api/auth/2fa?code=" + valid_code, headers=H_OWNER)
    ck(r.status_code == 200, f"disable code valide → 200 (got {r.status_code})")
    ck(r.json()["enabled"] is False, "disable → enabled=false")
    u = test_db.query(M.User).filter_by(id="usr_oE").first()
    ck(u.totp_enabled is False, "totp_enabled=False en DB après disable")
    ck(u.totp_secret_encrypted is None, "totp_secret_encrypted purgé (None) après disable")
    # audit entry disable
    dis_log = test_db.query(M.AuditLog).filter_by(action="auth.2fa.disable").first()
    ck(dis_log is not None, "audit entry auth.2fa.disable loggée")
    # disable sans 2FA activé → 400
    r = client.delete("/api/auth/2fa?code=000000", headers=H_OWNER)
    ck(r.status_code == 400, f"disable sans 2FA activé → 400 (got {r.status_code})")
    # Après disable, login retourne 200 Token normal (plus de gate)
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "Secret123!"})
    ck(r.status_code == 200, f"login après disable → 200 (plus de gate 2FA) (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # F. Migration 0035
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- F. Migration 0035 (users.totp_* columns) ---")
    insp = sa_inspect(engine)
    cols = [c["name"] for c in insp.get_columns("users")]
    ck("totp_secret_encrypted" in cols, "colonne users.totp_secret_encrypted existe (migration 0035)")
    ck("totp_enabled" in cols, "colonne users.totp_enabled existe (migration 0035)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
