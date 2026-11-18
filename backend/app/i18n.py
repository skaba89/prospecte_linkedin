"""P45 — i18n foundation : traductions FR/EN/ES + helper t(key, lang) + détection Accept-Language.

Approche légère (sans Babel/gettext — pas de nouvelle dépendance) : un catalogue de messages
pour les erreurs API les plus courantes (auth + quelques routers clés). L'infrastructure est
extensible : ajouter une clé dans MESSAGES pour les 3 langues suffit.

La détection de langue :
  1. Header Accept-Language (q-values) — standard HTTP.
  2. Fallback settings.default_language (défaut "fr").

P45 scope : auth.py messages + infrastructure. Migration complète des 700+ strings hardcoded
FR (23 routers) = v5 (t() appelé partout). Le User.locale (P45) permet de persister la langue
préférée par user (future surcharge Accept-Language).
"""
from __future__ import annotations

from typing import Optional

from .config import settings

SUPPORTED_LANGS = ("fr", "en", "es")
DEFAULT_LANG = "fr"

# Catalogue de messages — P45 : auth + messages communs. v5 : étendre à tous les routers.
MESSAGES: dict[str, dict[str, str]] = {
    # ── auth.py ────────────────────────────────────────────────────────────
    "auth.account_exists": {
        "fr": "Un compte existe déjà avec cet e-mail",
        "en": "An account already exists with this email",
        "es": "Ya existe una cuenta con este correo electrónico",
    },
    "auth.login_invalid": {
        "fr": "Identifiants invalides.",
        "en": "Invalid credentials.",
        "es": "Credenciales no válidas.",
    },
    "auth.demo_unavailable": {
        "fr": "Compte démo indisponible en production.",
        "en": "Demo account unavailable in production.",
        "es": "Cuenta demo no disponible en producción.",
    },
    "auth.token_no_jti": {
        "fr": "Token sans jti — non révocable.",
        "en": "Token without jti — not revocable.",
        "es": "Token sin jti — no revocable.",
    },
    "auth.demo_hint": {
        "fr": " Démo : {email} / {password}",
        "en": " Demo: {email} / {password}",
        "es": " Demo: {email} / {password}",
    },
    # ── sso.py (P45) ──────────────────────────────────────────────────────
    "sso.not_configured": {
        "fr": "SSO non configuré pour ce tenant.",
        "en": "SSO not configured for this tenant.",
        "es": "SSO no configurado para este tenant.",
    },
    "sso.state_invalid": {
        "fr": "État SSO invalide ou expiré (anti-CSRF).",
        "en": "Invalid or expired SSO state (anti-CSRF).",
        "es": "Estado SSO inválido o expirado (anti-CSRF).",
    },
    "sso.code_exchange_failed": {
        "fr": "Échange du code SSO échoué auprès du fournisseur d'identité.",
        "en": "SSO code exchange failed at the identity provider.",
        "es": "Intercambio de código SSO fallido en el proveedor de identidad.",
    },
    "sso.userinfo_failed": {
        "fr": "Récupération des infos utilisateur SSO échouée.",
        "en": "SSO userinfo retrieval failed.",
        "es": "Recuperación de información de usuario SSO fallida.",
    },
    "sso.domain_not_allowed": {
        "fr": "Domaine e-mail non autorisé pour ce tenant SSO (domaines : {domains}).",
        "en": "Email domain not allowed for this SSO tenant (domains: {domains}).",
        "es": "Dominio de correo no autorizado para este tenant SSO (dominios: {domains}).",
    },
    "sso.no_email": {
        "fr": "Le fournisseur SSO n'a pas retourné d'adresse e-mail (scope email requis).",
        "en": "SSO provider did not return an email address (email scope required).",
        "es": "El proveedor SSO no devolvió un correo (scope email requerido).",
    },
    "sso.success": {
        "fr": "Authentification SSO réussie.",
        "en": "SSO authentication successful.",
        "es": "Autenticación SSO exitosa.",
    },
}


def _normalize_lang(lang: Optional[str]) -> str:
    """Normalise un code langue ('fr-FR' → 'fr', 'en-US' → 'en'). Fallback DEFAULT_LANG."""
    if not lang:
        return DEFAULT_LANG
    code = lang.strip().lower().split("-")[0].split("_")[0]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def get_lang(accept_language: Optional[str] = None) -> str:
    """Détecte la langue depuis le header Accept-Language (q-values) → fallback default_language.
    Format Accept-Language : 'fr-FR,fr;q=0.9,en;q=0.8' → 'fr'."""
    if not accept_language:
        return _normalize_lang(getattr(settings, "default_language", DEFAULT_LANG))
    # Parse q-values : [(lang, q), ...] trié par q décroissant.
    parts = []
    for item in accept_language.split(","):
        item = item.strip()
        if not item:
            continue
        if ";q=" in item:
            lang_part, q_part = item.split(";q=", 1)
            try:
                q = float(q_part)
            except ValueError:
                q = 1.0
        else:
            lang_part, q = item, 1.0
        parts.append((lang_part, q))
    parts.sort(key=lambda x: x[1], reverse=True)
    for lang_part, _ in parts:
        norm = _normalize_lang(lang_part)
        if norm in SUPPORTED_LANGS:
            return norm
    return _normalize_lang(getattr(settings, "default_language", DEFAULT_LANG))


def t(key: str, lang: Optional[str] = None, **fmt) -> str:
    """Traduit une clé vers la langue (défaut fr). Formatage par kwargs (**fmt).
    Si la clé n'existe pas → retourne la clé elle-même (jamais d'exception en prod)."""
    lang = _normalize_lang(lang) if lang else DEFAULT_LANG
    entry = MESSAGES.get(key)
    if not entry:
        return key
    msg = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return msg.format(**fmt) if fmt else msg
