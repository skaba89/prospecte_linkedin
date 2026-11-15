"""LinkedIn OAuth 2.0 : connexion réelle + publication, avec mode mock pour dev/CI.

Routes :
  GET  /api/tenants/{tid}/linkedin/auth          → renvoie l'URL d'autorisation (ou mock)
  GET  /api/tenants/{tid}/linkedin/mock-connect  → (mock only) crée une connexion factice
  GET  /api/linkedin/callback                     → callback OAuth (échange code → token → profil)
  GET  /api/tenants/{tid}/linkedin/status         → état de la connexion
  POST /api/tenants/{tid}/linkedin/disconnect     → supprime la connexion
  POST /api/tenants/{tid}/linkedin/publish?aid=   → publie un contenu approuvé sur LinkedIn

Mode réel (production) : nécessite REVPULSE_LINKEDIN_CLIENT_ID / _SECRET / _REDIRECT_URI
(LinkedIn App sur https://www.linkedin.com/developers/apps).

Mode mock (dev/CI) : si pas de creds OU REVPULSE_LINKEDIN_MOCK=true, simule la connexion
et la publication. Permet de tester tout le flow UI sans App LinkedIn.

Scopes : `openid profile w_member_social`
  - openid / profile : récupérer member_id + nom (fonctionne immédiatement, sans review LinkedIn)
  - w_member_social   : PUBLIER (requiert l'accès Marketing API approuvé par LinkedIn)
⚠️ La publication réelle (/publish) ne marche que si l'App a `w_member_social` approuvé.
   En mock, publish renvoie un URN factice — utile pour valider le flow UI.

Sécurité : le state OAuth est un JWT signé (anti-CSRF) portant le tenant_id ; le callback
n'a PAS d'auth header (redirigé par le navigateur via LinkedIn) — il valide le state signé
pour retrouver le tenant. Le access_token n'est JAMAIS renvoyé par l'API (status ne expose
que member_name / member_id).
"""
import json
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

_AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_UGC_POST_URL = "https://api.linkedin.com/v2/ugcPosts"
_SCOPES = "openid profile w_member_social"
_MOCK_TOKEN = "mock-token-dev"


def _is_mock() -> bool:
    return bool(settings.linkedin_mock) or not settings.linkedin_client_id


def _state_for(tid: str) -> str:
    """JWT signé {tid, nonce, exp court} — validé au callback (anti-CSRF)."""
    payload = {"tid": tid, "nonce": str(time.time()),
               "exp": datetime.utcnow() + timedelta(minutes=10)}
    return security.jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algo)


def _verify_state(state: str) -> str:
    try:
        payload = security.jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algo])
    except security.JWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth invalide (CSRF ?)") from None
    tid = payload.get("tid")
    if not tid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "State OAuth sans tenant")
    return tid


def _upsert_connection(db: Session, tid: str, *, token: str, member_id: str, member_name: str,
                       member_headline: str, scope: str, expires_in: int) -> models.LinkedInConnection:
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    exp = datetime.utcnow() + timedelta(seconds=expires_in)
    if conn:
        conn.access_token = token
        conn.expires_at = exp
        conn.member_id = member_id
        conn.member_name = member_name
        conn.member_headline = member_headline
        conn.scope = scope
        conn.connected_at = datetime.utcnow()
    else:
        conn = models.LinkedInConnection(
            tenant_id=tid, access_token=token, expires_at=exp, member_id=member_id,
            member_name=member_name, member_headline=member_headline, scope=scope)
        db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


# ---- Appels API LinkedIn (réel) ----
def _exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
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
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.visibility.PhoneVisibilityEnum": "PUBLIC"},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(_UGC_POST_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Restli-Method", "create")
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        urn = ""
        if r.headers.get("Location"):
            urn = r.headers["Location"].split("/")[-1]
        return json.loads(body) if body else {"id": urn}


# ---- Routes ----
@router.get("/tenants/{tid}/linkedin/auth", response_model=schemas.LinkedInAuthUrl)
def linkedin_auth(tid: str, _t: models.Tenant = Depends(get_tenant),
                  _perm=Depends(require_permission("workspace.manage"))):
    """Démarre l'OAuth LinkedIn : renvoie l'URL d'autorisation (ou l'URL mock en dev)."""
    if _is_mock():
        return schemas.LinkedInAuthUrl(
            auth_url=f"/api/tenants/{tid}/linkedin/mock-connect", mock=True)
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "scope": _SCOPES,
        "state": _state_for(tid),
    }
    return schemas.LinkedInAuthUrl(
        auth_url=_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params), mock=False)


@router.get("/tenants/{tid}/linkedin/mock-connect")
def linkedin_mock_connect(tid: str, db: Session = Depends(get_db)):
    """Mode mock : crée une connexion LinkedIn factice et redirige vers le dashboard."""
    if not _is_mock():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Mode mock désactivé (creds LinkedIn présentes).")
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
    """Callback OAuth (redirigé par LinkedIn). Valide le state, échange le code, stocke la connexion."""
    tid = _verify_state(state)
    try:
        tok = _exchange_code(code)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        return RedirectResponse(url=f"/?linkedin=error&detail={urllib.parse.quote(detail)}")
    except Exception as e:  # réseau / timeout
        return RedirectResponse(url=f"/?linkedin=error&detail={urllib.parse.quote(str(e))}")
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
    return schemas.LinkedInStatus(
        connected=True, member_name=conn.member_name, member_id=conn.member_id,
        member_headline=conn.member_headline,
        expires_at=conn.expires_at.isoformat() if conn.expires_at else None,
        mock=_is_mock() or conn.access_token == _MOCK_TOKEN,
    )


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
def linkedin_publish(tid: str, aid: str, db: Session = Depends(get_db),
                     _t: models.Tenant = Depends(get_tenant),
                     _perm=Depends(require_permission("content.publish"))):
    """Publie un contenu approuvé (Approval) sur le compte LinkedIn connecté du tenant."""
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    if not conn:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Aucun compte LinkedIn connecté. Connectez-le d'abord.")
    a = db.query(models.Approval).filter_by(id=aid, tenant_id=tid).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contenu introuvable")
    if a.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Contenu non approuvé (status={a.status}). Approuvez-le avant publication.")
    text = (a.content_body or f"{a.title}\n\n{a.summary}").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Contenu vide (pas de corps à publier).")
    if _is_mock() or conn.access_token == _MOCK_TOKEN:
        urn = f"urn:li:ugcPost:mock-{int(time.time())}"
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="linkedin.publish", target_type="approval", target_id=aid,
                  detail={"published": True, "post_urn": urn, "mock": True})
        db.commit()
        return schemas.PublishResult(published=True, post_urn=urn,
                                     detail="Publication mockée (mode dev) — aucun appel réseau.")
    try:
        result = _publish_post(conn.access_token, conn.member_id, text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        return schemas.PublishResult(published=False, post_urn="",
                                     detail=f"LinkedIn API {e.code}: {detail}")
    except Exception as e:
        return schemas.PublishResult(published=False, post_urn="",
                                     detail=f"Erreur réseau : {e}")
    urn = result.get("id") or result.get("activity") or "urn:li:ugcPost:unknown"
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="linkedin.publish", target_type="approval", target_id=aid,
              detail={"published": True, "post_urn": urn, "mock": False})
    db.commit()
    return schemas.PublishResult(published=True, post_urn=urn, detail="Publié sur LinkedIn.")
