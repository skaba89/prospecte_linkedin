"""P24-A — Chiffrement au repos des secrets de signature webhook (Fernet, AES-128-CBC + HMAC).

Le secret HMAC (models.Webhook.secret) était stocké en clair (String) — une fuite DB/backup
permettait de forger des X-RevPulse-Signature valides pour tous les consumers. P24-A introduit
secret_enc (Text, Fernet-encrypted) + lazy-migration : si secret_enc est NULL + secret non vide,
on encrypte à la prochaine lecture + clear le plaintext.

Clé : env WEBHOOK_SECRET_KEY (clé Fernet base64, `Fernet.generate_key()` pour la générer). Si
absente → clé éphémère générée in-memory (dev uniquement, WARNING logué — en prod les secrets ne
survivent pas à un restart). Ne JAMAIS logger la clé elle-même.

encrypt(plaintext) → ciphertext str. decrypt(ciphertext) → plaintext str. rotate() → nouveau
plaintext (32 hex) pour l'endpoint POST /webhooks/{id}/rotate.
"""
import logging
import os
import secrets as _secrets

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("revpulse.crypto")

_FERNET: Fernet | None = None


def _get_fernet() -> Fernet:
    """Fernet singleton. Clé depuis WEBHOOK_SECRET_KEY (env) ou éphémère (dev, WARNING)."""
    global _FERNET
    if _FERNET is None:
        key = os.getenv("WEBHOOK_SECRET_KEY")
        if key:
            try:
                _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
            except (ValueError, TypeError) as e:
                raise RuntimeError(
                    f"WEBHOOK_SECRET_KEY invalide (clé Fernet base64 attendue) : {e}") from e
        else:
            _FERNET = Fernet(Fernet.generate_key())
            log.warning(
                "WEBHOOK_SECRET_KEY non défini — clé éphémère générée (dev uniquement). "
                "En prod : définir WEBHOOK_SECRET_KEY (Fernet.generate_key()) pour la persistance "
                "des secrets chiffrés à travers les restarts.")
    return _FERNET


def encrypt(plaintext: str) -> str:
    """Chiffre un plaintext → ciphertext Fernet (str). Jamais None."""
    if not plaintext:
        raise ValueError("encrypt: plaintext vide")
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Déchiffre un ciphertext Fernet → plaintext. Lève InvalidToken si corrompu/mauvaise clé."""
    if not ciphertext:
        raise ValueError("decrypt: ciphertext vide")
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def new_secret() -> str:
    """Génère un nouveau secret HMAC 32 hex (128 bits) — pour la rotation."""
    return _secrets.token_hex(16)


def is_encrypted(value: str | None) -> bool:
    """True si la valeur ressemble à un ciphertext Fernet (commence par 'gAAAAA' base64). Heuristique
    pour distinguer secret_enc (chiffré) de secret (plaintext legacy) sans tenter de decrypt."""
    return bool(value) and value.startswith("gAAAAA")


def safe_decrypt(value: str | None) -> str | None:
    """Déchiffre en tolérant les erreurs (InvalidToken → None). Pour le debug/audit, pas le hot path."""
    if not value:
        return None
    try:
        return decrypt(value)
    except (InvalidToken, ValueError):
        return None
