"""LinkedIn OAuth 2.0 : connexion réelle + publication, avec mode mock pour dev/CI.

Routes :
  GET  /api/tenants/{tid}/linkedin/auth          → URL d'autorisation (state signé + nonce single-use)
  GET  /api/tenants/{tid}/linkedin/mock-connect  → (mock only, dev/CI) crée une connexion factice
  GET  /api/linkedin/callback                     → callback OAuth (valide state anti-rejeu, échange code, stocke token CHIFFRÉ)
  GET  /api/tenants/{tid}/linkedin/status         → état de la connexion
  POST /api/tenants/{tid}/linkedin/disconnect     → supprime la connexion
  POST /api/tenants/{tid}/linkedin/publish?aid=   → publie un contenu approuvé (post/comment/dm séparés)

P10.2 : access_token stocké CHIFFRÉ (Fernet, rotation via *_PREVIOUS).
P10.3 : state OAuth = JWT signé {tid, nonce, exp} + nonce à usage unique (table oauth_states) → anti-rejeu.
P10.4 : mode mock + mock-connect INTERDITS en production (is_production gate).
P10.9 : publication tracée (table publications) + dédup par content_hash (sha256) + statut durable.
P10.10 : post/comment/dm séparés selon la capacité réelle de l'API LinkedIn (dm = manuel assisté).

Mode réel (production) : nécessite REVPULSE_LINKEDIN_CLIENT_ID/_SECRET/_REDIRECT_URI + REVPULSE_TOKEN_ENCRYPTION_KEY.
Scopes : openid profile w_member_social (w_member_social = publication, requiert App approuvée).
"""
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..audit import log_audit
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api", tags=["linkedin"])
_log = logging.getLogger("revpulse.linkedin")

_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_UGC_POST_URL = "https://api.linkedin.com/v2/ugcPosts"
_COMMENT_URL_TMPL = "https://api.linkedin.com/v2/socialActions/{activity}/comments"
_SCOPES = "openid profile w_member_social"
_MOCK_TOKEN = "mock-token-dev"

# P10.10 : capacités LinkedIn réelles par type. dm = NON automatisable (API messaging restreinte) → manuel.
LINKEDIN_CAPABILITY = {
    "post": "automated",        # ugcPosts API
    "comment": "automated",     # socialActions API (requiert parent_urn du post)
    "dm": "manual_required",    # API messaging restreinte → tâche manuelle assistée
}


def _is_mock() -> bool:
    """P10.4 : mock interdit en production. Autorisé en dev/CI si mock=true ou pas de creds."""
    if settings.is_production:
        return False
    return bool(settings.linkedin_mock) or not settings.linkedin_client_id


# ---- State OAuth (anti-CSRF + anti-rejeu, P10.3) ----
def _state_for(db: Session, tid: str) -> str:
    """JWT signé {tid, nonce, exp court} + nonce à usage unique persisté (oauth_states)."""
    nonce = secrets.token_urlsafe(24)
    exp = datetime.utcnow() + timedelta(minutes=10)
    db.add(models.OAuthState(tenant_id=tid, nonce=nonce, expires_at=exp, used=False))
    db.commit()
    return security.jwt.encode({"tid": tid, "nonce": nonce, "exp": exp},
                               settings.jwt_secret, algorithm=settings.jwt_algo)


def _verify_state(state: str, db: Session) -> str:
    """Valide signature + nonce (existant, non utilisé, non expiré) → marque utilisé (anti-rejeu)."""
    try:
        payload = security.jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algo])
    except security.JWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth invalide (CSRF ?)") from None
    tid, nonce = payload.get("tid"), payload.get("nonce")
    if not tid or not nonce:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth incomplet")
    row = db.query(models.OAuthState).filter_by(nonce=nonce).first()
    if not row:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth inconnu (rejeu bloqué)")
    if row.used:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth déjà utilisé (rejeu bloqué)")
    if row.expires_at and row.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth expiré")
    row.used = True
    db.commit()
    return tid


# ---- Tokens : chiffrement Fernet (P10.2) ----
def _store_token(tok: str) -> str:
    """Chiffre le token si une clé Fernet est configurée (prod). En dev/test sans clé → plaintext (legacy)."""
    if settings.token_encryption_key:
        return security.encrypt_token(tok)
    return tok


def _load_token(conn: models.LinkedInConnection) -> str:
    """Déchiffre le token stocké (gère rotation + legacy plaintext dev)."""
    return security.decrypt_token(conn.access_token)


def _upsert_connection(db: Session, tid: str, *, token: str, member_id: str, member_name: str,
                       member_headline: str, scope: str, expires_in: int) -> models.LinkedInConnection:
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    exp = datetime.utcnow() + timedelta(seconds=expires_in)
    enc = _store_token(token)
    if conn:
        conn.access_token = enc
        conn.expires_at = exp
        conn.member_id = member_id
        conn.member_name = member_name
        conn.member_headline = member_headline
        conn.scope = scope
        conn.connected_at = datetime.utcnow()
    else:
        conn = models.LinkedInConnection(
            tenant_id=tid, access_token=enc, expires_at=exp, member_id=member_id,
            member_name=member_name, member_headline=member_headline, scope=scope)
        db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


# ---- Appels API LinkedIn (réel) ----
def _exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": settings.linkedin_redirect_uri,
        "client_id": settings.linkedin_client_id,
        "client_secret": settings.linkedin_client_secret,
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _fetch_userinfo(access_token: str) -> dict:
    req = urllib.request.Request(_USERINFO_URL)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _publish_post(access_token: str, member_id: str, text: str) -> dict:
    payload = {
        "author": f"urn:li:person:{member_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": text}, "shareMediaCategory": "NONE"}},
        "visibility": {"com.linkedin.visibility.PhoneVisibilityEnum": "PUBLIC"},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(_UGC_POST_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Restli-Method", "create")
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        urn = r.headers.get("Location", "").split("/")[-1] if r.headers.get("Location") else ""
        return json.loads(body) if body else {"id": urn}


def _publish_comment(access_token: str, parent_urn: str, text: str) -> dict:
    """Commentaire via socialActions (requiert l'URN du post parent)."""
    url = _COMMENT_URL_TMPL.format(activity=urllib.parse.quote(parent_urn, safe=""))
    data = json.dumps({"actor": "urn:li:person:me", "message": {"text": text}}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Restli-Method", "create")
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        urn = r.headers.get("Location", "").split("/")[-1] if r.headers.get("Location") else ""
        return json.loads(body) if body else {"id": urn}


# ---- Routes ----
@router.get("/tenants/{tid}/linkedin/auth", response_model=schemas.LinkedInAuthUrl)
def linkedin_auth(tid: str, db: Session = Depends(get_db),
                  _t: models.Tenant = Depends(get_tenant),
                  _perm=Depends(require_permission("workspace.manage"))):
    """Démarre l'OAuth LinkedIn : renvoie l'URL d'autorisation (state signé + nonce single-use)."""
    if _is_mock():
        return schemas.LinkedInAuthUrl(
            auth_url=f"/api/tenants/{tid}/linkedin/mock-connect", mock=True)
    params = {"response_type": "code", "client_id": settings.linkedin_client_id,
              "redirect_uri": settings.linkedin_redirect_uri, "scope": _SCOPES,
              "state": _state_for(db, tid)}
    return schemas.LinkedInAuthUrl(
        auth_url=_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params), mock=False)


@router.get("/tenants/{tid}/linkedin/mock-connect")
def linkedin_mock_connect(tid: str, db: Session = Depends(get_db)):
    """Mode mock (dev/CI) : crée une connexion LinkedIn factice. P10.4 : 403 en production."""
    if settings.is_production:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mode mock interdit en production.")
    if not _is_mock():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mode mock désactivé (creds LinkedIn présentes).")
    if not db.query(models.Tenant).filter_by(id=tid).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant introuvable")
    _upsert_connection(db, tid, token=_MOCK_TOKEN, member_id="mock-member-001",
                       member_name="Alex Démo (LinkedIn mock)",
                       member_headline="Connexion démo — mode dev", scope=_SCOPES, expires_in=3600)
    log_audit(db, tenant_id=tid, actor_user_id=None, actor_name="LinkedIn OAuth (mock)",
              action="linkedin.connect", target_type="linkedin_connection", target_id=tid,
              detail={"member_name": "Alex Démo (LinkedIn mock)", "mock": True})
    db.commit()
    return RedirectResponse(url="/?linkedin=ok&mock=1")


@router.get("/linkedin/callback")
def linkedin_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Callback OAuth : valide state (anti-rejeu), échange code, stocke le token CHIFFRÉ."""
    tid = _verify_state(state, db)
    try:
        tok = _exchange_code(code)
    except urllib.error.HTTPError as e:
        _log.warning("OAuth code exchange failed: HTTP %s", e.code)
        return RedirectResponse(url="/?linkedin=error&code=oauth_exchange_failed")
    except Exception as e:
        _log.warning("OAuth callback error: %s", type(e).__name__)
        return RedirectResponse(url="/?linkedin=error&code=oauth_callback_error")
    access_token = tok.get("access_token", "")
    expires_in = int(tok.get("expires_in", 3600))
    try:
        info = _fetch_userinfo(access_token)
    except Exception:
        info = {}
    _upsert_connection(db, tid, token=access_token,
                       member_id=info.get("sub", ""), member_name=info.get("name", "Membre LinkedIn"),
                       member_headline=info.get("headline", ""), scope=tok.get("scope", _SCOPES),
                       expires_in=expires_in)
    log_audit(db, tenant_id=tid, actor_user_id=None, actor_name="LinkedIn OAuth (callback)",
              action="linkedin.connect", target_type="linkedin_connection", target_id=tid,
              detail={"member_name": info.get("name", ""), "mock": False})
    db.commit()
    return RedirectResponse(url="/?linkedin=ok")


@router.get("/tenants/{tid}/linkedin/status", response_model=schemas.LinkedInStatus)
def linkedin_status(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    if not conn:
        return schemas.LinkedInStatus(connected=False, mock=_is_mock())
    is_mock_conn = _is_mock() or _load_token(conn) == _MOCK_TOKEN
    return schemas.LinkedInStatus(
        connected=True, member_name=conn.member_name, member_id=conn.member_id,
        member_headline=conn.member_headline,
        expires_at=conn.expires_at.isoformat() if conn.expires_at else None, mock=is_mock_conn)


@router.post("/tenants/{tid}/linkedin/disconnect")
def linkedin_disconnect(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                        _perm=Depends(require_permission("workspace.manage"))):
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    if not conn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucune connexion LinkedIn à supprimer.")
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="linkedin.disconnect", target_type="linkedin_connection", target_id=tid, detail={})
    db.delete(conn)
    db.commit()
    return {"disconnected": tid}


@router.post("/tenants/{tid}/linkedin/publish", response_model=schemas.PublishResult)
def linkedin_publish(tid: str, aid: str, parent_urn: str | None = None, db: Session = Depends(get_db),
                     _t: models.Tenant = Depends(get_tenant),
                     _perm=Depends(require_permission("content.publish"))):
    """Publie un contenu approuvé sur LinkedIn. P10.9 (dédup + statut durable) + P10.10 (post/comment/dm séparés).

    kind déduit du type de l'Approval (post|comment|dm). parent_urn requis pour un commentaire (post parent).
    dm = non automatisable via API → statut 'manual_required' (tâche manuelle assistée).
    """
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    if not conn:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun compte LinkedIn connecté. Connectez-le d'abord.")
    a = db.query(models.Approval).filter_by(id=aid, tenant_id=tid).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contenu introuvable")
    if a.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Contenu non approuvé (status={a.status}). Approuvez-le avant publication.")
    text = (a.content_body or f"{a.title}\n\n{a.summary}").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contenu vide (pas de corps à publier).")
    kind = (a.type or "post").lower()
    if kind not in LINKEDIN_CAPABILITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Type de publication non supporté : {kind}")

    # P10.9 : dédup par content_hash (un même contenu déjà publié → rejet).
    ch = security.content_hash(text)
    dup = db.query(models.Publication).filter_by(tenant_id=tid, content_hash=ch, status="published").first()
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Contenu déjà publié (dédup content_hash). Modifiez le texte avant de republier.")

    pub = models.Publication(tenant_id=tid, approval_id=aid, kind=kind, content_hash=ch, status="pending")
    db.add(pub)
    db.commit()
    db.refresh(pub)

    # P10.10 : capacité LinkedIn par type.
    if LINKEDIN_CAPABILITY[kind] == "manual_required":
        pub.status = "manual_required"
        pub.error_detail = "Type non automatisable via l'API LinkedIn (messaging restreint) — tâche manuelle assistée requise."
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="linkedin.publish", target_type="approval", target_id=aid,
                  detail={"published": False, "kind": kind, "status": "manual_required"})
        db.commit()
        return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)

    # post ou comment (automated)
    if _is_mock() or _load_token(conn) == _MOCK_TOKEN:
        urn = f"urn:li:ugcPost:mock-{int(time.time())}"
        pub.status = "published"
        pub.linkedin_urn = urn
        pub.published_at = datetime.utcnow()
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="linkedin.publish", target_type="approval", target_id=aid,
                  detail={"published": True, "post_urn": urn, "mock": True, "kind": kind})
        db.commit()
        return schemas.PublishResult(published=True, post_urn=urn,
                                     detail="Publication mockée (mode dev) — aucun appel réseau.")
    access_token = _load_token(conn)
    try:
        if kind == "comment":
            if not parent_urn:
                pub.status = "manual_required"
                pub.error_detail = "Commentaire requiert parent_urn (URN du post parent) — tâche manuelle assistée."
                db.commit()
                return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)
            result = _publish_comment(access_token, parent_urn, text)
        else:
            result = _publish_post(access_token, conn.member_id, text)
        urn = result.get("id") or result.get("activity") or "urn:li:ugcPost:unknown"
        pub.status = "published"
        pub.linkedin_urn = urn
        pub.published_at = datetime.utcnow()
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="linkedin.publish", target_type="approval", target_id=aid,
                  detail={"published": True, "post_urn": urn, "mock": False, "kind": kind})
        db.commit()
        return schemas.PublishResult(published=True, post_urn=urn, detail="Publié sur LinkedIn.")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        pub.status = "failed"
        pub.error_detail = f"LinkedIn API {e.code}: {detail}"
        _log.warning("LinkedIn publish failed (HTTP %s): %s", e.code, detail[:120])
        db.commit()
        return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)
    except Exception as e:
        pub.status = "failed"
        pub.error_detail = f"Erreur réseau : {type(e).__name__}"
        _log.warning("LinkedIn publish error: %s", type(e).__name__)
        db.commit()
        return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)
