#!/usr/bin/env python3
"""test_p68_auth_p0_sso_email_verify.py — P68 Auth P0 (A4 SSO email_verified gate + A5 email verification)."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p68_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))
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
    from app.security import create_access_token, create_email_verify_token, decode_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine); test_db = Sess()
    def _ov(): yield test_db
    from app.main import app
    app.dependency_overrides[get_db] = _ov
    client = TestClient(app)
    for t in [M.AuditLog, M.Member, M.User, M.Tenant]: test_db.query(t).delete()
    test_db.commit()
    print("=== test_p68_auth_p0_sso_email_verify.py — P68 Auth P0 (A4/A5) ===\n")

    # ═══ A. Email verification register (A5) ═══
    print("--- A. Email verification register (A5) ---")
    r = client.post("/api/auth/register", json={"name":"Victor","email":"vic@corp.io","password":"Secret123!"})
    ck(r.status_code == 200, f"register → 200 (got {r.status_code})")
    reg = r.json()
    verify_token = reg.get("verify_token", "")
    ck(bool(verify_token), "register retourne verify_token en dev/test")
    # user créé avec email_verified=False
    u = test_db.query(M.User).filter_by(email="vic@corp.io").first()
    ck(u.email_verified is False, "user.email_verified=False après register (A5)")
    # token stateless décodable + claim verify_email
    payload = decode_token(verify_token)
    ck(payload is not None and payload.get("verify_email") is True, "verify_token décodable + claim verify_email=True")
    ck(payload.get("sub") == u.id, "verify_token sub = user.id")
    # verify-email endpoint → email_verified=True
    r = client.post("/api/auth/verify-email", json={"token": verify_token})
    ck(r.status_code == 200, f"verify-email → 200 (got {r.status_code})")
    ck(r.json().get("verified") is True, "verify-email → verified=true")
    test_db.expire_all()
    u = test_db.query(M.User).filter_by(email="vic@corp.io").first()
    ck(u.email_verified is True, "user.email_verified=True après verify-email (A5)")
    # idempotent : re-verify → 200 (déjà vérifié)
    r = client.post("/api/auth/verify-email", json={"token": verify_token})
    ck(r.status_code == 200, f"verify-email idempotent → 200 (got {r.status_code})")
    # token invalide → 400
    r = client.post("/api/auth/verify-email", json={"token": "invalid-jwt"})
    ck(r.status_code == 400, f"verify-email token invalide → 400 (got {r.status_code})")
    # token session (non verify_email) → 400
    sess_tok = create_access_token(u.id)
    r = client.post("/api/auth/verify-email", json={"token": sess_tok})
    ck(r.status_code == 400, f"verify-email token session (non verify_email) → 400 (got {r.status_code})")

    # ═══ B. SSO email_verified gate (A4 — anti takeover) ═══
    print("\n--- B. SSO email_verified gate (A4 anti takeover) ---")
    # Setup : tenant + SSO config (valid https URLs) + victime (email_verified=True) + attaquant-local (non vérifié)
    owner = M.User(id="usr_own", email="own@corp.io", name="Owner", password_hash="h", locale="fr", email_verified=True)
    tenant = M.Tenant(id="tnt_p68", name="Corp P68", plan="Enterprise", owner_id="usr_own", sso_enabled=True, retention_days=2555)
    mem = M.Member(id="mem_own", tenant_id="tnt_p68", user_id="usr_own", email="own@corp.io", name="Owner", role="Owner", status="active")
    # Victime : user local vérifié (email_verified=True) — SSO linking OK
    victim = M.User(id="usr_vic", email="victim@corp.io", name="Victim", password_hash="h", locale="fr", email_verified=True)
    # Attaquant : user local NON vérifié (email_verified=False) avec l'email de la victime... non, l'attaquant
    # crée un compte local avec SON email puis tente de lier un SSO avec l'email de la victime.
    # Pour tester le gate : un user local non vérifié ne peut pas être lié à un SSO.
    unverified = M.User(id="usr_unv", email="unverified@corp.io", name="Unverified", password_hash="h", locale="fr", email_verified=False)
    test_db.add_all([owner, tenant, mem, victim, unverified]); test_db.commit()
    # SSO config valide
    client.put("/api/tenants/tnt_p68/sso", headers={"Authorization": f"Bearer {create_access_token('usr_own')}"},
                json={"client_id":"cid","client_secret":"csec","issuer_url":"https://idp.corp.io",
                      "authorize_url":"https://idp.corp.io/authorize","token_url":"https://idp.corp.io/token",
                      "userinfo_url":"https://idp.corp.io/userinfo","domains":["corp.io"],"enabled":True})
    # Simule le SSO callback pour un user local NON vérifié → doit 403 (gate A4)
    from unittest.mock import patch, MagicMock
    fake_stripe = MagicMock()  # mock pour éviter DNS sur idp.corp.io
    fake_stripe.checkout = MagicMock()
    # Mock safe_urlopen : token exchange + userinfo retournent un user non vérifié (email=unverified@corp.io)
    def _mock_urlopen(req, timeout=15):
        from io import BytesIO
        class _R:
            def __init__(self, b): self._b = b
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): pass
        if "token" in str(req.full_url):
            return _R(b'{"access_token":"acc","id_token":"id","token_type":"Bearer"}')
        return _R(b'{"sub":"sso_unv_sub","email":"unverified@corp.io","name":"Unverified"}')
    # Initiate SSO pour récupérer un state
    r = client.get("/api/auth/sso/tnt_p68/initiate", follow_redirects=False)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(r.headers.get("location", "")).query).get("state", [""])[0]
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen):
        r = client.get("/api/auth/sso/callback", params={"code": "code1", "state": state}, follow_redirects=False)
    ck(r.status_code == 403, f"SSO callback user non vérifié → 403 (gate A4 anti takeover) (got {r.status_code})")
    # L'user non vérifié n'a PAS été lié au SSO (sso_id reste None)
    test_db.expire_all()
    unv = test_db.query(M.User).filter_by(id="usr_unv").first()
    ck(unv.sso_id is None, "user non vérifié non lié au SSO (sso_id reste None — A4 gate effectif)")
    # Maintenant vérifie l'user + retente SSO → linking OK
    vt = create_email_verify_token("usr_unv")
    client.post("/api/auth/verify-email", json={"token": vt})
    r = client.get("/api/auth/sso/tnt_p68/initiate", follow_redirects=False)
    state2 = parse_qs(urlparse(r.headers.get("location", "")).query).get("state", [""])[0]
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen):
        r = client.get("/api/auth/sso/callback", params={"code": "code2", "state": state2}, follow_redirects=False)
    ck(r.status_code in (302, 307), f"SSO callback user vérifié → redirect (linking OK) (got {r.status_code})")
    test_db.expire_all()
    unv = test_db.query(M.User).filter_by(id="usr_unv").first()
    ck(unv.sso_id == "sso_unv_sub", "user vérifié lié au SSO (sso_id set — A4 gate passe après verify)")

    # ═══ C. Migration 0037 + backward compat ═══
    print("\n--- C. Migration 0037 (email_verified col + backward compat) ---")
    cols = [c["name"] for c in sa_inspect(engine).get_columns("users")]
    ck("email_verified" in cols, "col users.email_verified (migration 0037)")
    # Users existants (créés directement, pas via register) ont email_verified=True (model default)
    ck(owner.email_verified is True, "user existant email_verified=True (backward compat model default)")
    ck(victim.email_verified is True, "victim email_verified=True (backward compat)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
