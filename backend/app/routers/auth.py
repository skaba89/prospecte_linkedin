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
    """POST /auth/2fa/verify : valide un code TOTP 6 digits. Si OK, active 2FA (totp_enabled=True) +
    génère 10 backup codes 1-use (P66 A3 — anti-lockout : perte téléphone = récupération via backup code).
    Anti-lockout : exige un code valide AVANT activation (l'utilisateur a prouvé qu'il a l'app configurée).
    Log audit auth.2fa.enable (via la 1ère membership active du user pour le tenant_id)."""
    if not user.totp_secret_encrypted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA non initialisée — appelez /auth/2fa/setup d'abord")
    secret = security.decrypt_token(user.totp_secret_encrypted)
    if not _totp(secret).verify(body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code TOTP invalide")
    user.totp_enabled = True
    # P66 (A3) — génère 10 backup codes 1-use (format XXXX-XXXX-XXXX, hashs bcrypt stockés en DB).
    # Retournés UNE FOIS ici (l'utilisateur doit les stocker hors-ligne — anti-lockout 2FA).
    import json as _json
    import secrets as _secrets

    def _gen_code() -> str:
        return "-".join(_secrets.token_hex(2).upper() for _ in range(3))  # ex. A1B2-C3D4-E5F6

    backup_codes = [_gen_code() for _ in range(10)]
    user.backup_codes_hashed = _json.dumps([security.hash_password(c) for c in backup_codes])
    db.commit()
    _log_twofa_event(db, user, "auth.2fa.enable")
    db.commit()
    return {"enabled": True, "user_id": user.id,
            "backup_codes": backup_codes,
            "note": "2FA activé + 10 backup codes générés (1-use, stockez-les hors-ligne — anti-lockout)"}


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
    user.backup_codes_hashed = None  # P66 : purge backup codes (2FA désactivé = codes obsolètes)
    db.commit()
    _log_twofa_event(db, user, "auth.2fa.disable")
    db.commit()
    return {"enabled": False, "user_id": user.id, "note": "2FA désactivé (secret + backup codes purgés)"}


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


# ─── P66 — Auth P0 : forgot/reset password (A2) + 2FA backup codes login (A3) ───

@router.post("/forgot-password")
@limiter.limit(settings.login_rate_limit)
def forgot_password(request: Request, body: schemas.PasswordForgot, db: Session = Depends(get_db)):
    """POST /auth/forgot-password : génère un token de reset (15 min), stocke son hash bcrypt + expiry.
    Anti-énumération : retourne toujours 200 (même si email inconnu — ne leak pas l'existence du compte).
    En dev/test (smtp_enabled=false) : retourne le token en clair (pour tests). En prod : envoie par email
    via SMTP (le token n'est JAMAIS retourné dans la response en prod)."""
    import secrets as _secrets
    from datetime import timedelta
    user = db.query(models.User).filter_by(email=body.email.lower()).first()
    reset_token = _secrets.token_urlsafe(32)
    if user:
        user.password_reset_token_hash = security.hash_password(reset_token)
        user.password_reset_expires_at = datetime.now(UTC) + timedelta(minutes=15)
        db.commit()
        if settings.smtp_enabled:
            # Prod : envoi par email (le token n'est pas retourné dans la response).
            from ..mailer import send_email
            send_email(to=user.email, subject="Réinitialisation mot de passe RevPulse",
                       body=f"Token de reset (valide 15 min) : {reset_token}")
    # Anti-énumération : même response que l'email existe ou non.
    if not settings.is_production and user:
        return {"sent": True, "reset_token": reset_token, "note": "dev/test — token retourné (prod enverrait par email)"}
    return {"sent": True, "note": "Si l'email existe, un lien de reset a été envoyé (valide 15 min)."}


@router.post("/reset-password")
@limiter.limit(settings.login_rate_limit)
def reset_password(request: Request, body: schemas.PasswordReset, db: Session = Depends(get_db)):
    """POST /auth/reset-password {token, new_password} : valide le token (hash compare + non-expiré),
    update password_hash, clear token (single-use). 400 si token invalide/expiré. Anti-replay."""
    user = db.query(models.User).filter(
        models.User.password_reset_token_hash.isnot(None)
    ).first()
    # Parcourir les users avec un token pending (peu nombreux) — compare hash bcrypt.
    candidates = db.query(models.User).filter(models.User.password_reset_token_hash.isnot(None)).all()
    user = next((u for u in candidates if security.verify_password(body.token, u.password_reset_token_hash)), None)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token invalide ou déjà utilisé")
    if user.password_reset_expires_at and datetime.now(UTC) > user.password_reset_expires_at.replace(tzinfo=UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token expiré (15 min) — redemandez un reset")
    user.password_hash = security.hash_password(body.new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    db.commit()
    return {"reset": True, "user_id": user.id, "note": "Mot de passe réinitialisé — token consommé (single-use)"}


@router.post("/login/2fa/backup", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def login_2fa_backup(request: Request, body: schemas.TwoFALogin, db: Session = Depends(get_db)):
    """POST /auth/login/2fa/backup {temp_token, code} : termine le login 2FA avec un BACKUP CODE (1-use)
    au lieu d'un code TOTP. Pour la perte de téléphone (anti-lockout). Valide temp_token (claim 2fa=true) +
    consume le backup code (hash compare + remove de la liste). Émet le token de session complet."""
    import json as _json
    payload = security.decode_token(body.temp_token)
    if not payload or not payload.get("2fa"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "temp_token invalide ou expiré")
    user = db.query(models.User).filter_by(id=payload["sub"]).first()
    if not user or not user.totp_enabled or not user.backup_codes_hashed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA non activé ou aucun backup code")
    codes_hashed = _json.loads(user.backup_codes_hashed)
    matched_idx = -1
    for i, h in enumerate(codes_hashed):
        if security.verify_password(body.code, h):
            matched_idx = i
            break
    if matched_idx < 0:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Backup code invalide")
    # Consume : retire le code utilisé de la liste (1-use).
    codes_hashed.pop(matched_idx)
    user.backup_codes_hashed = _json.dumps(codes_hashed) if codes_hashed else None
    db.commit()
    return schemas.Token(token=security.create_access_token(user.id), user=user)
