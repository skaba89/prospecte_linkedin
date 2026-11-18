#!/usr/bin/env python3
"""test_p38_v2_email_oauth.py — P38-V2 envoi candidatures réel (OAuth2 Gmail authorization code flow).

Coverage (mock HTTP via unittest.mock — AUCUN réseau externe en CI) :
  1. not_configured : app Gmail non configurée → send_application_email status "not_configured".
  2. is_gmail_configured + get_gmail_oauth_url : consent URL avec scope gmail.send + state.
  3. handle_gmail_oauth_callback : mock token exchange + tokeninfo → UserEmailIntegration stocké
     (refresh_token + access_token chiffrés Fernet, email récupéré, upsert).
  4. _get_valid_access_token : token valide (cache) / token expiré → refresh → new token.
  5. send_application_email success : mock refresh + Gmail API send → status "sent" + message_id
     + app.sent_at + application_method=email + timeline.
  6. send_application_email not_connected : app configurée, user sans intégration → not_connected.
  7. send_application_email unauthorized : refresh échec → unauthorized + intégration supprimée.
  8. _build_rfc5322_message : base64url avec From/To/Subject (RFC 5322).
  9. Persistence : disconnect_integration, list_user_integrations, tokens chiffrés (pas en clair).
 10. Rate-limit : DAILY_SEND_LIMIT (50/jour) — anti-abus.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p38_test.db python tests/test_p38_v2_email_oauth.py
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
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p38_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def _mock_resp(status_code: int, body: bytes):
    """Mock context manager pour urllib.request.urlopen (resp.status + resp.read())."""
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = status_code
    m.read = MagicMock(return_value=body)
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
    u = M.User(id="usr_p38", email="p38@test.com", name="Cand", password_hash="h")
    prof = M.CareerProfile(id="crp_p38", user_id="usr_p38", mobility=True, currency="EUR")
    app = M.JobApplication(id="app_p38", career_profile_id="crp_p38", company="TechCorp",
                           job_title="Dev Python", recipient_email="rh@techcorp.com",
                           cover_letter="Madame, Monsieur,\n\nCandidature spontanée.\n\nCordialement.",
                           application_method="email")
    db.add_all([u, prof, app])
    db.commit()

    print("=== test_p38_v2_email_oauth.py — P38-V2 OAuth2 Gmail ===\n")

    # ── 1. not_configured (app Gmail non configurée) ───────────────────────
    print("--- 1. not_configured (app Gmail absente) ---")
    with patch.object(settings, "gmail_client_id", ""), patch.object(settings, "gmail_client_secret", ""):
        ck(es.is_gmail_configured() is False, "is_gmail_configured=False sans creds")
        res = es.send_application_email(app, prof, db)
        ck(res.status == "not_configured", f"status=not_configured (got {res.status})")
        ck(app.sent_at is None, "sent_at reste None (pas envoyé)")
        ck("GMAIL_CLIENT_ID/SECRET absents" in (res.error or ""), "error mentionne creds absents")

    # ── 2. is_gmail_configured + get_gmail_oauth_url ───────────────────────
    print("\n--- 2. consent URL Gmail ---")
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"):
        ck(es.is_gmail_configured() is True, "is_gmail_configured=True avec creds")
        url = es.get_gmail_oauth_url("nonce_abc123")
        ck("accounts.google.com/o/oauth2/v2/auth" in url, f"URL consent Google (got {url[:60]})")
        ck("client_id=test_gcid" in url, "client_id dans URL")
        ck("scope=" in url and "gmail.send" in url, "scope gmail.send dans URL")
        ck("response_type=code" in url, "response_type=code (authorization code flow)")
        ck("access_type=offline" in url, "access_type=offline (refresh_token)")
        ck("state=nonce_abc123" in url, "state anti-CSRF dans URL")

    # ── 3. handle_gmail_oauth_callback (exchange + tokeninfo + store) ─────
    print("\n--- 3. oauth callback (exchange + store chiffré) ---")
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"):
        token_body = json.dumps({
            "access_token": "ya29.acc_tok", "refresh_token": "1//refresh_long_lived",
            "expires_in": 3600, "token_type": "Bearer", "scope": es.GMAIL_SCOPE,
        }).encode()
        tokeninfo_body = json.dumps({"email": "candidat@gmail.com", "scope": es.GMAIL_SCOPE}).encode()

        def _cb_side(req, **kw):
            # POST token exchange (Request avec data) vs GET tokeninfo (string URL sans .data)
            if hasattr(req, "data") and req.data:
                return _mock_resp(200, token_body)
            return _mock_resp(200, tokeninfo_body)  # tokeninfo GET

        with patch("urllib.request.urlopen", side_effect=_cb_side):
            result = es.handle_gmail_oauth_callback("code_xyz", "state_ok", db, "usr_p38")
        ck(result["status"] == "connected", f"status=connected (got {result['status']})")
        ck(result["email"] == "candidat@gmail.com", f"email récupéré (got {result['email']})")
        # Vérifier intégration stockée en DB
        integ = es.get_user_integration(db, "usr_p38", "gmail_oauth")
        ck(integ is not None, "UserEmailIntegration stockée en DB")
        ck(integ.email == "candidat@gmail.com", "email stocké")
        ck(integ.refresh_token_encrypted is not None, "refresh_token_encrypted présent")
        ck(integ.access_token_encrypted is not None, "access_token_encrypted présent")
        # Anti-fuite : refresh_token + access_token JAMAIS en clair en DB (chiffrés Fernet)
        ck("1//refresh_long_lived" not in (integ.refresh_token_encrypted or ""),
           "refresh_token CHIFFRÉ en DB (pas en clair)")
        ck("ya29.acc_tok" not in (integ.access_token_encrypted or ""),
           "access_token CHIFFRÉ en DB (pas en clair)")
        # Déchiffrement OK (récupère le plaintext)
        ck(crypto.decrypt(integ.refresh_token_encrypted) == "1//refresh_long_lived",
           "decrypt(refresh_token) récupère le plaintext")
        ck(crypto.decrypt(integ.access_token_encrypted) == "ya29.acc_tok",
           "decrypt(access_token) récupère le plaintext")
        ck(integ.access_token_expires_at is not None, "expires_at stocké")

    # ── 3b. upsert (re-connect met à jour l'intégration existante) ─────────
    print("\n--- 3b. upsert (re-connect met à jour) ---")
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"):
        token_body2 = json.dumps({
            "access_token": "ya29.new_acc", "refresh_token": "1//new_refresh",
            "expires_in": 3600, "scope": es.GMAIL_SCOPE,
        }).encode()
        tokeninfo_body2 = json.dumps({"email": "candidat@gmail.com"}).encode()
        with patch("urllib.request.urlopen", side_effect=lambda req, **kw: _mock_resp(
            200, token_body2 if (hasattr(req, "data") and req.data) else tokeninfo_body2)):
            es.handle_gmail_oauth_callback("code_xyz2", "state_ok2", db, "usr_p38")
        count = db.query(M.UserEmailIntegration).filter_by(user_id="usr_p38", provider="gmail_oauth").count()
        ck(count == 1, f"1 seule intégration (upsert, pas de doublon) — got {count}")
        integ = es.get_user_integration(db, "usr_p38", "gmail_oauth")
        ck(crypto.decrypt(integ.access_token_encrypted) == "ya29.new_acc", "access_token mis à jour (upsert)")

    # ── 4. _get_valid_access_token (cache + refresh) ──────────────────────
    print("\n--- 4. access token (cache + refresh si expiré) ---")
    integ = es.get_user_integration(db, "usr_p38", "gmail_oauth")
    # Token encore valide (expires_at dans le futur) → pas de refresh
    integ.access_token_expires_at = datetime.utcnow() + timedelta(seconds=600)
    db.commit()
    tok = es._get_valid_access_token(integ, db)
    ck(tok == "ya29.new_acc", "token valide → cache (pas de refresh)")
    # Token expiré → refresh
    integ.access_token_expires_at = datetime.utcnow() - timedelta(seconds=10)
    db.commit()
    refresh_body = json.dumps({"access_token": "ya29.refreshed", "expires_in": 3600}).encode()
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"), \
         patch("urllib.request.urlopen", return_value=_mock_resp(200, refresh_body)):
        tok = es._get_valid_access_token(integ, db)
    ck(tok == "ya29.refreshed", f"token expiré → refresh → nouveau token (got {tok[:20]})")
    integ = es.get_user_integration(db, "usr_p38", "gmail_oauth")
    ck(integ.access_token_expires_at > datetime.utcnow(), "expires_at mis à jour après refresh")

    # ── 5. send_application_email success ─────────────────────────────────
    print("\n--- 5. send success (Gmail API) ---")
    # Reset app sent_at
    app.sent_at = None
    app.application_method = "email"
    db.commit()
    # Token valide (pas de refresh)
    integ.access_token_expires_at = datetime.utcnow() + timedelta(seconds=600)
    integ.access_token_encrypted = crypto.encrypt("ya29.send_tok")
    db.commit()
    gmail_send_body = json.dumps({"id": "msg_id_abc", "threadId": "t1"}).encode()
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"), \
         patch("urllib.request.urlopen", return_value=_mock_resp(200, gmail_send_body)):
        res = es.send_application_email(app, prof, db)
    ck(res.status == "sent", f"status=sent (got {res.status})")
    ck(res.provider == "gmail_oauth", "provider=gmail_oauth")
    ck(res.message_id == "msg_id_abc", f"message_id Gmail (got {res.message_id})")
    ck(res.sent_at is not None, "sent_at renseigné")
    ck(app.sent_at is not None, "app.sent_at mis à jour en DB")
    ck(app.application_method == "email", "application_method=email")
    ck(app.status == "submitted", f"status=submitted (got {app.status})")

    # ── 6. send not_connected (app configurée, user sans intégration) ──────
    print("\n--- 6. not_connected (user sans Gmail connecté) ---")
    # Supprimer l'intégration → user n'a plus de Gmail connecté
    es.disconnect_integration(db, "usr_p38", "gmail_oauth")
    app2 = M.JobApplication(id="app_p38b", career_profile_id="crp_p38", company="Corp2",
                            job_title="Dev2", recipient_email="rh2@corp2.com",
                            cover_letter="Candidature", application_method="email")
    db.add(app2)
    db.commit()
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"):
        res = es.send_application_email(app2, prof, db)
    ck(res.status == "not_connected", f"status=not_connected (got {res.status})")
    ck("Gmail non connecté" in (res.error or ""), "error invite à connecter Gmail")
    ck(res.provider == "gmail_oauth", "provider=gmail_oauth (app configurée)")

    # ── 7. send unauthorized (refresh échec) ──────────────────────────────
    print("\n--- 7. unauthorized (refresh échec → déconnexion) ---")
    # Re-connecter avec un refresh_token qui va échouer au refresh
    integ = M.UserEmailIntegration(
        user_id="usr_p38", provider="gmail_oauth", email="cand@gmail.com",
        refresh_token_encrypted=crypto.encrypt("1//revoked_refresh"),
        access_token_encrypted=crypto.encrypt("ya29.expired"),
        access_token_expires_at=datetime.utcnow() - timedelta(seconds=10),  # expiré → refresh
        scopes=es.GMAIL_SCOPE,
    )
    db.add(integ)
    db.commit()
    import urllib.error
    refresh_err = urllib.error.HTTPError(
        url="https://oauth2.googleapis.com/token", code=400,
        msg="Bad Request", hdrs=None, fp=None)
    app3 = M.JobApplication(id="app_p38c", career_profile_id="crp_p38", company="Corp3",
                            job_title="Dev3", recipient_email="rh3@corp3.com",
                            cover_letter="Cand", application_method="email")
    db.add(app3)
    db.commit()
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"), \
         patch("urllib.request.urlopen", side_effect=refresh_err):
        res = es.send_application_email(app3, prof, db)
    ck(res.status == "unauthorized", f"status=unauthorized (got {res.status})")
    ck("refresh échec" in (res.error or "") or "Reconnectez" in (res.error or ""),
       f"error invite à reconnecter (got {res.error})")
    # Intégration supprimée (stale token révoqué)
    gone = es.get_user_integration(db, "usr_p38", "gmail_oauth")
    ck(gone is None, "intégration stale supprimée après unauthorized")

    # ── 8. _build_rfc5322_message ─────────────────────────────────────────
    print("\n--- 8. RFC 5322 message (base64url) ---")
    raw = es._build_rfc5322_message("cand@gmail.com", "rh@corp.com", "Candidature Dev", "Madame,\n\nCordialement.")
    # Décoder base64url (Gmail format) → message RFC 5322 complet
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    ck("From: cand@gmail.com" in decoded, "header From présent")
    ck("To: rh@corp.com" in decoded, "header To présent")
    ck("Subject:" in decoded, "header Subject présent")
    # MIMEText utf-8 encode le body en base64 (Content-Transfer-Encoding) — décoder le body interne
    ck("Content-Transfer-Encoding:" in decoded, "Content-Transfer-Encoding présent (MIME)")
    # Extraire le body (après la 1ère ligne vide) + décoder base64 interne
    body_part = decoded.split("\n\n", 1)[1] if "\n\n" in decoded else ""
    try:
        inner = base64.b64decode(body_part.strip()).decode("utf-8")
    except Exception:
        inner = body_part  # fallback si pas base64 (7bit/quoted-printable)
    ck("Madame" in inner, f"body décodé contient 'Madame' (got {inner[:40]})")
    ck("Cordialement." in inner, "body décodé contient 'Cordialement.'")

    # ── 9. Persistence (disconnect + list + encrypted) ────────────────────
    print("\n--- 9. persistence (disconnect + list) ---")
    # Re-connect pour tester disconnect + list
    integ = M.UserEmailIntegration(user_id="usr_p38", provider="gmail_oauth", email="final@gmail.com",
                                   refresh_token_encrypted=crypto.encrypt("rt"),
                                   access_token_encrypted=crypto.encrypt("at"),
                                   access_token_expires_at=datetime.utcnow() + timedelta(seconds=600),
                                   scopes=es.GMAIL_SCOPE)
    db.add(integ)
    db.commit()
    lst = es.list_user_integrations(db, "usr_p38")
    ck(len(lst) == 1, f"list_user_integrations 1 (got {len(lst)})")
    ck(lst[0]["email"] == "final@gmail.com", "list retourne email")
    ck(lst[0]["provider"] == "gmail_oauth", "list retourne provider")
    deleted = es.disconnect_integration(db, "usr_p38", "gmail_oauth")
    ck(deleted is True, "disconnect_integration retourne True")
    ck(es.get_user_integration(db, "usr_p38", "gmail_oauth") is None, "intégration supprimée après disconnect")
    deleted2 = es.disconnect_integration(db, "usr_p38", "gmail_oauth")
    ck(deleted2 is False, "disconnect sur intégration absente → False")

    # ── 10. rate-limit (anti-abus) ────────────────────────────────────────
    print("\n--- 10. rate-limit (anti-abus 50/jour) ---")
    ck(es.DAILY_SEND_LIMIT == 50, f"DAILY_SEND_LIMIT=50 (got {es.DAILY_SEND_LIMIT})")
    # Simuler 50 envois aujourd'hui → rate-limit atteint
    app.sent_at = None
    db.query(M.JobApplication).filter_by(id="app_p38").update({"sent_at": datetime.utcnow()})
    for i in range(50):
        extra = M.JobApplication(id=f"app_rl_{i}", career_profile_id="crp_p38", company=f"C{i}",
                                job_title="D", recipient_email=f"r{i}@c.com", application_method="email",
                                sent_at=datetime.utcnow())
        db.add(extra)
    db.commit()
    integ = M.UserEmailIntegration(user_id="usr_p38", provider="gmail_oauth", email="rl@gmail.com",
                                   refresh_token_encrypted=crypto.encrypt("rt"),
                                   access_token_encrypted=crypto.encrypt("at"),
                                   access_token_expires_at=datetime.utcnow() + timedelta(seconds=600),
                                   scopes=es.GMAIL_SCOPE)
    db.add(integ)
    db.commit()
    app_rate = M.JobApplication(id="app_rate_test", career_profile_id="crp_p38", company="RateCorp",
                                job_title="Dev", recipient_email="rh@rate.com",
                                cover_letter="Cand", application_method="email")
    db.add(app_rate)
    db.commit()
    with patch.object(settings, "gmail_client_id", "test_gcid"), \
         patch.object(settings, "gmail_client_secret", "test_gcs"):
        res = es.send_application_email(app_rate, prof, db)
    ck(res.status == "error", f"rate-limit → status=error (got {res.status})")
    ck("Rate-limit" in (res.error or ""), "error mentionne rate-limit (anti-abus)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
