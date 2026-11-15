"""Sécurité : hachage bcrypt + JWT (HS256). Utilise le package bcrypt directement
(pas passlib, incompatible avec bcrypt 4.x)."""
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from .config import settings


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours)
    return jwt.encode({"sub": subject, "exp": expire}, settings.jwt_secret, algorithm=settings.jwt_algo)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algo])
    except JWTError:
        return None

