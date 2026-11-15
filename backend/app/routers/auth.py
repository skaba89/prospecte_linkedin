"""Auth : register, login, demo, me. Retourne un JWT + l'utilisateur.

Rate-limiting (slowapi) sur login/register/demo : anti brute-force, configurable
via REVPULSE_LOGIN_RATE_LIMIT (défaut 5/minute), désactivable via
REVPULSE_RATE_LIMIT_ENABLED.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..extensions import limiter
from ..seed import ensure_demo

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def register(request: Request, body: schemas.UserCreate, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(models.User).filter_by(email=email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Un compte existe déjà avec cet e-mail")
    user = models.User(email=email, name=body.name, password_hash=security.hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.post("/login", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def login(request: Request, body: schemas.UserLogin, db: Session = Depends(get_db)):
    email = body.email.lower()
    user = db.query(models.User).filter_by(email=email).first()
    # Répare le compte démo à la volée si l'utilisateur saisit les identifiants démo
    if not user and email == "demo@revpulse.io":
        user = ensure_demo(db)
        user = db.query(models.User).filter_by(email=email).first()
    if not user or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides. Démo : demo@revpulse.io / demo1234")
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.post("/demo", response_model=schemas.Token)
@limiter.limit(settings.login_rate_limit)
def demo(request: Request, db: Session = Depends(get_db)):
    """Accès démo en 1 clic : garantit le seed puis connecte Alex Démo."""
    ensure_demo(db)
    user = db.query(models.User).filter_by(email="demo@revpulse.io").first()
    return schemas.Token(token=security.create_access_token(user.id), user=user)


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user
