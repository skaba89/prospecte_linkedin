"""P29-V2 — Enrichissement de prospects Business (Hunter.io Email Finder API réel).

enrich_prospect(domain, first_name, last_name) → EnrichResult : enrichit un prospect CRM via Hunter.io.
v2 : API Hunter.io réelle (Email Finder). Si hunter_api_key non configuré → status "not_configured"
(simulé, JAMAIS d'email inventé — anti-fake P29 invariant).

Anti-fake (P29 invariant) :
  - JAMAIS d'email inventé — si l'API ne retourne pas d'email (score trop bas / introuvable) → status "not_found"
    avec email=None. Le commercial saisit l'email manuellement.
  - simulated=True quand not_configured (pas d'appel API) — JAMAIS présenté comme un vrai enrichment.
  - Confidence score (0-100) retourné + seuil configurable (default 70 — on n'accepte que les emails ≥ 70).

EnrichResult : {status: enriched|not_configured|not_found|error, email, confidence, position, company,
                linkedin_url, simulated, error}.

Flow :
  1. POST /api/tenants/{tid}/crm/contacts/{id}/enrich — le commercial déclenche l'enrichment explicite.
  2. enrich_prospect(contact.company.domain, contact.first_name, contact.last_name) → Hunter.io API.
  3. Si email trouvé + confidence ≥ seuil → update contact (email, title, score) + status "enriched".
  4. Sinon → status "not_found" (email=None, le commercial saisit manuellement).
  5. Anti-abus : rate-limit par tenant (max 100 enrichissements/jour — Hunter free tier 25/mois, mais
     on garde une marge + le rate-limit protège contre les abus payants).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from ..config import settings

log = logging.getLogger("revpulse.business.prospect_enrichment")

# Anti-abus : rate-limit par tenant (max 100 enrichissements/jour — Hunter free tier 25/mois,
# mais on garde une marge + protège contre les abus payants).
DAILY_ENRICH_LIMIT = 100

# Seuil de confiance (0-100) — on n'accepte que les emails ≥ 70 (anti-fake : éviter les emails devinés).
CONFIDENCE_THRESHOLD = 70

# Timeout HTTP (secondes) — anti-blocage si Hunter API lente/down.
_HTTP_TIMEOUT = 10


@dataclass
class EnrichResult:
    """Résultat d'enrichissement d'un prospect (P29-V2). status :
      - enriched : email trouvé + confidence ≥ seuil → contact mis à jour.
      - not_configured : hunter_api_key absent (simulé, AUCUN appel API, email=None).
      - not_found : API appelée mais aucun email trouvé ou confidence < seuil (email=None).
      - error : échec technique (API down, rate-limit, domaine invalide).
    """
    status: str  # enriched|not_configured|not_found|error
    email: str | None = None
    confidence: int = 0  # 0-100
    position: str | None = None
    company: str | None = None
    linkedin_url: str | None = None
    simulated: bool = False
    error: str | None = None
    detail: dict = field(default_factory=dict)


def is_hunter_configured() -> bool:
    """True si Hunter.io est configuré (HUNTER_API_KEY en config)."""
    return bool(settings.hunter_api_key)


def _check_rate_limit(db, tenant_id: str) -> bool:
    """Anti-abus : vérifie que le tenant n'a pas dépassé DAILY_ENRICH_LIMIT aujourd'hui.
    Compte les audit logs d'enrichment (action crm.contact.enrich) aujourd'hui."""
    from sqlalchemy import func

    from .. import models as M
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.query(func.count(M.AuditLog.id)).filter(
        M.AuditLog.tenant_id == tenant_id,
        M.AuditLog.action == "crm.contact.enrich",
        M.AuditLog.created_at >= today_start,
    ).scalar() or 0
    return count < DAILY_ENRICH_LIMIT


def _call_hunter_email_finder(domain: str, first_name: str, last_name: str) -> dict | None:
    """Appelle Hunter.io Email Finder API (GET /v2/email-finder).
    Retourne le payload JSON {data: {email, score, position, company, linkedin}} ou None si échec."""
    params = {
        "domain": domain,
        "first_name": first_name,
        "last_name": last_name,
        "api_key": settings.hunter_api_key,
    }
    url = f"{settings.hunter_api_base}/email-finder?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 401 = api_key invalide, 429 = rate-limit Hunter, 400 = domaine invalide
        log.warning("Hunter API HTTP %s.", e.code)
        return {"_error": f"Hunter API HTTP {e.code}"}
    except Exception as e:
        log.warning("Hunter API injoignable : %s", type(e).__name__)
        return {"_error": f"Hunter API injoignable — {type(e).__name__}"}


def enrich_prospect(domain: str, first_name: str, last_name: str, db=None, tenant_id: str | None = None) -> EnrichResult:
    """Enrichit un prospect via Hunter.io Email Finder (P29-V2).

    - hunter_api_key absent → status "not_configured" (simulé=True, email=None, AUCUN appel API).
    - API appelée → email trouvé + confidence ≥ seuil → status "enriched" + email + position + company.
    - API appelée → aucun email ou confidence < seuil → status "not_found" (email=None).
    - Échec technique (API down, rate-limit) → status "error".

    Anti-fake : JAMAIS d'email inventé. Si l'API ne retourne rien, email=None (le commercial saisit manuellement).
    """
    if not domain or not first_name or not last_name:
        return EnrichResult(status="error",
                            error="domain + first_name + last_name requis pour l'enrichment.")

    # Anti-abus : rate-limit par tenant (si db + tenant_id fournis).
    if db and tenant_id and not _check_rate_limit(db, tenant_id):
        log.warning("Rate-limit enrichment atteint pour tenant=%s (max %d/jour).", tenant_id, DAILY_ENRICH_LIMIT)
        return EnrichResult(status="error",
                            error=f"Rate-limit atteint (max {DAILY_ENRICH_LIMIT} enrichissements/jour).")

    # v1/v2 stub : Hunter non configuré → simulé (anti-fake : AUCUN email inventé).
    if not is_hunter_configured():
        log.info("Enrichment prospect %s %s @%s — Hunter non configuré (simulé).",
                 first_name, last_name, domain)
        return EnrichResult(
            status="not_configured", simulated=True,
            error="Hunter.io non configuré (HUNTER_API_KEY absent). Configurez l'API pour l'enrichment réel.",
            detail={"domain": domain, "first_name": first_name, "last_name": last_name,
                    "provider_v2": "hunter.io"},
        )

    # v2 : appel API Hunter.io Email Finder.
    payload = _call_hunter_email_finder(domain, first_name, last_name)
    if not payload:
        return EnrichResult(status="error", error="Hunter API injoignable (réponse vide).")
    if "_error" in payload:
        return EnrichResult(status="error", error=payload["_error"],
                            detail={"domain": domain})

    data = payload.get("data") or {}
    email = data.get("email")
    confidence = int(data.get("score") or 0)
    position = data.get("position")
    company = data.get("company")
    linkedin = data.get("linkedin")

    # Anti-fake : pas d'email ou confidence < seuil → not_found (JAMAIS d'email inventé/guessé).
    if not email or confidence < CONFIDENCE_THRESHOLD:
        log.info("Hunter : aucun email fiable pour %s %s @%s (confidence=%s).",
                 first_name, last_name, domain, confidence)
        return EnrichResult(
            status="not_found", email=None, confidence=confidence,
            position=position, company=company,
            error=f"Aucun email fiable (confidence={confidence} < seuil {CONFIDENCE_THRESHOLD}). Saisissez l'email manuellement.",
            detail={"domain": domain, "confidence": confidence},
        )

    # Email trouvé + confidence ≥ seuil → enriched.
    log.info("Hunter : email trouvé pour %s %s @%s (confidence=%s).",
             first_name, last_name, domain, confidence)
    return EnrichResult(
        status="enriched", email=email, confidence=confidence,
        position=position, company=company, linkedin_url=linkedin,
        detail={"domain": domain, "provider": "hunter.io"},
    )
