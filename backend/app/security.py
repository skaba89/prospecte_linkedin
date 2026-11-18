"""Sécurité : hachage bcrypt, JWT (HS256 + jti pour révocation), chiffrement Fernet des
tokens OAuth (rotation de clé via REVPULSE_TOKEN_ENCRYPTION_KEY_PREVIOUS), content_hash
(dédup des publications). Utilise bcrypt directement (pas passlib, incompatible bcrypt 4.x).

P32 : migration python-jose → PyJWT (PyJWT 2.12.1 — maintenance active, jose non maintenu).
PyJWT expose jwt.PyJWTError (base des erreurs encode/decode) + jwt.encode/decode (même API que jose)."""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str) -> str:
    """JWT {sub, exp, jti}. Le jti (unique) permet la révocation (blacklist)."""
    expire = datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours)
    return jwt.encode(
        {"sub": subject, "exp": expire, "jti": secrets.token_urlsafe(16)},
        settings.jwt_secret,
        algorithm=settings.jwt_algo,
    )


def create_2fa_temp_token(subject: str) -> str:
    """P62 — JWT éphémère (5 min) pour le login 2FA. Claim `2fa: true` distingue ce temp token du
    token de session complet (le /auth/login/2fa le valide + exige le code TOTP avant d'émettre le
    vrai token via create_access_token). Court TTL → limite la fenêtre d'attaque si intercepté."""
    expire = datetime.now(UTC) + timedelta(minutes=5)
    return jwt.encode(
        {"sub": subject, "exp": expire, "2fa": True, "jti": secrets.token_urlsafe(16)},
        settings.jwt_secret,
        algorithm=settings.jwt_algo,
    )


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algo])
    except jwt.PyJWTError:
        return None


# ---- Chiffrement des tokens OAuth (Fernet, rotation de clé) ----
def _fernet(key: str) -> Fernet | None:
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def encrypt_token(plaintext: str) -> str:
    """Chiffre un token OAuth avec la clé courante. ValueError si aucune clé valide."""
    f = _fernet(settings.token_encryption_key)
    if f is None:
        raise ValueError("REVPULSE_TOKEN_ENCRYPTION_KEY absent/invalide — chiffrement impossible.")
    return f.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Déchiffre un token OAuth. Gère la rotation : clé courante puis précédente.
    Pour les valeurs legacy non-Fernet (dev sans clé), retourne tel quel — utile en CI."""
    if not ciphertext:
        return ""
    f = _fernet(settings.token_encryption_key)
    if f is not None:
        try:
            return f.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            pass
    fp = _fernet(settings.token_encryption_key_previous)
    if fp is not None:
        try:
            return fp.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            pass
    # legacy plaintext (dev/test sans clé Fernet) — retour tel quel si pas un token Fernet.
    if not ciphertext.startswith("gAAAA"):
        return ciphertext
    raise ValueError("Token OAuth indéchiffrable (clé manquante ou incorrecte).")


def content_hash(text: str) -> str:
    """sha256 du texte normalisé (strip + lower) pour la dédup des publications."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()
