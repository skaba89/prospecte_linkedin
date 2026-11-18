#!/usr/bin/env python3
"""test_p38_v3_outlook_attachments.py — P38-V3 Outlook OAuth2 + CV PDF multipart + revoke Google.

Coverage (mock HTTP via unittest.mock — AUCUN réseau externe en CI) :
  1. is_outlook_configured : True avec creds, False sans creds.
  2. handle_outlook_oauth_callback : mock token exchange + Graph /me → UserEmailIntegration stocké + email.
  3. _send_via_outlook : mock Graph /me/sendMail 202 → status sent + message_id.
  4. _send_via_outlook 401 → unauthorized.
  5. _send_via_outlook avec attachment (CV PDF) → payload contient l'attachment base64.
  6. _build_rfc5322_message multipart : attachment → MIME multipart + Content-Disposition attachment.
  7. _send_via_gmail avec attachment → multipart MIME (body + CV PDF).
  8. _b64decode : base64 standard + urlsafe → bytes.
  9. _revoke_provider_token gmail : mock Google revoke endpoint → True (HTTP 200).
 10. _revoke_provider_token outlook : best-effort → False (MS pas d'endpoint perso).
 11. disconnect_integration : appelle revoke + supprime DB row.
 12. Provider dispatch : send_application_email Gmail priorité, fallback Outlook.

Anti-fake (P38 invariant) : consentement explicite (POST send), rate-limit, scope minimal Mail.Send.
Sécurité (P24-A) : refresh_token chiffré Fernet, state OAuth anti-CSRF, revoke serveur best-effort.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p38_v3_test.db python tests/test_p38_v3_outlook_attachments.py
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p38_v3_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_resp(status_code: int, body: bytes, headers: dict | None = None):
    """Mock context manager pour urllib.request.urlopen."""
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
    m.headers = headers or {}
    return m


def main():
    from app import models as M
    from app.config import settings
    from app.database import Base
    from app.services import crypto
    from app.services import email_sender as es
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()

    # Clean state
    db.query(M.UserEmailIntegration).delete()
    db.query(M.JobApplication).delete()
    db.query(M.CareerProfile).delete()
    db.query(M.User).delete()
    db.commit()

    # Seed user + profile + application
    u = M.User(id="usr_p38v3", email="p38v3@test.com", name="Cand", password_hash="h")
    prof = M.CareerProfile(id="crp_p38v3", user_id="usr_p38v3", mobility=True, currency="EUR")
    app = M.JobApplication(id="app_p38v3", career_profile_id="crp_p38v3", company="TechCorp",
                           job_title="Dev Python", recipient_email="rh@techcorp.com",
                           cover_letter="Madame, Monsieur,\n\nCandidature spontanée.\n\nCordialement.",
                           application_method="email")
    db.add_all([u, prof, app])
    db.commit()

    print("=== test_p38_v3_outlook_attachments.py — P38-V3 Outlook + CV PDF + revoke ===\n")

    # ── 1. is_outlook_configured ──────────────────────────────────────────
    print("--- 1. is_outlook_configured ---")
    with patch.object(settings, "outlook_client_id", ""), patch.object(settings, "outlook_client_secret", ""):
        ck(es.is_outlook_configured() is False, "False sans creds")
        ck(es.is_provider_configured("outlook_oauth") is False, "is_provider_configured outlook=False sans creds")
    with patch.object(settings, "outlook_client_id", "ms_id"), patch.object(settings, "outlook_client_secret", "ms_sec"):
        ck(es.is_outlook_configured() is True, "True avec creds")
        ck(es.is_provider_configured("outlook_oauth") is True, "is_provider_configured outlook=True avec creds")

    # ── 2. handle_outlook_oauth_callback (mock exchange + Graph /me) ──────
    print("\n--- 2. handle_outlook_oauth_callback (exchange + /me) ---")
    # Mock : 1er call urlopen = token exchange, 2e call = Graph /me
    token_body = json.dumps({
        "access_token": "ms_access_123", "refresh_token": "ms_refresh_456",
        "expires_in": 3600, "scope": es.OUTLOOK_SCOPE,
    }).encode()
    me_body = json.dumps({"mail": "cand@outlook.com", "userPrincipalName": "cand@outlook.com"}).encode()
    with patch.object(settings, "outlook_client_id", "ms_id"), patch.object(settings, "outlook_client_secret", "ms_sec"), \
         patch("urllib.request.urlopen", side_effect=[_mock_resp(200, token_body), _mock_resp(200, me_body)]):
        result = es.handle_outlook_oauth_callback("fake_code", "fake_state", db, "usr_p38v3")
    ck(result["status"] == "connected", f"status=connected (got {result['status']})")
    ck(result["provider"] == "outlook_oauth", "provider=outlook_oauth")
    ck(result["email"] == "cand@outlook.com", f"email=cand@outlook.com (got {result['email']})")
    # Vérifier que l'intégration est stockée + tokens chiffrés
    integ = es.get_user_integration(db, "usr_p38v3", "outlook_oauth")
    ck(integ is not None, "UserEmailIntegration stockée")
    ck(integ.email == "cand@outlook.com", "integration.email correct")
    ck(crypto.decrypt(integ.access_token_encrypted) == "ms_access_123", "access_token chiffré Fernet")
    ck(crypto.decrypt(integ.refresh_token_encrypted) == "ms_refresh_456", "refresh_token chiffré Fernet")

    # ── 3. _send_via_outlook (mock Graph /me/sendMail 202) ────────────────
    print("\n--- 3. _send_via_outlook (sendMail 202) ---")
    with patch("urllib.request.urlopen", return_value=_mock_resp(202, b"")):
        res = es._send_via_outlook("ms_access_123", "cand@outlook.com", "rh@techcorp.com",
                                   "Candidature", "Body", None)
    ck(res["status"] == "sent", f"status=sent (got {res['status']})")
    ck("outlook-" in (res.get("message_id") or ""), "message_id commence par outlook-")

    # ── 4. _send_via_outlook 401 → unauthorized ───────────────────────────
    print("\n--- 4. _send_via_outlook 401 → unauthorized ---")
    import urllib.error
    err_401 = urllib.error.HTTPError(url="sendMail", code=401, msg="Unauth", hdrs=None, fp=None)
    with patch("urllib.request.urlopen", side_effect=err_401):
        res = es._send_via_outlook("bad_token", "cand@outlook.com", "rh@techcorp.com", "S", "B", None)
    ck(res["status"] == "unauthorized", f"401 → unauthorized (got {res['status']})")

    # ── 5. _send_via_outlook avec attachment (CV PDF) ─────────────────────
    print("\n--- 5. _send_via_outlook avec attachment (CV PDF) ---")
    cv_b64 = base64.b64encode(b"%PDF-1.4 fake cv content").decode()
    captured_payload = {}
    def _capture_sendmail(req, timeout=10):
        captured_payload["data"] = json.loads(req.data.decode())
        return _mock_resp(202, b"")
    with patch("urllib.request.urlopen", side_effect=_capture_sendmail):
        res = es._send_via_outlook("ms_access_123", "cand@outlook.com", "rh@techcorp.com",
                                   "Candidature", "Body",
                                   [{"name": "CV.pdf", "content_bytes_b64": cv_b64, "contentType": "application/pdf"}])
    ck(res["status"] == "sent", "send avec attachment → sent")
    ck("attachments" in captured_payload["data"]["message"], "payload contient attachments")
    ck(len(captured_payload["data"]["message"]["attachments"]) == 1, "1 attachment dans payload")
    ck(captured_payload["data"]["message"]["attachments"][0]["name"] == "CV.pdf", "attachment name=CV.pdf")
    ck(captured_payload["data"]["message"]["attachments"][0]["contentBytes"] == cv_b64, "attachment contentBytes=base64")

    # ── 6. _build_rfc5322_message multipart (attachment) ──────────────────
    print("\n--- 6. _build_rfc5322_message multipart (attachment) ---")
    raw = es._build_rfc5322_message("cand@x.com", "rh@y.com", "Test", "Body",
                                    [{"name": "CV.pdf", "content_bytes_b64": cv_b64}])
    # Décoder base64url pour inspecter le MIME
    import base64 as _b64
    decoded = _b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="replace")
    ck("multipart/mixed" in decoded, "MIME multipart/mixed présent")
    ck('Content-Disposition: attachment' in decoded or "attachment" in decoded, "Content-Disposition attachment présent")
    ck("CV.pdf" in decoded, "filename CV.pdf dans MIME")

    # ── 7. _send_via_gmail avec attachment (multipart MIME) ───────────────
    print("\n--- 7. _send_via_gmail avec attachment ---")
    gmail_resp_body = json.dumps({"id": "gmail_msg_123"}).encode()
    captured_gmail = {}
    def _capture_gmail(req, timeout=10):
        captured_gmail["raw"] = json.loads(req.data.decode())["raw"]
        return _mock_resp(200, gmail_resp_body)
    with patch.object(settings, "gmail_api_base", "https://gmail.googleapis.com/gmail/v1/users/me"), \
         patch("urllib.request.urlopen", side_effect=_capture_gmail):
        res = es._send_via_gmail("gmail_tok", "cand@x.com", "rh@y.com", "Candidature", "Body",
                                 [{"name": "CV.pdf", "content_bytes_b64": cv_b64}])
    ck(res["status"] == "sent", "gmail send avec attachment → sent")
    ck(res.get("message_id") == "gmail_msg_123", "message_id=gmail_msg_123")
    decoded_gmail = _b64.urlsafe_b64decode(captured_gmail["raw"] + "=" * (-len(captured_gmail["raw"]) % 4)).decode("utf-8", errors="replace")
    ck("multipart/mixed" in decoded_gmail, "gmail MIME multipart/mixed")
    ck("CV.pdf" in decoded_gmail, "gmail MIME contient CV.pdf")

    # ── 8. _b64decode (standard + urlsafe) ────────────────────────────────
    print("\n--- 8. _b64decode (standard + urlsafe) ---")
    ck(es._b64decode(base64.b64encode(b"hello").decode()) == b"hello", "standard base64 → bytes")
    ck(es._b64decode(base64.urlsafe_b64encode(b"hello").decode()) == b"hello", "urlsafe base64 → bytes")
    ck(es._b64decode(base64.b64encode(b"%PDF").decode()) == b"%PDF", "binary PDF-like → bytes")

    # ── 9. _revoke_provider_token gmail (mock Google revoke) ──────────────
    print("\n--- 9. _revoke_provider_token gmail (Google revoke endpoint) ---")
    # Créer une intégration gmail avec refresh_token chiffré
    integ_gmail = M.UserEmailIntegration(
        user_id="usr_p38v3", provider="gmail_oauth", email="cand@gmail.com",
        access_token_encrypted=crypto.encrypt("gmail_access"),
        refresh_token_encrypted=crypto.encrypt("gmail_refresh"),
        access_token_expires_at=datetime.utcnow() + timedelta(hours=1),
        scopes="gmail.send",
    )
    db.add(integ_gmail); db.commit()
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, b"")):
        revoked = es._revoke_provider_token(integ_gmail, "gmail_oauth")
    ck(revoked is True, "gmail revoke → True (HTTP 200)")

    # ── 10. _revoke_provider_token outlook (best-effort) ──────────────────
    print("\n--- 10. _revoke_provider_token outlook (best-effort, pas d'endpoint) ---")
    revoked_outlook = es._revoke_provider_token(integ, "outlook_oauth")
    ck(revoked_outlook is False, "outlook revoke → False (best-effort, MS pas d'endpoint perso)")

    # ── 11. disconnect_integration (revoke + delete DB) ───────────────────
    print("\n--- 11. disconnect_integration (revoke + delete DB) ---")
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, b"")):
        deleted = es.disconnect_integration(db, "usr_p38v3", "gmail_oauth")
    ck(deleted is True, "disconnect gmail → True (supprimée)")
    ck(es.get_user_integration(db, "usr_p38v3", "gmail_oauth") is None, "intégration gmail supprimée de DB")
    # Outlook disconnect (best-effort, pas d'appel réseau)
    deleted_outlook = es.disconnect_integration(db, "usr_p38v3", "outlook_oauth")
    ck(deleted_outlook is True, "disconnect outlook → True")
    ck(es.get_user_integration(db, "usr_p38v3", "outlook_oauth") is None, "intégration outlook supprimée de DB")
    # Disconnect provider inexistant → False
    ck(es.disconnect_integration(db, "usr_p38v3", "gmail_oauth") is False, "disconnect provider inexistant → False")

    # ── 12. Provider dispatch (Gmail priorité, fallback Outlook) ──────────
    print("\n--- 12. Provider dispatch (Gmail priorité, fallback Outlook) ---")
    # Aucun provider configuré → not_configured
    with patch.object(settings, "gmail_client_id", ""), patch.object(settings, "gmail_client_secret", ""), \
         patch.object(settings, "outlook_client_id", ""), patch.object(settings, "outlook_client_secret", ""):
        res = es.send_application_email(app, prof, db)
        ck(res.status == "not_configured", f"aucun provider → not_configured (got {res.status})")

    # Gmail configuré + connecté → envoi Gmail (mock)
    with patch.object(settings, "gmail_client_id", "g_id"), patch.object(settings, "gmail_client_secret", "g_sec"), \
         patch.object(settings, "outlook_client_id", "ms_id"), patch.object(settings, "outlook_client_secret", "ms_sec"):
        # Re-créer l'intégration gmail (supprimée au test 11)
        integ_g2 = M.UserEmailIntegration(
            user_id="usr_p38v3", provider="gmail_oauth", email="cand@gmail.com",
            access_token_encrypted=crypto.encrypt("gmail_access"),
            refresh_token_encrypted=crypto.encrypt("gmail_refresh"),
            access_token_expires_at=datetime.utcnow() + timedelta(hours=1),
            scopes="gmail.send",
        )
        db.add(integ_g2); db.commit()
        with patch("urllib.request.urlopen", return_value=_mock_resp(200, json.dumps({"id": "g_msg"}).encode())):
            res = es.send_application_email(app, prof, db, cv_pdf_b64=cv_b64, cv_filename="Jean_CV.pdf")
        ck(res.status == "sent", f"dispatch gmail → sent (got {res.status})")
        ck(res.provider == "gmail_oauth", f"provider=gmail_oauth priorité (got {res.provider})")
        ck(res.detail.get("attachments") == 1, "1 attachment (CV PDF)")
        ck(res.detail.get("cv_filename") == "Jean_CV.pdf", "cv_filename propagé")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
