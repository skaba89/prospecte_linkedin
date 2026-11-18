"""Auth : register, login, demo, me, logout. Retourne un JWT + l'utilisateur.

P10.4 : /api/auth/demo + le compte démo sont interdits en production (404).
        Le hint d'identifiants démo sur 401 login est retiré en production.
P10.7 : /api/auth/logout révoque le JIT courant (blacklist revoked_tokens) ;
        get_current_user (deps) vérifie la révocation à chaque requête.

Rate-limiting (slowapi) sur login/register/demo : anti brute-force, configurable
via REVPULSE_LOGIN_RATE_LIMIT (défaut 5/minute), désactivable via
REVPULSE_RATE_LIMIT_ENABLED.
"""
from datetime import UTC, datetime

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, oauth2_scheme
from ..extensions import limiter
from ..i18n import get_lang, t
from ..seed import ensure_demo

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def register(request: Request, body: schemas.UserCreate, db: Session = Depends(get_db)):
    lang = get_lang(request.headers.get("accept-language"))
    email = body.email.lower()
    if db.query(models.User).filter_by(email=email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, t("auth.account_exists", lang))
    user = models.User(email=email, name=body.name, password_hash=security.hash_password(body.password),
                       locale=lang)
    db.add(user)
    db.commit()
    db.refresh(user)
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.post("/login", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def login(request: Request, body: schemas.UserLogin, db: Session = Depends(get_db)):
    lang = get_lang(request.headers.get("accept-language"))
    email = body.email.lower()
    user = db.query(models.User).filter_by(email=email).first()
    # P10.4 : réparation du compte démo + hint d'identifiants only hors production.
    if not settings.is_production and not user and email == settings.demo_email.lower():
        ensure_demo(db)
        user = db.query(models.User).filter_by(email=email).first()
    if not user or not security.verify_password(body.password, user.password_hash):
        hint = "" if settings.is_production else t("auth.demo_hint", lang, email=settings.demo_email, password=settings.demo_password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, t("auth.login_invalid", lang) + hint)
    # P62 — 2FA/TOTP : si l'utilisateur a 2FA activé, n'émet PAS le token de session complet.
    # Retourne 403 avec un temp_token (5 min) — le client doit POST /auth/login/2fa {temp_token, code}.
    if user.totp_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail={"requires_2fa": True, "temp_token": security.create_2fa_temp_token(user.id)})
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.post("/demo", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def demo(request: Request, db: Session = Depends(get_db)):
    """Accès démo en 1 clic (dev/CI). P10.4 : 404 en production."""
    lang = get_lang(request.headers.get("accept-language"))
    if settings.is_production:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("auth.demo_unavailable", lang))
    ensure_demo(db)
    user = db.query(models.User).filter_by(email=settings.demo_email.lower()).first()
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.post("/logout")
def logout(user: models.User = Depends(get_current_user), token: str = Depends(oauth2_scheme),
           db: Session = Depends(get_db)):
    """P10.7 : révoque le JIT courant (blacklist revoked_tokens). Invalidation immédiate."""
    lang = getattr(user, "locale", None) or "fr"
    payload = security.decode_token(token)
    if not payload or not payload.get("jti"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("auth.token_no_jti", lang))
    exp = payload.get("exp")
    expires_at = datetime.fromtimestamp(exp, tz=UTC).replace(tzinfo=None) if exp else datetime.utcnow()
    db.add(models.RevokedToken(jti=payload["jti"], user_id=user.id, expires_at=expires_at))
    db.commit()
    return {"revoked": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


# ─── P62 — 2FA/TOTP (RFC 6238) ───

def _totp(secret: str) -> pyotp.TOTP:
    """Construit l'objet TOTP depuis le secret base32. Issuer RevPulse, période 30s (défaut RFC 6238)."""
    return pyotp.TOTP(secret)


@router.post("/2fa/setup", response_model=schemas.TwoFASetup)
def twofa_setup(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """POST /auth/2fa/setup : génère un secret TOTP (base32), le chiffre Fernet en DB, retourne le secret
    (une fois, pour saisie manuelle) + l'URI otpauth:// (pour QR code côté frontend). totp_enabled reste
    False jusqu'à /2fa/verify (anti-lockout : setup sans verify = no-op, le secret n'est pas actif)."""
    secret = pyotp.random_base32()
    user.totp_secret_encrypted = security.encrypt_token(secret)
    db.commit()
    # otpauth URI (RFC 6238) — issuer RevPulse, account = email (lisible dans l'app authenticator)
    uri = _totp(secret).provisioning_uri(name=user.email, issuer_name="RevPulse")
    return schemas.TwoFASetup(secret=secret, otpauth_uri=uri, enabled=user.totp_enabled)


@router.post("/2fa/verify")
def twofa_verify(body: schemas.TwoFAVerify, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """POST /auth/2fa/verify : valide un code TOTP 6 digits. Si OK, active 2FA (totp_enabled=True).
    Anti-lockout : exige un code valide AVANT activation (l'utilisateur a prouvé qu'il a l'app configurée).
    Log audit auth.2fa.enable (via la 1ère membership active du user pour le tenant_id)."""
    if not user.totp_secret_encrypted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA non initialisée — appelez /auth/2fa/setup d'abord")
    secret = security.decrypt_token(user.totp_secret_encrypted)
    if not _totp(secret).verify(body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code TOTP invalide")
    user.totp_enabled = True
    db.commit()
    _log_twofa_event(db, user, "auth.2fa.enable")
    db.commit()
    return {"enabled": True, "user_id": user.id, "note": "2FA activé — le login exigera désormais un code TOTP"}


@router.delete("/2fa")
def twofa_disable(code: str = Query(..., description="Code TOTP 6 digits (anti-désactivation accidentelle)"),
                 user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """DELETE /auth/2fa?code=X : désactive 2FA. Exige un code TOTP valide (anti-désactivation accidentelle /
    par un attaquant qui aurait volé la session — sans le secret TOTP, impossible de désactiver).
    Log audit auth.2fa.disable."""
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA non activée")
    secret = security.decrypt_token(user.totp_secret_encrypted)
    if not _totp(secret).verify(code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code TOTP invalide — désactivation refusée")
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    db.commit()
    _log_twofa_event(db, user, "auth.2fa.disable")
    db.commit()
    return {"enabled": False, "user_id": user.id, "note": "2FA désactivé (secret purgé)"}


@router.post("/login/2fa", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def login_2fa(request: Request, body: schemas.TwoFALogin, db: Session = Depends(get_db)):
    """POST /auth/login/2fa : termine le login 2FA. Valide le temp_token (5 min, claim 2fa=true) + le code
    TOTP, puis émet le token de session complet via create_access_token. Rate-limité (anti brute-force code)."""
    payload = security.decode_token(body.temp_token)
    if not payload or not payload.get("2fa"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "temp_token invalide ou expiré")
    user = db.query(models.User).filter_by(id=payload["sub"]).first()
    if not user or not user.totp_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur introuvable ou 2FA désactivé")
    secret = security.decrypt_token(user.totp_secret_encrypted)
    if not _totp(secret).verify(body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code TOTP invalide")
    return schemas.Token(token=security.create_access_token(user.id), user=user)


def _log_twofa_event(db: Session, user: models.User, action: str):
    """Log audit 2FA via la 1ère membership active du user (pour le tenant_id). No-op si pas de membership."""
    from ..audit import log_audit
    m = db.query(models.Member).filter_by(user_id=user.id, status="active").first()
    if m:
        log_audit(db, tenant_id=m.tenant_id, actor_user_id=user.id, actor_name=user.name,
                  action=action, target_type="user", target_id=user.id)
