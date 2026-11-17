"""P38 — Envoi de candidatures via email (OAuth2 Gmail/Outlook officiel — v1 stub not_configured).

send_application_email(application, profile) → SendResult : envoie la candidature via l'intégration email
configurée (Gmail OAuth2 OU Outlook OAuth2). v1 : stub not_configured (comme P35 job connectors — aucune
intégration réelle active tant que OAuth credentials ne sont pas configurés). v2 : OAuth2 Gmail/Outlook
concret (google-api-python-client, msal).

Anti-spam (P38 invariant) :
  - JAMAIS de mass-email/spam — une candidature à la fois, consentement explicite du candidat
    (POST /applications/{id}/send — appel explicite, pas d'automatisation).
  - Pas de scheduler de relance non consenti (v2 : relances opt-in par candidature).
  - Rate-limit par utilisateur (max 50 candidatures/jour — anti-abus, configurable).
  - Destinataire vérifié (email valide + domaine non blacklisté — v2).

SendResult structure : {status: sent|not_configured|error, provider, message_id, sent_at, error}.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("revpulse.career.email_sender")

# Anti-abus : rate-limit par utilisateur (max 50 candidatures/jour). v2 : configurable par plan.
DAILY_SEND_LIMIT = 50

# Providers supportés (v2 : OAuth2 concret). v1 : aucun configuré.
SUPPORTED_PROVIDERS = ["gmail_oauth", "outlook_oauth"]


@dataclass
class SendResult:
    """Résultat d'envoi d'une candidature (P38). status=sent (envoyé), not_configured (v1 stub — aucune
    intégration email configurée), error (échec). provider = gmail_oauth|outlook_oauth|none."""
    status: str  # sent|not_configured|error
    provider: str = "none"
    message_id: str | None = None
    sent_at: datetime | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)


def _check_rate_limit(db, profile_id: str) -> bool:
    """Anti-abus : vérifie que le candidat n'a pas dépassé DAILY_SEND_LIMIT aujourd'hui.
    Retourne True si OK (peut envoyer), False si limite atteinte. v1 : compte les applications envoyées
    aujourd'hui (status=sent ou sent_at today)."""
    from sqlalchemy import func

    from .. import models as M
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.query(func.count(M.JobApplication.id)).filter(
        M.JobApplication.career_profile_id == profile_id,
        M.JobApplication.sent_at >= today_start,
    ).scalar() or 0
    return count < DAILY_SEND_LIMIT


def send_application_email(application, profile, db) -> SendResult:
    """Envoie une candidature via l'intégration email configurée (P38). v1 : stub not_configured.
    v2 : OAuth2 Gmail/Outlook officiel (google-api-python-client / msal).

    Anti-spam : une candidature à la fois, consentement explicite (appelé depuis POST /applications/{id}/send).
    Rate-limit : max DAILY_SEND_LIMIT par jour par candidat."""
    # Anti-abus : rate-limit.
    if not _check_rate_limit(db, application.career_profile_id):
        log.warning("Rate-limit atteint pour profile=%s (max %d/jour).", application.career_profile_id, DAILY_SEND_LIMIT)
        return SendResult(status="error", error=f"Rate-limit atteint (max {DAILY_SEND_LIMIT} candidatures/jour).")

    # Destinataire requis.
    if not application.recipient_email:
        return SendResult(status="error", error="recipient_email requis pour envoi email.")

    # v1 : aucune intégration email configurée (stub not_configured, comme P35 connectors).
    # v2 : OAuth2 Gmail/Outlook officiel — checker credentials configurés + envoyer via API.
    log.info("Envoi candidature app=%s via email (v1 stub not_configured).", application.id)
    return SendResult(
        status="not_configured",
        provider="none",
        error="Aucune intégration email configurée (v1). Configurez OAuth2 Gmail/Outlook (v2).",
        detail={"recipient": application.recipient_email, "provider_v2": SUPPORTED_PROVIDERS},
    )


def is_provider_configured(provider: str) -> bool:
    """Vérifie si un provider email est configuré (OAuth credentials). v1 : toujours False (stub)."""
    if provider not in SUPPORTED_PROVIDERS:
        return False
    # v2 : vérifier credentials OAuth (env vars GMAIL_CLIENT_ID/OUTLOOK_CLIENT_ID...).
    import os
    if provider == "gmail_oauth":
        return bool(os.getenv("GMAIL_CLIENT_ID") and os.getenv("GMAIL_CLIENT_SECRET"))
    if provider == "outlook_oauth":
        return bool(os.getenv("OUTLOOK_CLIENT_ID") and os.getenv("OUTLOOK_CLIENT_SECRET"))
    return False
