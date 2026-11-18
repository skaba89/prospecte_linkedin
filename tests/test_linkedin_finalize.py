#!/usr/bin/env python3
"""test_linkedin_finalize.py — LinkedIn finalisation (revoke serveur + expiry pre-flight + member_id JWT fallback + config externalisée).

Coverage (mock HTTP via unittest.mock — AUCUN réseau externe en CI) :
  1. Config externalisée : 8 settings LinkedIn (authorize/token/revoke/userinfo/ugc_post/comment/scopes) présents + non-vides.
  2. _revoke_linkedin_token : mock urlopen HTTP 200 → True (LinkedIn ACK).
  3. _revoke_linkedin_token : mock network error → False (best-effort, n'explode pas).
  4. _extract_sub_from_jwt : JWT valide → sub (member_id).
  5. _extract_sub_from_jwt : token invalide/non-JWT → "".
  6. _publish_post : mock ugcPosts → dict avec id (URN).
  7. _publish_comment : mock socialActions → dict avec id.
  8. Disconnect route : seed LinkedInConnection + mock revoke → DB row supprimée + revoke appelée + retour server_side_revoked=True.
  9. _SCOPES + _REVOKE_URL lisent depuis settings (patch settings → constantes reflètent).

Sécurité (P-linkedin-finalize) : revoke côté serveur LinkedIn au disconnect (invalide access_token),
state OAuth anti-CSRF (nonce single-use), tokens Fernet chiffrés, pre-flight expiry anti-401 aveugle.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_linkedin_finalize_test.db python tests/test_linkedin_finalize.py
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_linkedin_finalize_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_resp(status_code: int, body: bytes, headers: dict | None = None):
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
    m.headers = headers or {}
    return m


def _craft_jwt(sub: str = "mem_123", exp: int = 9999999999) -> str:
    """Craft a fake LinkedIn-style JWT (header.payload.signature) with sub claim."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": exp, "iss": "linkedin"}).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
    return f"{header}.{payload}.{sig}"


def main():
    from app import models as M
    from app.config import settings
    from app.database import Base
    from app.routers import linkedin as L
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()

    # Clean state
    db.query(M.LinkedInConnection).delete()
    db.query(M.OAuthState).delete()
    db.query(M.Tenant).delete()
    db.query(M.User).delete()
    db.commit()

    print("=== test_linkedin_finalize.py — LinkedIn finalisation (revoke + expiry + JWT fallback + config) ===\n")

    # ── 1. Config externalisée (8 settings) ───────────────────────────────
    print("--- 1. Config externalisée (8 settings LinkedIn) ---")
    ck(bool(settings.linkedin_authorize_url), f"linkedin_authorize_url présent ({settings.linkedin_authorize_url[:40]})")
    ck(bool(settings.linkedin_token_url), "linkedin_token_url présent")
    ck(bool(settings.linkedin_revoke_url), f"linkedin_revoke_url présent ({settings.linkedin_revoke_url})")
    ck(bool(settings.linkedin_userinfo_url), "linkedin_userinfo_url présent")
    ck(bool(settings.linkedin_ugc_post_url), "linkedin_ugc_post_url présent")
    ck(bool(settings.linkedin_comment_url_tmpl), "linkedin_comment_url_tmpl présent")
    ck(settings.linkedin_scopes == "openid profile w_member_social", f"linkedin_scopes correct ({settings.linkedin_scopes})")
    # Constantes module reflètent les settings
    ck(L._REVOKE_URL == settings.linkedin_revoke_url, "_REVOKE_URL == settings.linkedin_revoke_url")
    ck(L._SCOPES == settings.linkedin_scopes, "_SCOPES == settings.linkedin_scopes")
    ck(L._AUTHORIZE_URL == settings.linkedin_authorize_url, "_AUTHORIZE_URL == settings.linkedin_authorize_url")

    # ── 2. _revoke_linkedin_token (mock HTTP 200 → True) ──────────────────
    print("\n--- 2. _revoke_linkedin_token (HTTP 200 → True) ---")
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, b"")):
        revoked = L._revoke_linkedin_token("li_access_token_123")
    ck(revoked is True, "revoke HTTP 200 → True")

    # ── 3. _revoke_linkedin_token (network error → False, best-effort) ────
    print("\n--- 3. _revoke_linkedin_token (network error → False) ---")
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        revoked = L._revoke_linkedin_token("li_access_token_123")
    ck(revoked is False, "revoke network error → False (best-effort, n'explode pas)")

    # ── 4. _extract_sub_from_jwt (JWT valide → sub) ───────────────────────
    print("\n--- 4. _extract_sub_from_jwt (JWT valide → sub) ---")
    jwt_tok = _craft_jwt(sub="mem_ABC999")
    sub = L._extract_sub_from_jwt(jwt_tok)
    ck(sub == "mem_ABC999", f"sub extrait du JWT (got {sub})")

    # ── 5. _extract_sub_from_jwt (token invalide → "") ────────────────────
    print("\n--- 5. _extract_sub_from_jwt (token invalide → '') ---")
    ck(L._extract_sub_from_jwt("not.a.jwt") == "", "token non-JWT → '' (best-effort)")
    ck(L._extract_sub_from_jwt("plain_token_no_dots") == "", "token sans dots → ''")
    ck(L._extract_sub_from_jwt("") == "", "token vide → ''")

    # ── 6. _publish_post (mock ugcPosts → id) ─────────────────────────────
    print("\n--- 6. _publish_post (mock ugcPosts → id) ---")
    ugc_resp = json.dumps({"id": "urn:li:ugcPost:123456", "activity": "urn:li:activity:123456"}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_resp(201, ugc_resp, headers={"Location": "urn:li:ugcPost:123456"})):
        result = L._publish_post("li_token", "mem_123", "Hello LinkedIn!")
    ck(result.get("id") == "urn:li:ugcPost:123456", f"publish_post → id URN (got {result.get('id')})")

    # ── 7. _publish_comment (mock socialActions → id) ─────────────────────
    print("\n--- 7. _publish_comment (mock socialActions → id) ---")
    comment_resp = json.dumps({"id": "urn:li:comment:(123,456)"}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_resp(201, comment_resp, headers={"Location": "urn:li:comment:(123,456)"})):
        result = L._publish_comment("li_token", "urn:li:activity:123", "Great post!")
    ck(result.get("id") == "urn:li:comment:(123,456)", f"publish_comment → id URN (got {result.get('id')})")

    # ── 8. Disconnect route (integration : revoke + delete DB) ────────────
    print("\n--- 8. Disconnect route (revoke serveur + delete DB) ---")
    # Seed tenant + LinkedInConnection avec token plaintext (mode dev sans clé Fernet)
    u = M.User(id="usr_li", email="li@test.com", name="LI User", password_hash="h")
    t = M.Tenant(id="tnt_li", name="LI Tenant", plan="free", owner_id="usr_li")
    db.add_all([u, t]); db.commit()
    conn = M.LinkedInConnection(
        tenant_id="tnt_li", access_token="li_real_access_token_xyz",
        expires_at=datetime.utcnow() + timedelta(days=50),
        member_id="mem_real_001", member_name="Real Member",
        member_headline="CTO", scope="openid profile w_member_social",
    )
    db.add(conn); db.commit()
    # Mock _perm (require_permission retourne un objet avec user_id + name). SimpleNamespace évite le gotcha MagicMock.name.
    _perm = SimpleNamespace(user_id="usr_li", name="LI User", tenant_id="tnt_li")
    # Mock urlopen pour le revoke (HTTP 200)
    revoke_called = {"count": 0}
    def _capture_revoke(req, timeout=10):
        revoke_called["count"] += 1
        # Vérifier que l'URL est bien le revoke endpoint
        assert "revoke" in str(req.full_url), f"revoke URL attendue, got {req.full_url}"
        return _mock_resp(200, b"")
    with patch("urllib.request.urlopen", side_effect=_capture_revoke):
        result = L.linkedin_disconnect(tid="tnt_li", db=db, _t=t, _perm=_perm)
    ck(result["disconnected"] == "tnt_li", "disconnect retourne disconnected=tnt_li")
    ck(result.get("server_side_revoked") is True, "disconnect retourne server_side_revoked=True")
    ck(revoke_called["count"] == 1, f"revoke appelé 1 fois (got {revoke_called['count']})")
    # DB row supprimée
    ck(db.query(M.LinkedInConnection).filter_by(tenant_id="tnt_li").first() is None, "LinkedInConnection supprimée de DB")

    # ── 9. Disconnect avec token mock (pas de revoke appelé) ──────────────
    print("\n--- 9. Disconnect avec token mock (revoke SKIPPÉ) ---")
    conn2 = M.LinkedInConnection(
        tenant_id="tnt_li", access_token=L._MOCK_TOKEN,
        expires_at=datetime.utcnow() + timedelta(days=50),
        member_id="mock-member", member_name="Mock",
    )
    db.add(conn2); db.commit()
    revoke_called2 = {"count": 0}
    def _no_revoke(req, timeout=10):
        revoke_called2["count"] += 1
        return _mock_resp(200, b"")
    with patch("urllib.request.urlopen", side_effect=_no_revoke):
        result = L.linkedin_disconnect(tid="tnt_li", db=db, _t=t, _perm=_perm)
    ck(result["disconnected"] == "tnt_li", "disconnect mock token → disconnected")
    ck(revoke_called2["count"] == 0, "revoke NON appelé pour token mock (dev/CI)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
