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

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
