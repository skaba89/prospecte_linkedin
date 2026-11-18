"""P38-V2 — Envoi de candidatures via email (OAuth2 Gmail officiel — authorization code flow).

send_application_email(application, profile, db) → SendResult : envoie la candidature via l'intégration
email OAuth2 de l'utilisateur (Gmail connecté). v2 : OAuth2 Gmail réel (3-legged authorization code flow).

Flow OAuth2 Gmail (3-legged) :
  1. GET /api/career/email/connect/gmail_oauth → redirect Google consent (scope gmail.send).
     get_gmail_oauth_url(state) construit l'URL https://accounts.google.com/o/oauth2/v2/auth?...
  2. Google callback → /api/career/email/oauth/callback?code=XXX&state=XXX.
     handle_gmail_oauth_callback(code, db, user_id) : POST oauth2.googleapis.com/token (exchange code)
     → access_token (~1h) + refresh_token (long-lived). Tokens chiffrés Fernet (crypto.encrypt) +
     stockés en UserEmailIntegration. email récupéré via tokeninfo.
  3. send_application_email :
     - Si Gmail app non configuré (GMAIL_CLIENT_ID/SECRET absents) → status "not_configured".
     - Si user n'a pas connecté son Gmail → status "not_connected".
     - Sinon → _get_valid_access_token (refresh si expiré) → _send_via_gmail (Gmail API send).
       status "sent" + message_id Gmail. Application.sent_at + status mis à jour.

Anti-spam (P38 invariant) :
  - JAMAIS de mass-email/spam — une candidature à la fois, consentement explicite (POST /applications/{id}/send).
  - Rate-limit par utilisateur (max DAILY_SEND_LIMIT/jour — anti-abus).
  - Destinataire vérifié (email valide requis).
  - Scope minimal gmail.send (pas gmail.compose/full — principe moindre privilège).

Sécurité (P24-A) :
  - refresh_token + access_token JAMAIS en clair en DB — Fernet (services.crypto.encrypt/decrypt).
  - state OAuth anti-CSRF (réutilise OAuthState P10.3 — nonce usage unique).
  - AUCUN credential dans les logs.
  - HTTP via urllib (stdlib, pas de dépendance externe). Timeout 10s (anti-blocage).
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import settings
from . import crypto

log = logging.getLogger("revpulse.career.email_sender")

# Anti-abus : rate-limit par utilisateur (max 50 candidatures/jour). v3 : configurable par plan.
DAILY_SEND_LIMIT = 50

# Providers supportés (v2 : Gmail OAuth2 réel. Outlook v3 : Microsoft Graph).
SUPPORTED_PROVIDERS = ["gmail_oauth", "outlook_oauth"]

# Scope Gmail minimal (principe moindre privilège — envoi seulement, pas lecture/compose).
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"

# Timeout HTTP (secondes) — anti-blocage si Google API lente/down.
_HTTP_TIMEOUT = 10


@dataclass
class SendResult:
    """Résultat d'envoi d'une candidature (P38). status :
      - sent : envoyé avec succès (message_id Gmail).
      - not_configured : app Gmail non configurée (GMAIL_CLIENT_ID/SECRET absents — v1 stub).
      - not_connected : app configurée mais user n'a pas connecté son Gmail (doit faire OAuth).
      - unauthorized : token expiré/invalidé + refresh échec (user doit reconnecter).
      - error : échec technique (API down, rate-limit, destinataire invalide).
    """
    status: str  # sent|not_configured|not_connected|unauthorized|error
    provider: str = "none"
    message_id: str | None = None
    sent_at: datetime | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)


def _check_rate_limit(db, profile_id: str) -> bool:
    """Anti-abus : vérifie que le candidat n'a pas dépassé DAILY_SEND_LIMIT aujourd'hui.
    Retourne True si OK (peut envoyer), False si limite atteinte."""
    from sqlalchemy import func

    from .. import models as M
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.query(func.count(M.JobApplication.id)).filter(
        M.JobApplication.career_profile_id == profile_id,
        M.JobApplication.sent_at >= today_start,
    ).scalar() or 0
    return count < DAILY_SEND_LIMIT


# ─── OAuth2 Gmail (3-legged authorization code flow) ─────────────────────────


def is_gmail_configured() -> bool:
    """True si l'app Gmail est configurée (GMAIL_CLIENT_ID + SECRET en config)."""
    return bool(settings.gmail_client_id and settings.gmail_client_secret)


def is_outlook_configured() -> bool:
    """P38-V3 : True si Outlook OAuth2 (Microsoft Graph) est configuré
    (OUTLOOK_CLIENT_ID + SECRET en config)."""
    return bool(settings.outlook_client_id and settings.outlook_client_secret)


def is_provider_configured(provider: str) -> bool:
    """Vérifie si un provider email est configuré au niveau app (OAuth credentials)."""
    if provider not in SUPPORTED_PROVIDERS:
        return False
    if provider == "gmail_oauth":
        return is_gmail_configured()
    if provider == "outlook_oauth":
        return is_outlook_configured()
    return False


def get_gmail_oauth_url(state: str) -> str:
    """Construit l'URL de consentement Google (authorization code flow).
    state = nonce anti-CSRF (OAuthState P10.3 — usage unique). redirect_uri doit être autorisée
    dans Google Cloud Console. scope = gmail.send (moindre privilège). access_type=offline → refresh_token."""
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": settings.gmail_redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",  # requis pour refresh_token
        "prompt": "consent",  # force consent → refresh_token toujours retourné (même si déjà connecté)
        "state": state,
    }
    return f"{settings.gmail_auth_url}?{urllib.parse.urlencode(params)}"


def _exchange_code_for_tokens(code: str) -> dict | None:
    """Exchange authorization code → access_token + refresh_token (POST oauth2.googleapis.com/token).
    Retourne le payload JSON ou None si échec."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "redirect_uri": settings.gmail_redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        settings.gmail_token_url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("Gmail token exchange échec : %s", type(e).__name__)
        return None


def _get_user_email(access_token: str) -> str | None:
    """Récupère l'email du compte Gmail connecté via tokeninfo (GET oauth2.googleapis.com/tokeninfo).
    Retourne l'email ou None si échec."""
    url = f"https://oauth2.googleapis.com/tokeninfo?access_token={urllib.parse.quote(access_token)}"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("email")
    except Exception:
        return None


def handle_gmail_oauth_callback(code: str, state: str, db, user_id: str) -> dict:
    """Traite le callback OAuth Gmail : exchange code → tokens → stocke UserEmailIntegration (chiffré).
    Retourne {status: connected|error, provider, email?, error?}.
    state est vérifié par l'appelant (router) via OAuthState (anti-CSRF P10.3) avant cet appel."""
    if not is_gmail_configured():
        return {"status": "error", "provider": "gmail_oauth",
                "error": "App Gmail non configurée (GMAIL_CLIENT_ID/SECRET absents)."}
    tokens = _exchange_code_for_tokens(code)
    if not tokens or "access_token" not in tokens:
        return {"status": "error", "provider": "gmail_oauth",
                "error": "Exchange code échec (code invalide/expiré)."}
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")  # absent si user avait déjà connecté sans prompt=consent
    expires_in = tokens.get("expires_in", 3600)
    user_email = _get_user_email(access_token)
    # Upsert UserEmailIntegration (unique user_id + provider)
    from .. import models as M
    integration = db.query(M.UserEmailIntegration).filter(
        M.UserEmailIntegration.user_id == user_id,
        M.UserEmailIntegration.provider == "gmail_oauth",
    ).first()
    if integration:
        integration.email = user_email
        integration.access_token_encrypted = crypto.encrypt(access_token)
        integration.access_token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        integration.scopes = GMAIL_SCOPE
        if refresh_token:
            integration.refresh_token_encrypted = crypto.encrypt(refresh_token)
    else:
        integration = M.UserEmailIntegration(
            user_id=user_id, provider="gmail_oauth", email=user_email,
            refresh_token_encrypted=crypto.encrypt(refresh_token) if refresh_token else None,
            access_token_encrypted=crypto.encrypt(access_token),
            access_token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            scopes=GMAIL_SCOPE,
        )
        db.add(integration)
    db.commit()
    log.info("Gmail connecté pour user=%s email=%s.", user_id, user_email)
    return {"status": "connected", "provider": "gmail_oauth", "email": user_email}


def _refresh_access_token(refresh_token: str) -> dict | None:
    """Refresh l'access_token via refresh_token (POST oauth2.googleapis.com/token).
    Retourne {access_token, expires_in} ou None si échec (refresh_token invalidé/révoqué)."""
    data = urllib.parse.urlencode({
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(
        settings.gmail_token_url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 400 invalid_grant → refresh_token révoqué (user a changé son mdp / révoqué l'accès)
        log.warning("Gmail refresh échec HTTP %s.", e.code)
        return None
    except Exception as e:
        log.warning("Gmail refresh échec : %s", type(e).__name__)
        return None


def _get_valid_access_token(integration, db) -> str | None:
    """Retourne un access_token valide pour l'intégration (refresh si expiré).
    Retourne None si refresh échec (user doit reconnecter). Met à jour integration en DB."""
    if not integration.access_token_encrypted:
        return None
    now = datetime.utcnow()
    # Marge 60s (anti-race : token expire pendant l'envoi)
    if integration.access_token_expires_at and integration.access_token_expires_at > now + timedelta(seconds=60):
        try:
            return crypto.decrypt(integration.access_token_encrypted)
        except Exception:
            return None
    # Token expiré → refresh via refresh_token
    if not integration.refresh_token_encrypted:
        return None
    try:
        refresh_token = crypto.decrypt(integration.refresh_token_encrypted)
    except Exception:
        return None
    # P38-V3 : dispatch refresh par provider (Gmail vs Outlook Microsoft Graph).
    if integration.provider == "outlook_oauth":
        tokens = _refresh_outlook_token(refresh_token)
    else:
        tokens = _refresh_access_token(refresh_token)
    if not tokens or "access_token" not in tokens:
        return None
    new_access = tokens["access_token"]
    expires_in = tokens.get("expires_in", 3600)
    integration.access_token_encrypted = crypto.encrypt(new_access)
    integration.access_token_expires_at = now + timedelta(seconds=expires_in)
    db.commit()
    return new_access


def _build_rfc5322_message(sender: str, to: str, subject: str, body: str,
                           attachments: list | None = None) -> str:
    """Construit un message RFC 5322 (headers From/To/Subject + body) → base64url (Gmail API format).
    P38-V3 : multipart MIME si attachments (CV PDF). Sinon MIMEText plain (MVP original)."""
    from email.header import Header
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    if attachments:
        # Multipart : body text + 1 pièce jointe par attachment (CV PDF).
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = Header(subject, "utf-8")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for att in attachments:
            part = MIMEApplication(_b64decode(att["content_bytes_b64"]),
                                   Name=att["name"])
            part["Content-Disposition"] = f'attachment; filename="{att["name"]}"'
            part.add_header("Content-Type", att.get("contentType", "application/pdf"))
            msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = Header(subject, "utf-8")
    raw = msg.as_string()
    # Gmail API attend base64url (sans padding)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _b64decode(b64_str: str) -> bytes:
    """Décode base64 (standard ou urlsafe, avec ou sans padding) → bytes."""
    import binascii
    pad = "=" * (-len(b64_str) % 4)
    try:
        return base64.urlsafe_b64decode(b64_str + pad)
    except (binascii.Error, ValueError):
        return base64.b64decode(b64_str + pad)


def _send_via_gmail(access_token: str, sender: str, to: str, subject: str, body: str,
                    attachments: list | None = None) -> dict:
    """Envoie un email via Gmail API (POST gmail.googleapis.com/gmail/v1/users/me/messages/send).
    P38-V3 : attachments (CV PDF) via multipart MIME. Retourne {status: sent|error, message_id?, error?}."""
    raw_message = _build_rfc5322_message(sender, to, subject, body, attachments)
    payload = json.dumps({"raw": raw_message}).encode("utf-8")
    url = f"{settings.gmail_api_base}/messages/send"
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            resp_payload = json.loads(resp.read().decode("utf-8"))
        return {"status": "sent", "message_id": resp_payload.get("id")}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"status": "unauthorized", "error": "Gmail API 401 — access_token invalide."}
        body_err = e.read().decode("utf-8", errors="replace")[:300]
        return {"status": "error", "error": f"Gmail API HTTP {e.code} — {body_err}"}
    except Exception as e:
        return {"status": "error", "error": f"Gmail API injoignable — {type(e).__name__}: {e}"}


# ─── P38-V3 — Outlook OAuth2 (Microsoft Graph) ─────────────────────────────
OUTLOOK_SCOPE = "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access"


def get_outlook_oauth_url(state: str) -> str:
    """P38-V3 : construit l'URL de consentement Microsoft (authorization code flow).
    GET {authority}/authorize?response_type=code&client_id=...&redirect_uri=...&scope=...&state=...
    Scope Mail.Send + User.Read + offline_access (refresh_token)."""
    params = {
        "response_type": "code",
        "client_id": settings.outlook_client_id,
        "redirect_uri": settings.outlook_redirect_uri,
        "scope": OUTLOOK_SCOPE,
        "state": state,
        "response_mode": "query",
    }
    return f"{settings.outlook_authority}/authorize?{urllib.parse.urlencode(params)}"


def _exchange_outlook_code(code: str) -> dict | None:
    """P38-V3 : exchange authorization code → tokens (POST {authority}/token).
    Retourne {access_token, refresh_token, expires_in, scope} ou None si échec."""
    data = urllib.parse.urlencode({
        "client_id": settings.outlook_client_id,
        "client_secret": settings.outlook_client_secret,
        "code": code,
        "redirect_uri": settings.outlook_redirect_uri,
        "grant_type": "authorization_code",
        "scope": OUTLOOK_SCOPE,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{settings.outlook_authority}/token", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("Outlook token exchange HTTP %s.", e.code)
        return None
    except Exception as e:
        log.warning("Outlook token exchange échec : %s", type(e).__name__)
        return None


def _get_outlook_user_email(access_token: str) -> str | None:
    """P38-V3 : récupère l'email de l'utilisateur via Microsoft Graph GET /me."""
    req = urllib.request.Request(
        f"{settings.outlook_api_base}/me",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # /me retourne userPrincipalName OU mail
        return data.get("mail") or data.get("userPrincipalName")
    except Exception as e:
        log.warning("Outlook /me échec : %s", type(e).__name__)
        return None


def handle_outlook_oauth_callback(code: str, state: str, db, user_id: str) -> dict:
    """P38-V3 : traite le callback Outlook (exchange code + persist tokens chiffrés).
    Retourne {status: connected|error, provider, email?, error?}."""
    tokens = _exchange_outlook_code(code)
    if not tokens or "access_token" not in tokens:
        return {"status": "error", "provider": "outlook_oauth",
                "error": "Exchange code Outlook échec (code invalide/expiré)."}
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")  # offline_access → présent au premier consent
    expires_in = int(tokens.get("expires_in", 3600))
    user_email = _get_outlook_user_email(access_token) or "outlook-user@unknown"
    # Upsert UserEmailIntegration (unique user_id + provider)
    from .. import models as M
    integration = db.query(M.UserEmailIntegration).filter(
        M.UserEmailIntegration.user_id == user_id,
        M.UserEmailIntegration.provider == "outlook_oauth",
    ).first()
    now = datetime.utcnow()
    if integration:
        integration.access_token_encrypted = crypto.encrypt(access_token)
        integration.refresh_token_encrypted = crypto.encrypt(refresh_token) if refresh_token else integration.refresh_token_encrypted
        integration.access_token_expires_at = now + timedelta(seconds=expires_in)
        integration.email = user_email
        integration.scopes = tokens.get("scope", OUTLOOK_SCOPE)
    else:
        integration = M.UserEmailIntegration(
            user_id=user_id, provider="outlook_oauth", email=user_email,
            access_token_encrypted=crypto.encrypt(access_token),
            refresh_token_encrypted=crypto.encrypt(refresh_token) if refresh_token else None,
            access_token_expires_at=now + timedelta(seconds=expires_in),
            scopes=tokens.get("scope", OUTLOOK_SCOPE),
        )
        db.add(integration)
    db.commit()
    log.info("Outlook connecté pour user=%s (email=%s).", user_id, user_email)
    return {"status": "connected", "provider": "outlook_oauth", "email": user_email}


def _refresh_outlook_token(refresh_token: str) -> dict | None:
    """P38-V3 : refresh access_token via refresh_token (POST {authority}/token).
    Retourne {access_token, expires_in} ou None si échec."""
    data = urllib.parse.urlencode({
        "client_id": settings.outlook_client_id,
        "client_secret": settings.outlook_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": OUTLOOK_SCOPE,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{settings.outlook_authority}/token", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("Outlook refresh échec HTTP %s.", e.code)
        return None
    except Exception as e:
        log.warning("Outlook refresh échec : %s", type(e).__name__)
        return None


def _send_via_outlook(access_token: str, sender: str, to: str, subject: str, body: str,
                      attachments: list | None = None) -> dict:
    """P38-V3 : envoie un email via Microsoft Graph POST /me/sendMail.
    attachments : [{name, content_bytes_b64}] (CV PDF). sender ignoré (implied par access_token).
    Retourne {status: sent|error, message_id?, error?}."""
    msg = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
        "from": {"emailAddress": {"address": sender}} if sender and sender != "me" else None,
    }
    if attachments:
        msg["attachments"] = [
            {"@odata.type": "#microsoft.graph.fileAttachment",
             "name": a["name"], "contentType": a.get("contentType", "application/pdf"),
             "contentBytes": a["content_bytes_b64"]}
            for a in attachments
        ]
    payload = json.dumps({"message": msg, "saveToSentItems": True}).encode("utf-8")
    req = urllib.request.Request(
        f"{settings.outlook_api_base}/me/sendMail", data=payload, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        # /me/sendMail retourne 202 Accepted (no body).
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status == 202:
                return {"status": "sent", "message_id": f"outlook-{int(datetime.utcnow().timestamp())}"}
            return {"status": "sent", "message_id": f"outlook-{resp.status}"}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"status": "unauthorized", "error": "Outlook Graph 401 — access_token invalide."}
        body_err = e.read().decode("utf-8", errors="replace")[:300]
        return {"status": "error", "error": f"Outlook Graph HTTP {e.code} — {body_err}"}
    except Exception as e:
        return {"status": "error", "error": f"Outlook Graph injoignable — {type(e).__name__}: {e}"}


# ─── Intégrations (persistance + revoke) ───────────────────────────────────
def get_user_integration(db, user_id: str, provider: str = "gmail_oauth"):
    """Retourne l'intégration email de l'utilisateur pour le provider (None si non connecté)."""
    from .. import models as M
    return db.query(M.UserEmailIntegration).filter(
        M.UserEmailIntegration.user_id == user_id,
        M.UserEmailIntegration.provider == provider,
    ).first()


def list_user_integrations(db, user_id: str) -> list[dict]:
    """Liste les intégrations email connectées de l'utilisateur (pour /email/status)."""
    from .. import models as M
    rows = db.query(M.UserEmailIntegration).filter(
        M.UserEmailIntegration.user_id == user_id,
    ).all()
    return [{"provider": r.provider, "email": r.email, "scopes": r.scopes,
             "connected_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


def _revoke_provider_token(integration, provider: str) -> bool:
    """P38-V3 : révoque le refresh_token côté serveur (best-effort, avant suppression DB).
    Gmail : POST oauth2.googleapis.com/revoke?token=refresh_token (invalide immédiatement).
    Outlook : pas d'endpoint revoke simple pour comptes perso MS (token expire ~1h access /
    refresh ~90j) — best-effort = suppression locale (enterprise : admin Azure AD revoke)."""
    if not integration.refresh_token_encrypted:
        return False
    try:
        refresh_token = crypto.decrypt(integration.refresh_token_encrypted)
    except Exception:
        return False
    if provider == "gmail_oauth":
        data = urllib.parse.urlencode({"token": refresh_token}).encode("utf-8")
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke", data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                log.info("Gmail refresh_token révoqué côté serveur (HTTP %s).", resp.status)
                return True
        except Exception as e:
            log.warning("Gmail revoke best-effort échoué : %s", type(e).__name__)
            return False
    elif provider == "outlook_oauth":
        # MS pas d'endpoint revoke perso — best-effort local (token expire naturellement).
        log.info("Outlook revoke best-effort (local delete — MS pas d'endpoint revoke perso).")
        return False
    return False


def disconnect_integration(db, user_id: str, provider: str) -> bool:
    """Supprime l'intégration email. P38-V3 : revoke côté serveur (best-effort) AVANT suppression DB.
    Retourne True si l'intégration existait + fut supprimée."""
    from .. import models as M
    integration = db.query(M.UserEmailIntegration).filter(
        M.UserEmailIntegration.user_id == user_id,
        M.UserEmailIntegration.provider == provider,
    ).first()
    if not integration:
        return False
    # P38-V3 : revoke serveur best-effort (même si échec, on supprime local — user-side isolé).
    _revoke_provider_token(integration, provider)
    db.delete(integration)
    db.commit()
    return True


def send_application_email(application, profile, db, cv_pdf_b64: str | None = None,
                           cv_filename: str = "CV.pdf") -> SendResult:
    """Envoie une candidature via l'intégration email OAuth2 de l'utilisateur (P38-V3).

    - Aucun provider configuré (GMAIL/OUTLOOK creds absents) → status "not_configured".
    - User n'a connecté aucun provider → status "not_connected" (doit faire OAuth).
    - Provider connecté → refresh access_token si expiré → send via Gmail/Outlook → status "sent".
    - P38-V3 : cv_pdf_b64 (base64 PDF) attaché en pièce jointe (multipart MIME Gmail / Graph attachments).
    - 401 (refresh échec) → status "unauthorized" (user doit reconnecter) + revoke stale.
    - API error/timeout → status "error".

    Dispatch provider : si user a connecté Gmail → Gmail ; sinon Outlook ; sinon not_connected.
    Anti-spam : une candidature à la fois, consentement explicite (POST /applications/{id}/send).
    Rate-limit : max DAILY_SEND_LIMIT par jour par candidat."""
    # Anti-abus : rate-limit.
    if not _check_rate_limit(db, application.career_profile_id):
        log.warning("Rate-limit atteint pour profile=%s (max %d/jour).",
                    application.career_profile_id, DAILY_SEND_LIMIT)
        return SendResult(status="error",
                          error=f"Rate-limit atteint (max {DAILY_SEND_LIMIT} candidatures/jour).")

    # Destinataire requis.
    if not application.recipient_email:
        return SendResult(status="error", error="recipient_email requis pour envoi email.")

    # P38-V3 : dispatch provider — Gmail en priorité, sinon Outlook.
    user_id = profile.user_id
    provider = None
    integration = None
    if is_gmail_configured():
        integration = get_user_integration(db, user_id, "gmail_oauth")
        if integration:
            provider = "gmail_oauth"
    if not provider and is_outlook_configured():
        integration = get_user_integration(db, user_id, "outlook_oauth")
        if integration:
            provider = "outlook_oauth"

    # Aucun provider configuré au niveau app.
    if not is_gmail_configured() and not is_outlook_configured():
        log.info("Envoi candidature app=%s — aucun provider configuré (not_configured).", application.id)
        return SendResult(status="not_configured", provider="none",
                          error="Aucun provider email configuré (GMAIL/OUTLOOK creds absents). Configurez OAuth2.",
                          detail={"recipient": application.recipient_email, "providers": SUPPORTED_PROVIDERS})

    # App configurée mais user n'a connecté aucun provider.
    if not provider or not integration:
        configured = [p for p in SUPPORTED_PROVIDERS if is_provider_configured(p)]
        return SendResult(status="not_connected", provider=configured[0] if configured else "none",
                          error=f"Aucun provider connecté. Connectez via /api/career/email/connect/{configured[0] if configured else 'gmail_oauth'}.",
                          detail={"recipient": application.recipient_email, "configured": configured})

    # Access token valide (refresh si expiré — dispatch par provider dans _get_valid_access_token).
    access_token = _get_valid_access_token(integration, db)
    if not access_token:
        # Refresh échec → user doit reconnecter (révoque l'intégration stale).
        disconnect_integration(db, user_id, provider)
        return SendResult(status="unauthorized", provider=provider,
                          error=f"Token {provider} expiré + refresh échec. Reconnectez votre compte.")

    # Construire le sujet + corps + attachments depuis la candidature.
    sender = integration.email or "me"
    subject = f"Candidature — {application.job_title} chez {application.company}"
    body = application.cover_letter or f"Madame, Monsieur,\n\nJe vous prie de trouver ci-joint ma candidature pour le poste de {application.job_title} chez {application.company}.\n\nCordialement."
    attachments = []
    if cv_pdf_b64:
        attachments.append({"name": cv_filename, "content_bytes_b64": cv_pdf_b64,
                            "contentType": "application/pdf"})

    # Envoi via le provider connecté (dispatch Gmail vs Outlook Graph).
    if provider == "outlook_oauth":
        result = _send_via_outlook(access_token, sender, application.recipient_email,
                                   subject, body, attachments)
    else:
        result = _send_via_gmail(access_token, sender, application.recipient_email,
                                 subject, body, attachments)
    if result["status"] == "sent":
        sent_at = datetime.utcnow()
        # Mettre à jour la candidature (sent_at + status + application_method=email).
        application.sent_at = sent_at
        application.application_method = "email"
        application.status = "submitted"
        db.commit()
        log.info("Candidature app=%s envoyée via %s (message_id=%s, attachments=%d).",
                 application.id, provider, result.get("message_id"), len(attachments))
        return SendResult(status="sent", provider=provider,
                          message_id=result.get("message_id"), sent_at=sent_at,
                          detail={"recipient": application.recipient_email, "sender": sender,
                                  "attachments": len(attachments), "cv_filename": cv_filename if attachments else None})
    if result["status"] == "unauthorized":
        disconnect_integration(db, user_id, provider)
        return SendResult(status="unauthorized", provider=provider,
                          error=result.get("error", f"{provider} 401."))
    return SendResult(status="error", provider=provider,
                      error=result.get("error", f"Échec envoi {provider}."),
                      detail={"recipient": application.recipient_email})
