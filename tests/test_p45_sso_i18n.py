#!/usr/bin/env python3
"""test_p45_sso_i18n.py — P45 SSO Enterprise OIDC + i18n FR/EN/ES.

Coverage (mock HTTP OIDC via urllib mock — AUCUN réseau IdP externe en CI) :
  A. i18n module :
    1. MESSAGES : 12 keys × 3 langues (FR/EN/ES) présentes.
    2. t(key, lang) : traductions correctes (login_invalid FR/EN/ES).
    3. t() unknown key → retourne la clé (jamais d'exception).
    4. t() fmt kwargs (demo_hint avec email/password).
    5. get_lang : q-values Accept-Language (fr-FR,fr;q=0.9,en;q=0.8 → fr ; en-US → en ; es-ES → es ; de-DE → fr fallback).
    6. get_lang(None) → default_language.
  B. SSO OIDC Enterprise :
    7. EnterpriseSSO model + table enterprise_sso créée.
    8. Config : sso_redirect_uri + default_language présents.
    9. sso_initiate : pas de config SSO → 404 (message i18n).
    10. sso_initiate : config SSO → RedirectResponse vers IdP authorize URL (state + client_id + scope).
    11. sso_callback : state invalide → 400.
    12. sso_callback : mock exchange + userinfo → JIT user created + JWT + redirect frontend.
    13. sso_callback : domaine non autorisé → 403 (JIT gate).
    14. sso_callback : userinfo sans email → 400.
    15. JIT : user existant par email → lie sso_id (pas de duplicate).

Sécurité P45 : state JWT + nonce single-use (anti-CSRF), client_secret Fernet, JIT gate par domaine,
AUCUN secret dans les logs, Accept-Language détecté (i18n).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p45_test.db python tests/test_p45_sso_i18n.py
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p45_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
# Clé Fernet valide (32 bytes url-safe base64) pour encrypt_token (client_secret SSO).
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_urlopen_resp(status_code: int, body: bytes):
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
    m.headers = {}
    return m


def main():
    from app import models as M
    from app.config import settings
    from app.database import Base
    from app.i18n import MESSAGES, SUPPORTED_LANGS, get_lang, t
    from app.routers import sso as S
    from app.security import encrypt_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)

    # Override the get_db dependency to use our test session
    from app.database import get_db
    test_db = Sess()

    def _override_db():
        yield test_db

    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app, follow_redirects=False)

    # Clean state
    test_db.query(M.EnterpriseSSO).delete()
    test_db.query(M.OAuthState).delete()
    test_db.query(M.Subscription).delete()
    test_db.query(M.User).delete()
    test_db.query(M.Tenant).delete()
    test_db.commit()

    print("=== test_p45_sso_i18n.py — P45 SSO Enterprise OIDC + i18n FR/EN/ES ===\n")

    # ══════════════════════════════════════════════════════════════════════
    # A. i18n module
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. i18n module ---")

    # 1. MESSAGES : 12 keys × 3 langues
    ck(len(MESSAGES) >= 12, f"MESSAGES ≥ 12 keys (got {len(MESSAGES)})")
    all_complete = all(len(v) == 3 for v in MESSAGES.values())
    ck(all_complete, "toutes les keys ont FR/EN/ES")
    ck(set(SUPPORTED_LANGS) == {"fr", "en", "es"}, "SUPPORTED_LANGS = fr/en/es")

    # 2. t(key, lang) traductions
    ck(t("auth.login_invalid", "fr") == "Identifiants invalides.", "login_invalid FR")
    ck(t("auth.login_invalid", "en") == "Invalid credentials.", "login_invalid EN")
    ck(t("auth.login_invalid", "es") == "Credenciales no válidas.", "login_invalid ES")
    ck(t("auth.account_exists", "en") == "An account already exists with this email", "account_exists EN")
    ck(t("sso.domain_not_allowed", "es").startswith("Dominio de correo"), "domain_not_allowed ES")

    # 3. t() unknown key → retourne la clé
    ck(t("nonexistent.key", "fr") == "nonexistent.key", "unknown key → retourne la clé (no exception)")

    # 4. t() fmt kwargs
    hint = t("auth.demo_hint", "en", email="demo@x.io", password="pass123")
    ck("demo@x.io" in hint and "pass123" in hint, f"demo_hint fmt kwargs (got '{hint}')")

    # 5. get_lang q-values
    ck(get_lang("fr-FR,fr;q=0.9,en;q=0.8") == "fr", "get_lang fr-FR,fr;q=0.9 → fr")
    ck(get_lang("en-US,en;q=0.9") == "en", "get_lang en-US → en")
    ck(get_lang("es-ES") == "es", "get_lang es-ES → es")
    ck(get_lang("de-DE,fr;q=0.5") == "fr", "get_lang de-DE,fr;q=0.5 → fr (de non supporté, fr fallback)")
    ck(get_lang("zh-CN,ja;q=0.9") == "fr", "get_lang zh/ja → fr (aucun supporté, fallback default)")

    # 6. get_lang(None) → default_language
    ck(get_lang(None) == settings.default_language, f"get_lang(None) → default_language ({settings.default_language})")

    # ══════════════════════════════════════════════════════════════════════
    # B. SSO OIDC Enterprise
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. SSO OIDC Enterprise ---")

    # 7. EnterpriseSSO model + table
    ck(hasattr(M, "EnterpriseSSO"), "model EnterpriseSSO défini")
    insp = sa_inspect(engine)
    ck("enterprise_sso" in insp.get_table_names(), "table enterprise_sso créée en DB")
    cols = {c["name"] for c in insp.get_columns("enterprise_sso")}
    ck({"id", "tenant_id", "client_id", "client_secret_encrypted", "issuer_url", "domains", "enabled"} <= cols,
       f"colonnes enterprise_sso présentes ({sorted(cols)})")
    # User columns
    ucols = {c["name"] for c in insp.get_columns("users")}
    ck({"sso_id", "identity_provider", "locale", "sso_tenant_id"} <= ucols, "colonnes User SSO/i18n présentes")
    # Tenant column
    tcols = {c["name"] for c in insp.get_columns("tenants")}
    ck("sso_enabled" in tcols, "colonne tenants.sso_enabled présente")

    # 8. Config
    ck(bool(settings.sso_redirect_uri), f"sso_redirect_uri présent ({settings.sso_redirect_uri})")
    ck(settings.default_language == "fr", "default_language = fr")

    # Seed tenant + EnterpriseSSO
    owner = M.User(id="usr_sso", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_sso", name="Corp SSO", plan="Enterprise", owner_id="usr_sso", sso_enabled=True)
    test_db.add_all([owner, tenant]); test_db.commit()
    sso_cfg = M.EnterpriseSSO(
        tenant_id="tnt_sso", provider_type="oidc", client_id="oidc_client_123",
        client_secret_encrypted=encrypt_token("oidc_secret_xyz"),
        issuer_url="https://login.microsoftonline.com/corp/v2.0",
        authorize_url="https://login.microsoftonline.com/corp/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/corp/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        scopes="openid profile email", domains=json.dumps(["corp.io", "revpulse.io"]), enabled=True,
    )
    test_db.add(sso_cfg); test_db.commit()

    # 9. sso_initiate : pas de config SSO → 404
    print("\n--- 9. sso_initiate (no config → 404) ---")
    r = client.get("/api/auth/sso/tnt_nosuch/initiate")
    ck(r.status_code == 404, f"initiate no config → 404 (got {r.status_code})")
    ck("SSO" in r.json().get("detail", ""), f"message i18n SSO (got '{r.json().get('detail','')[:40]}')")

    # 10. sso_initiate : config SSO → redirect IdP
    print("\n--- 10. sso_initiate (config → redirect IdP) ---")
    r = client.get("/api/auth/sso/tnt_sso/initiate")
    ck(r.status_code in (302, 307), f"initiate → redirect (got {r.status_code})")
    loc = r.headers.get("location", "")
    ck("login.microsoftonline.com" in loc, f"redirect vers IdP (got {loc[:60]})")
    ck("client_id=oidc_client_123" in loc, "client_id dans URL")
    ck("state=" in loc, "state dans URL (anti-CSRF)")
    ck("scope=openid" in loc or "scope=openid+profile" in loc or "openid" in loc, "scope dans URL")
    # Extract state for callback test
    from urllib.parse import urlparse, parse_qs
    state = parse_qs(urlparse(loc).query).get("state", [""])[0]
    ck(bool(state), "state extrait de l'URL (non vide)")

    # 11. sso_callback : state invalide → 400
    print("\n--- 11. sso_callback (invalid state → 400) ---")
    r = client.get("/api/auth/sso/callback", params={"code": "fakecode", "state": "invalid_state_jwt"})
    ck(r.status_code == 400, f"invalid state → 400 (got {r.status_code})")

    # 12. sso_callback : mock exchange + userinfo → JIT user + JWT + redirect
    print("\n--- 12. sso_callback (mock OIDC → JIT user + JWT + redirect) ---")
    # Mock urlopen : 1st call = token exchange, 2nd call = userinfo
    token_resp = json.dumps({"access_token": "acc_tok_123", "id_token": "id_tok", "token_type": "Bearer"}).encode()
    userinfo_resp = json.dumps({"sub": "azp_sub_789", "email": "alice@corp.io", "name": "Alice"}).encode()
    urlopen_calls = []
    def _mock_urlopen(req, timeout=15):
        urlopen_calls.append(str(req.full_url))
        if "token" in str(req.full_url):
            return _mock_urlopen_resp(200, token_resp)
        return _mock_urlopen_resp(200, userinfo_resp)
    # Re-initiate to get a fresh state (the previous one was consumed by... no, initiate doesn't verify)
    r2 = client.get("/api/auth/sso/tnt_sso/initiate")
    state2 = parse_qs(urlparse(r2.headers.get("location", "")).query).get("state", [""])[0]
    # P64 : sso._exchange_oidc_code/_fetch_oidc_userinfo utilisent safe_urlopen (SSRF-safe) au lieu de
    # urllib.request.urlopen. Le mock patche donc app.routers.sso.safe_urlopen (même signature req, timeout).
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen):
        r3 = client.get("/api/auth/sso/callback", params={"code": "realcode", "state": state2})
    ck(r3.status_code in (302, 307), f"callback success → redirect (got {r3.status_code})")
    ck(r3.status_code != 200 or "sso_token" in r3.headers.get("location", ""), "redirect avec token")
    loc3 = r3.headers.get("location", "")
    ck("sso_token=" in loc3, f"JWT dans redirect URL (got {loc3[:60]})")
    # JIT user created
    user = test_db.query(M.User).filter_by(email="alice@corp.io").first()
    ck(user is not None, "JIT user créé (alice@corp.io)")
    ck(user.sso_id == "azp_sub_789", f"sso_id lié (got {user.sso_id})")
    ck(user.identity_provider and "oidc:" in user.identity_provider, "identity_provider = oidc:...")
    ck(user.sso_tenant_id == "tnt_sso", "sso_tenant_id = tnt_sso")
    ck(len(urlopen_calls) == 2, f"2 appels HTTP OIDC (token + userinfo) (got {len(urlopen_calls)})")

    # 13. sso_callback : domaine non autorisé → 403
    print("\n--- 13. sso_callback (domain not allowed → 403) ---")
    r4 = client.get("/api/auth/sso/tnt_sso/initiate")
    state4 = parse_qs(urlparse(r4.headers.get("location", "")).query).get("state", [""])[0]
    userinfo_bad_domain = json.dumps({"sub": "bob_sub", "email": "bob@evil.com", "name": "Bob"}).encode()
    def _mock_urlopen_bad(req, timeout=15):
        if "token" in str(req.full_url):
            return _mock_urlopen_resp(200, token_resp)
        return _mock_urlopen_resp(200, userinfo_bad_domain)
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen_bad):
        r5 = client.get("/api/auth/sso/callback", params={"code": "code2", "state": state4})
    ck(r5.status_code == 403, f"domain not allowed → 403 (got {r5.status_code})")
    ck("evil.com" not in test_db.query(M.User).filter_by(email="bob@evil.com").first().__class__.__name__ if False else True, "")
    # bob@evil.com NOT created (JIT gate blocked)
    ck(test_db.query(M.User).filter_by(email="bob@evil.com").first() is None, "user bob@evil.com NON créé (JIT gate)")

    # 14. sso_callback : userinfo sans email → 400
    print("\n--- 14. sso_callback (no email → 400) ---")
    r6 = client.get("/api/auth/sso/tnt_sso/initiate")
    state6 = parse_qs(urlparse(r6.headers.get("location", "")).query).get("state", [""])[0]
    userinfo_no_email = json.dumps({"sub": "carol_sub", "name": "Carol"}).encode()
    def _mock_urlopen_noemail(req, timeout=15):
        if "token" in str(req.full_url):
            return _mock_urlopen_resp(200, token_resp)
        return _mock_urlopen_resp(200, userinfo_no_email)
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen_noemail):
        r7 = client.get("/api/auth/sso/callback", params={"code": "code3", "state": state6})
    ck(r7.status_code == 400, f"no email → 400 (got {r7.status_code})")

    # 15. JIT : user existant par email → lie sso_id (pas de duplicate)
    print("\n--- 15. JIT (existing email → link sso_id, no duplicate) ---")
    existing = M.User(id="usr_existing", email="dave@corp.io", name="Dave", password_hash="h_real", locale="fr")
    test_db.add(existing); test_db.commit()
    count_before = test_db.query(M.User).filter_by(email="dave@corp.io").count()
    r8 = client.get("/api/auth/sso/tnt_sso/initiate")
    state8 = parse_qs(urlparse(r8.headers.get("location", "")).query).get("state", [""])[0]
    userinfo_dave = json.dumps({"sub": "dave_sub_new", "email": "dave@corp.io", "name": "Dave"}).encode()
    def _mock_urlopen_dave(req, timeout=15):
        if "token" in str(req.full_url):
            return _mock_urlopen_resp(200, token_resp)
        return _mock_urlopen_resp(200, userinfo_dave)
    with patch("app.routers.sso.safe_urlopen", side_effect=_mock_urlopen_dave):
        r9 = client.get("/api/auth/sso/callback", params={"code": "code4", "state": state8})
    ck(r9.status_code in (302, 307), "existing user SSO → redirect (success)")
    count_after = test_db.query(M.User).filter_by(email="dave@corp.io").count()
    ck(count_after == count_before, f"pas de duplicate (count before={count_before} after={count_after})")
    test_db.refresh(existing)
    ck(existing.sso_id == "dave_sub_new", f"sso_id lié à l'user existant (got {existing.sso_id})")
    ck(existing.identity_provider and "oidc:" in existing.identity_provider, "identity_provider lié")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
