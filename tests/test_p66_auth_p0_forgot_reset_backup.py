#!/usr/bin/env python3
"""test_p66_auth_p0_forgot_reset_backup.py — P66 Auth P0 (A2 forgot/reset + A3 2FA backup codes)."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p66_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_RATE_LIMIT_ENABLED", "false")
passed = failed = 0
def ck(c, l):
    global passed, failed
    print(f"  {'✓' if c else '✗'} {l}")
    if c: passed += 1
    else: failed += 1

def main():
    from app import models as M
    from app.database import Base, get_db
    from app.security import create_access_token, hash_password
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.orm import sessionmaker
    import pyotp
    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine); test_db = Sess()
    def _ov(): yield test_db
    from app.main import app
    app.dependency_overrides[get_db] = _ov
    client = TestClient(app)
    for t in [M.AuditLog, M.Member, M.User, M.Tenant]: test_db.query(t).delete()
    test_db.commit()
    print("=== test_p66_auth_p0_forgot_reset_backup.py — P66 Auth P0 (A2/A3) ===\n")
    owner = M.User(id="usr_oG", email="owner@corp.io", name="Alice", password_hash=hash_password("Secret123!"), locale="fr")
    tenant = M.Tenant(id="tnt_p66", name="Corp P66", plan="Enterprise", owner_id="usr_oG", sso_enabled=False, retention_days=2555)
    mem = M.Member(id="mem_oG", tenant_id="tnt_p66", user_id="usr_oG", email="owner@corp.io", name="Alice", role="Owner", status="active")
    test_db.add_all([owner, tenant, mem]); test_db.commit()

    # ═══ A. Forgot/reset password (A2) ═══
    print("--- A. Forgot/reset password (A2) ---")
    r = client.post("/api/auth/forgot-password", json={"email": "owner@corp.io"})
    ck(r.status_code == 200, f"POST /forgot-password → 200 (got {r.status_code})")
    reset_token = r.json().get("reset_token", "")
    ck(bool(reset_token), "forgot-password retourne reset_token en dev/test")
    u = test_db.query(M.User).filter_by(id="usr_oG").first()
    ck(u.password_reset_token_hash is not None, "password_reset_token_hash stocké (bcrypt)")
    ck(u.password_reset_expires_at is not None, "password_reset_expires_at set (15 min)")
    ck(reset_token != u.password_reset_token_hash, "token retourné en clair != hash stocké")
    # reset password
    r = client.post("/api/auth/reset-password", json={"token": reset_token, "new_password": "NewSecret456!"})
    ck(r.status_code == 200, f"POST /reset-password → 200 (got {r.status_code})")
    ck(r.json().get("reset") is True, "reset-password → reset=true")
    # token single-use (replay → 400)
    r = client.post("/api/auth/reset-password", json={"token": reset_token, "new_password": "X"})
    ck(r.status_code == 400, f"reset-password replay token (single-use) → 400 (got {r.status_code})")
    # login avec nouveau password
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "NewSecret456!"})
    ck(r.status_code == 200, f"login avec nouveau password → 200 (got {r.status_code})")
    # login avec ancien password → 401
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "Secret123!"})
    ck(r.status_code == 401, f"login ancien password → 401 (got {r.status_code})")
    # anti-énumération : email inconnu → 200 (pas 404)
    r = client.post("/api/auth/forgot-password", json={"email": "unknown@nowhere.io"})
    ck(r.status_code == 200, f"forgot-password email inconnu → 200 (anti-énumération) (got {r.status_code})")
    ck("reset_token" not in r.json(), "email inconnu ne retourne pas de token (anti-énumération)")
    # token invalide → 400
    r = client.post("/api/auth/reset-password", json={"token": "invalid-token", "new_password": "X"})
    ck(r.status_code == 400, f"reset-password token invalide → 400 (got {r.status_code})")

    # ═══ B. 2FA backup codes (A3) ═══
    print("\n--- B. 2FA backup codes (A3) ---")
    H = {"Authorization": f"Bearer {create_access_token('usr_oG')}"}
    r = client.post("/api/auth/2fa/setup", headers=H)
    ck(r.status_code == 200, f"2fa/setup → 200 (got {r.status_code})")
    secret = r.json()["secret"]
    totp = pyotp.TOTP(secret)
    # verify → génère backup codes
    r = client.post("/api/auth/2fa/verify", headers=H, json={"code": totp.now()})
    ck(r.status_code == 200, f"2fa/verify → 200 (got {r.status_code})")
    backup_codes = r.json().get("backup_codes", [])
    ck(len(backup_codes) == 10, f"verify génère 10 backup codes (got {len(backup_codes)})")
    ck(all("-" in c and len(c) >= 10 for c in backup_codes), "backup codes format XXXX-XXXX-XXXX")
    # login → 403 requires_2fa + temp_token
    r = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "NewSecret456!"})
    ck(r.status_code == 403, f"login 2FA on → 403 requires_2fa (got {r.status_code})")
    temp_token = r.json()["detail"]["temp_token"]
    # login/2fa/backup avec un backup code → 200 Token
    r = client.post("/api/auth/login/2fa/backup", json={"temp_token": temp_token, "code": backup_codes[0]})
    ck(r.status_code == 200, f"login/2fa/backup code valide → 200 Token (got {r.status_code})")
    ck(r.json()["token"] != temp_token, "backup login → token session != temp_token")
    # backup code consommé (1-use) → 2e tentative → 401
    r = client.post("/api/auth/login/2fa/backup", json={"temp_token": temp_token, "code": backup_codes[0]})
    ck(r.status_code == 401, f"backup code 1-use (replay) → 401 (got {r.status_code})")
    # 2e backup code différent → 200 (encore 9 dispo)
    r2 = client.post("/api/auth/login", json={"email": "owner@corp.io", "password": "NewSecret456!"})
    temp_token2 = r2.json()["detail"]["temp_token"]
    r = client.post("/api/auth/login/2fa/backup", json={"temp_token": temp_token2, "code": backup_codes[1]})
    ck(r.status_code == 200, f"2e backup code → 200 (got {r.status_code})")
    # backup code invalide → 401
    r = client.post("/api/auth/login/2fa/backup", json={"temp_token": temp_token2, "code": "ZZZZ-ZZZZ-ZZZZ"})
    ck(r.status_code == 401, f"backup code invalide → 401 (got {r.status_code})")

    # ═══ C. Migration 0036 ═══
    print("\n--- C. Migration 0036 (users columns) ---")
    cols = [c["name"] for c in sa_inspect(engine).get_columns("users")]
    ck("password_reset_token_hash" in cols, "col users.password_reset_token_hash (migration 0036)")
    ck("password_reset_expires_at" in cols, "col users.password_reset_expires_at")
    ck("backup_codes_hashed" in cols, "col users.backup_codes_hashed")

    # ═══ D. 2FA disable clears backup codes ═══
    print("\n--- D. 2FA disable clears backup codes ---")
    r = client.delete(f"/api/auth/2fa?code={totp.now()}", headers=H)
    ck(r.status_code == 200, f"2fa/disable → 200 (got {r.status_code})")
    u = test_db.query(M.User).filter_by(id="usr_oG").first()
    ck(u.backup_codes_hashed is None, "disable purge backup_codes_hashed (None)")
    ck(u.totp_enabled is False, "disable → totp_enabled=False")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
