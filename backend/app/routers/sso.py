"""P45 — SSO Enterprise OIDC : initiate + callback + JIT provisioning par domaine.

Couvre Microsoft Entra ID, Google Workspace, Okta, Keycloak (tous supportent OIDC).
SAML = v5 (python3-saml + XML signing — plus lourd). OIDC suffit pour la majorité des IdP enterprise.

Flow :
  1. GET /api/auth/sso/{tenant_id}/initiate → find EnterpriseSSO → build authorize URL + state JWT (anti-CSRF) → redirect IdP.
  2. IdP callback → GET /api/auth/sso/callback?code=&state= → verify state (nonce single-use) → exchange code (POST token_url)
     → fetch userinfo (GET userinfo_url) → extract sub + email → check email domain in EnterpriseSSO.domains (JIT gate)
     → find-or-create User (by sso_id or email, password_hash placeholder) → issue JWT RevPulse → redirect frontend with token.

Sécurité (P45) :
  - state JWT signé + nonce single-use (OAuthState model, anti-CSRF + anti-rejeu — réutilise pattern LinkedIn finalize).
  - client_secret chiffré Fernet en DB (security.encrypt_token/decrypt_token).
  - JIT provisioning gate par domaine (email domain doit être dans EnterpriseSSO.domains — pas de création d'user random).
  - Aucun secret dans les logs. URL de redirection frontend limitée à settings.app_base_url (anti-open-redirect).

Usage : un admin configure EnterpriseSSO pour son tenant (client_id, client_secret, issuer_url, domains).
L'utilisateur clique "SSO Login" → /initiate → IdP → callback → JWT → app.
"""
from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..config import settings
from ..database import get_db
from ..i18n import get_lang, t

router = APIRouter(prefix="/api/auth/sso", tags=["sso"])
_log = logging.getLogger("revpulse.sso")


# ---- State OAuth (anti-CSRF + anti-rejeu, réutilise OAuthState model) ----
def _sso_state_for(db: Session, tid: str) -> str:
    """JWT signé {tid, nonce, exp court 10m} + nonce single-use persisté (oauth_states)."""
    nonce = secrets.token_urlsafe(24)
    exp = datetime.utcnow() + timedelta(minutes=10)
    db.add(models.OAuthState(tenant_id=tid, nonce=nonce, expires_at=exp, used=False))
    db.commit()
    return security.jwt.encode({"tid": tid, "nonce": nonce, "exp": exp},
                               settings.jwt_secret, algorithm=settings.jwt_algo)


def _sso_verify_state(state: str, db: Session) -> str:
    """Valide signature + nonce (existant, non utilisé, non expiré) → marque utilisé (anti-rejeu). Retourne tid."""
    try:
        payload = security.jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algo])
    except security.jwt.PyJWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State SSO invalide (CSRF ?)") from None
    tid, nonce = payload.get("tid"), payload.get("nonce")
    if not tid or not nonce:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State SSO incomplet")
    row = db.query(models.OAuthState).filter_by(nonce=nonce).first()
    if not row or row.used or (row.expires_at and row.expires_at < datetime.utcnow()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State SSO invalide/expiré/rejoué")
    row.used = True
    db.commit()
    return tid


# ---- Appels OIDC (réel, urllib — pas de nouvelle dépendance) ----
def _exchange_oidc_code(sso: models.EnterpriseSSO, code: str) -> dict:
    """POST token_url (auth-code → access_token + id_token). client_secret décrypté Fernet."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": settings.sso_redirect_uri,
        "client_id": sso.client_id,
        "client_secret": security.decrypt_token(sso.client_secret_encrypted),
    }).encode()
    req = urllib.request.Request(sso.token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _fetch_oidc_userinfo(sso: models.EnterpriseSSO, access_token: str) -> dict:
    """GET userinfo_url (Bearer) → {sub, email, name, ...}."""
    req = urllib.request.Request(sso.userinfo_url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _email_domain(email: str) -> str:
    """Extrait le domaine d'une adresse e-mail (lowercase). '' si invalide."""
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower().strip()


def _domain_allowed(email: str, sso: models.EnterpriseSSO) -> bool:
    """P45 JIT gate : le domaine de l'email doit être dans EnterpriseSSO.domains (JSON list)."""
    domains = json.loads(sso.domains or "[]")
    if not domains:
        return False  # pas de domaines configurés → pas de JIT (sécurité : deny par défaut)
    return _email_domain(email) in [d.lower().strip() for d in domains]


def _find_or_create_sso_user(db: Session, sso: models.EnterpriseSSO, sub: str, email: str, name: str) -> models.User:
    """JIT provisioning : find by (sso_id + identity_provider) ou email → sinon create.
    password_hash = placeholder aléatoire (jamais utilisé pour login SSO)."""
    provider = f"oidc:{sso.issuer_url}"
    user = db.query(models.User).filter_by(sso_id=sub, identity_provider=provider).first()
    if user:
        # Update email/name au cas où (IdP peut changer)
        if email and user.email != email:
            user.email = email
        if name and user.name != name:
            user.name = name
        db.commit()
        return user
    # Fallback : email exists déjà (user local qui active SSO) → lie le compte
    user = db.query(models.User).filter_by(email=email.lower()).first()
    if user:
        user.sso_id = sub
        user.identity_provider = provider
        user.sso_tenant_id = sso.tenant_id
        db.commit()
        return user
    # Sinon : JIT create (password_hash placeholder — non utilisable pour login password)
    user = models.User(
        email=email.lower(), name=name or email.split("@")[0],
        password_hash=security.hash_password(secrets.token_urlsafe(32)),
        sso_id=sub, identity_provider=provider, locale=settings.default_language,
        sso_tenant_id=sso.tenant_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _log.info("JIT SSO user created : tenant=%s email=%s provider=%s", sso.tenant_id, email, provider)
    return user


@router.get("/{tenant_id}/initiate")
def sso_initiate(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """Démarre le flow SSO OIDC : redirect vers l'IdP authorize URL avec state anti-CSRF."""
    lang = get_lang(request.headers.get("accept-language"))
    sso = db.query(models.EnterpriseSSO).filter_by(tenant_id=tenant_id, enabled=True).first()
    if not sso:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("sso.not_configured", lang))
    state = _sso_state_for(db, tenant_id)
    params = {
        "response_type": "code", "client_id": sso.client_id,
        "redirect_uri": settings.sso_redirect_uri, "state": state,
        "scope": sso.scopes,
    }
    auth_url = sso.authorize_url + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
def sso_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    """Callback OIDC : verify state → exchange code → userinfo → JIT user → JWT → redirect frontend.
    Le token JWT est passé en query param au frontend (fragment # serait mieux mais query est plus simple
    pour une page statique). Le frontend le stocke dans localStorage['revpulse_jwt']."""
    lang = get_lang(request.headers.get("accept-language"))
    tenant_id = _sso_verify_state(state, db)
    sso = db.query(models.EnterpriseSSO).filter_by(tenant_id=tenant_id, enabled=True).first()
    if not sso:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("sso.not_configured", lang))
    try:
        tok = _exchange_oidc_code(sso, code)
    except Exception as e:
        _log.warning("SSO code exchange failed (tenant=%s): %s", tenant_id, type(e).__name__)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("sso.code_exchange_failed", lang)) from None
    access_token = tok.get("access_token", "")
    try:
        info = _fetch_oidc_userinfo(sso, access_token)
    except Exception as e:
        _log.warning("SSO userinfo failed (tenant=%s): %s", tenant_id, type(e).__name__)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("sso.userinfo_failed", lang)) from None
    sub = str(info.get("sub", ""))
    email = (info.get("email") or "").lower().strip()
    name = info.get("name") or info.get("preferred_username") or ""
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("sso.no_email", lang))
    if not _domain_allowed(email, sso):
        domains = json.loads(sso.domains or "[]")
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("sso.domain_not_allowed", lang, domains=", ".join(domains)))
    user = _find_or_create_sso_user(db, sso, sub, email, name)
    jwt_token = security.create_access_token(user.id)
    _log.info("SSO login success : tenant=%s user=%s provider=%s", tenant_id, user.id, user.identity_provider)
    # Redirect frontend avec token (query param). Frontend stocke dans localStorage + redirige vers l'app.
    base = getattr(settings, "app_base_url", "/") or "/"
    sep = "&" if "?" in base else "?"
    return RedirectResponse(url=f"{base}{sep}sso_token={jwt_token}&lang={user.locale}")
