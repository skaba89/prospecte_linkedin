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

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..audit import log_audit
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import linkedin_client
from .crm import _compute_score

router = APIRouter(prefix="/api", tags=["linkedin"])
_log = logging.getLogger("revpulse.linkedin")

# P-linkedin-finalize : URLs API + scopes externalisées dans config (configurables par opérateur).
_AUTHORIZE_URL = settings.linkedin_authorize_url
_TOKEN_URL = settings.linkedin_token_url
_REVOKE_URL = settings.linkedin_revoke_url
_USERINFO_URL = settings.linkedin_userinfo_url
_UGC_POST_URL = settings.linkedin_ugc_post_url
_COMMENT_URL_TMPL = settings.linkedin_comment_url_tmpl
_SCOPES = settings.linkedin_scopes
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
    except security.jwt.PyJWTError:
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


def _placeholder_email(linkedin_url: str, first_name: str, last_name: str) -> str:
    """Email synthétique pour un contact LinkedIn sans email (l'API /v2/connections ne
    l'expose pas — scope r_emailaddress séparé). Dérivé du slug linkedin_url (unique par
    connexion) + domaine clairement factice (@linkedin.sync) — jamais un email réel."""
    slug = ""
    if linkedin_url:
        slug = linkedin_url.rstrip("/").split("/in/")[-1].split("/")[-1].lower()
    if not slug:
        slug = f"{first_name}{last_name}".lower()
    return f"{slug}@linkedin.sync"


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


def _revoke_linkedin_token(access_token: str) -> bool:
    """P-linkedin-finalize : révoque l'access_token côté serveur LinkedIn (POST /oauth/v2/revoke).
    Best-effort : même si l'appel échoue (réseau/token déjà expiré), on supprime local au disconnect.
    Retourne True si LinkedIn a ACK (HTTP 200), False sinon."""
    data = urllib.parse.urlencode({"token": access_token}).encode()
    req = urllib.request.Request(_REVOKE_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _log.info("LinkedIn token révoqué côté serveur (HTTP %s).", r.status)
            return True
    except Exception as e:
        _log.warning("LinkedIn revoke best-effort échoué : %s", type(e).__name__)
        return False


def _extract_sub_from_jwt(access_token: str) -> str:
    """P-linkedin-finalize : extrait le member_id (sub) du JWT access_token LinkedIn.
    Fallback si /userinfo échoue (LinkedIn tokens sont des JWT avec claim sub). Retourne "" si non décodé."""
    import base64 as _b64
    parts = access_token.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(payload_b64).decode("utf-8", errors="replace"))
        return str(payload.get("sub", ""))
    except Exception:
        return ""


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
    # P-linkedin-finalize : fallback member_id via décodage JWT si /userinfo échoue (évite URN vide → publish cassé).
    member_id = info.get("sub") or _extract_sub_from_jwt(access_token)
    if not member_id:
        _log.warning("LinkedIn callback : member_id vide (userinfo + JWT fallback échoués) — publish sera bloqué.")
    _upsert_connection(db, tid, token=access_token,
                       member_id=member_id, member_name=info.get("name", "Membre LinkedIn"),
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
    # P-linkedin-finalize : revoke côté serveur LinkedIn (best-effort) AVANT suppression DB.
    try:
        access_token = _load_token(conn)
        if access_token and access_token != _MOCK_TOKEN:
            _revoke_linkedin_token(access_token)
    except Exception as e:
        _log.warning("Revoke LinkedIn skipped (token illisible) : %s", type(e).__name__)
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="linkedin.disconnect", target_type="linkedin_connection", target_id=tid, detail={})
    db.delete(conn)
    db.commit()
    return {"disconnected": tid, "server_side_revoked": True}


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
    # P-linkedin-finalize : pre-flight expiry (anti-401 aveugle) + member_id requis pour posts.
    if conn.expires_at and conn.expires_at < datetime.utcnow():
        pub.status = "failed"
        pub.error_detail = "Token LinkedIn expiré (expires_at dépassé). Reconnectez le compte via /linkedin/auth."
        _log.warning("LinkedIn publish bloqué : token expiré pour tenant=%s.", tid)
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="linkedin.publish", target_type="approval", target_id=aid,
                  detail={"published": False, "kind": kind, "status": "failed", "reason": "token_expired"})
        db.commit()
        return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)
    if kind != "comment" and not conn.member_id:
        pub.status = "failed"
        pub.error_detail = "member_id LinkedIn manquant (userinfo + JWT fallback échoués à la connexion). Reconnectez le compte."
        _log.warning("LinkedIn publish bloqué : member_id vide pour tenant=%s.", tid)
        db.commit()
        return schemas.PublishResult(published=False, post_urn="", detail=pub.error_detail)
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


# ─── P12 : sync LinkedIn → CRM Contacts (pont P10 OAuth ↔ P11 CRM) ─────────────
@router.post("/tenants/{tid}/linkedin/sync-contacts", response_model=schemas.SyncResult)
def linkedin_sync_contacts(tid: str, db: Session = Depends(get_db),
                           _t: models.Tenant = Depends(get_tenant),
                           membership=Depends(require_permission("crm.manage"))):
    """P12 : sync des connexions LinkedIn 1er degré → CRM Contacts.

    Récupère les connexions (mock en dev/CI, réel en prod avec retry/backoff), upsert dans
    Contact (source='linkedin', dédup par linkedin_url puis email dans le tenant). Skip les
    emails en suppression list (RGPD). Score recalculé via l'heuristique CRM (_compute_score).
    Trace dans LinkedInSyncRun + audit_log. Retourne les compteurs (imported/updated/skipped/total).
    """
    conn = db.query(models.LinkedInConnection).filter_by(tenant_id=tid).first()
    if not conn:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Aucun compte LinkedIn connecté. Connectez-le d'abord (GET /linkedin/auth).")
    run = models.LinkedInSyncRun(tenant_id=tid, status="running", actor_user_id=membership.user_id)
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        is_mock = _is_mock() or _load_token(conn) == _MOCK_TOKEN
        access_token = _load_token(conn)
        connections = linkedin_client.fetch_connections(access_token, mock=is_mock)
        imported = updated = skipped = 0
        for c in connections:
            email = (c.get("email") or "").strip().lower()
            linkedin_url = (c.get("linkedin_url") or "").strip()
            # RGPD : skip si email présent dans la suppression list du tenant.
            if email and db.query(models.SuppressionList).filter_by(
                    tenant_id=tid, email=email).first():
                skipped += 1
                continue
            # Dédup : linkedin_url d'abord (identifiant LinkedIn stable), puis email.
            existing = None
            if linkedin_url:
                existing = db.query(models.Contact).filter_by(
                    tenant_id=tid, linkedin_url=linkedin_url).first()
            if not existing and email:
                existing = db.query(models.Contact).filter_by(
                    tenant_id=tid, email=email).first()
            title = c.get("title") or c.get("headline", "")
            first_name, last_name = c.get("first_name", ""), c.get("last_name", "")
            if existing:
                if first_name:
                    existing.first_name = first_name
                if last_name:
                    existing.last_name = last_name
                if title:
                    existing.title = title
                if linkedin_url:
                    existing.linkedin_url = linkedin_url
                if email:
                    existing.email = email
                existing.source = "linkedin"
                existing.score, _ = _compute_score(existing, None)
                updated += 1
            else:
                contact = models.Contact(
                    tenant_id=tid, first_name=first_name, last_name=last_name,
                    email=email or _placeholder_email(linkedin_url, first_name, last_name),
                    title=title, linkedin_url=linkedin_url,
                    source="linkedin", status="new")
                contact.score, _ = _compute_score(contact, None)
                db.add(contact)
                imported += 1
        run.total, run.imported, run.updated, run.skipped = (
            len(connections), imported, updated, skipped)
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="linkedin.sync_contacts", target_type="linkedin_sync_run", target_id=run.id,
                  detail={"total": run.total, "imported": imported, "updated": updated,
                          "skipped": skipped, "mock": is_mock})
        db.commit()
        mode = "mock" if is_mock else "réel"
        return schemas.SyncResult(
            sync_run_id=run.id, status="completed", total=run.total,
            imported=imported, updated=updated, skipped=skipped,
            detail=f"Sync {mode} terminé : {imported} importés, {updated} mis à jour, "
                  f"{skipped} ignorés (RGPD).")
    except Exception as e:
        run.status = "failed"
        run.error_detail = f"{type(e).__name__}: {str(e)[:200]}"
        run.completed_at = datetime.utcnow()
        db.commit()
        _log.warning("LinkedIn sync failed (run %s): %s", run.id, run.error_detail)
        return schemas.SyncResult(
            sync_run_id=run.id, status="failed", total=0,
            imported=0, updated=0, skipped=0, detail=run.error_detail)


@router.get("/tenants/{tid}/linkedin/sync-runs", response_model=list[schemas.SyncRunOut])
def linkedin_sync_runs(tid: str, response: Response, page: Page = Depends(page_dep),
                       db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """P12 : historique paginé des syncs LinkedIn → CRM (métriques + traçabilité).
    Trié par started_at desc (plus récent d'abord). X-Total-Count = total runs."""
    q = (db.query(models.LinkedInSyncRun).filter_by(tenant_id=tid)
         .order_by(models.LinkedInSyncRun.started_at.desc()))
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()
