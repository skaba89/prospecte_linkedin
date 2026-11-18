"""P35 — Registre des connecteurs job boards (12 providers).

Chaque connecteur étend JobConnector. MVP v1 : stubs configurables — search() retourne :
  - "not_configured" si credential requis + absent (API-based : LinkedIn Jobs, Free-Work, Malt, etc.).
  - "ok" avec [] postings si RSS/flux public (France Travail RSS, sites carrières) — v2 implémentera
    le parsing RSS concret.
  - JAMAIS de faux postings (interdiction absolue — mission P29/P35).

Connecteurs (12) :
  1. linkedin_jobs   — LinkedIn Jobs API officielle (OAuth2 + Talent Solutions partner).
  2. freework        — Free-Work API (partner).
  3. malt            — Malt API (partner, freelances).
  4. lehibou         — LeHibou API (partner, freelances).
  5. france_travail  — France Travail (RSS public + API partner).
  6. apec            — Apec (partner, cadres).
  7. indeed          — Indeed (publisher API).
  8. hays            — Hays (partner, recrutement).
  9. wttj            — Welcome to the Jungle (API partner).
  10. upwork         — Upwork API (freelances internationaux).
  11. career_sites   — Sites carrières d'entreprises (RSS/flux publics, scraping interdit).
  12. africa         — Plateformes africaines (Emploitic, JobinfoPoint, etc. — partner v2).

Sécurité : tous utilisent UNIQUEMENT les API/flux autorisés. AUCUN scraping anti-bot. Chaque connecteur
documente sa méthode d'accès (auth_method). v2 : implémentation concrète par provider (OAuth, API key, RSS).
"""
from __future__ import annotations

from .base import ConnectorResult, JobConnector, SearchCriteria
from .france_travail import _is_configured as _ft_configured
from .france_travail import search_france_travail


class _StubConnector(JobConnector):
    """Base des stubs v1. search() retourne not_configured (si credential absent) ou ok avec [] (RSS).
    v2 : surcharger search() avec l'appel API/RSS concret."""

    def search(self, criteria: SearchCriteria) -> ConnectorResult:
        if not self.configured:
            return ConnectorResult(
                connector_name=self.name, status="not_configured",
                error=f"Credential {self.credential_env} absent — connecteur non configuré (v1 stub).",
            )
        # RSS / flux public configuré → v2 implémentera le parsing RSS. MVP v1 : ok avec 0 postings.
        return ConnectorResult(
            connector_name=self.name, status="ok", postings=[], fetched_count=0,
            error="v1 stub — RSS parsing à implémenter (v2).",
        )


class LinkedInJobsConnector(_StubConnector):
    name = "linkedin_jobs"
    auth_method = "oauth2"
    base_url = "https://api.linkedin.com/v2/jobs"
    credential_env = "REVPULSE_LINKEDIN_JOBS_TOKEN"  # OAuth2 access token (Talent Solutions partner)


class FreeWorkConnector(_StubConnector):
    name = "freework"
    auth_method = "api_key"
    base_url = "https://api.freework.com/v1/jobs"
    credential_env = "REVPULSE_FREEWORK_API_KEY"


class MaltConnector(_StubConnector):
    name = "malt"
    auth_method = "api_key"
    base_url = "https://api.malt.com/v1/jobs"
    credential_env = "REVPULSE_MALT_API_KEY"  # freelances


class LeHibouConnector(_StubConnector):
    name = "lehibou"
    auth_method = "api_key"
    base_url = "https://api.lehibou.com/v1/jobs"
    credential_env = "REVPULSE_LEHIBOU_API_KEY"  # freelances


class FranceTravailConnector(JobConnector):
    """P35-V2 — Connecteur France Travail RÉEL (API Offres d'emploi v2, OAuth2 client credentials).
    Remplace le stub v1. Délègue à services/job_connectors/france_travail.py (OAuth + HTTP + mapping).
    Si credentials absents (REVPULSE_FRANCE_TRAVAIL_CLIENT_ID/SECRET) → status not_configured (JAMAIS
    de faux postings — mission P29/P35). AUCUN scraping — API officielle uniquement."""
    name = "france_travail"
    auth_method = "oauth2"  # v2 : OAuth2 client credentials (gratuit, registration developers portal)
    base_url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    credential_env = ""  # v2 : credentials en config settings (france_travail_client_id/secret)

    @property
    def configured(self) -> bool:
        return _ft_configured()

    def search(self, criteria: SearchCriteria) -> ConnectorResult:
        return search_france_travail(criteria)


class ApecConnector(_StubConnector):
    name = "apec"
    auth_method = "partner"
    base_url = "https://api.apec.fr/v1/offres"
    credential_env = "REVPULSE_APEC_API_KEY"  # partner agreement


class IndeedConnector(_StubConnector):
    name = "indeed"
    auth_method = "api_key"
    base_url = "https://apis.indeed.com/v2/jobs"
    credential_env = "REVPULSE_INDEED_PUBLISHER_ID"  # Indeed publisher API


class HaysConnector(_StubConnector):
    name = "hays"
    auth_method = "partner"
    base_url = "https://api.hays.com/v1/jobs"
    credential_env = "REVPULSE_HAYS_API_KEY"


class WelcomeToTheJungleConnector(_StubConnector):
    name = "wttj"
    auth_method = "partner"
    base_url = "https://api.welcometothejungle.com/v1/jobs"
    credential_env = "REVPULSE_WTTJ_API_KEY"


class UpworkConnector(_StubConnector):
    name = "upwork"
    auth_method = "oauth2"
    base_url = "https://www.upwork.com/api/v3/jobs"
    credential_env = "REVPULSE_UPWORK_TOKEN"  # freelances internationaux


class CareerSitesConnector(_StubConnector):
    name = "career_sites"
    auth_method = "rss"  # sites carrières d'entreprises (flux RSS publics)
    base_url = ""  # multi-sources (URL par entreprise en config)
    credential_env = ""  # RSS public — configured=True


class AfricaJobsConnector(_StubConnector):
    name = "africa"
    auth_method = "partner"  # Emploitic, JobinfoPoint, etc. (v2 : partner agreements)
    base_url = ""  # multi-plateformes
    credential_env = "REVPULSE_AFRICA_API_KEY"


# Registre des 12 connecteurs (instances singleton).
CONNECTOR_REGISTRY: dict[str, JobConnector] = {
    "linkedin_jobs": LinkedInJobsConnector(),
    "freework": FreeWorkConnector(),
    "malt": MaltConnector(),
    "lehibou": LeHibouConnector(),
    "france_travail": FranceTravailConnector(),
    "apec": ApecConnector(),
    "indeed": IndeedConnector(),
    "hays": HaysConnector(),
    "wttj": WelcomeToTheJungleConnector(),
    "upwork": UpworkConnector(),
    "career_sites": CareerSitesConnector(),
    "africa": AfricaJobsConnector(),
}


def get_connector(name: str) -> JobConnector | None:
    """Retourne le connecteur par nom (None si inconnu)."""
    return CONNECTOR_REGISTRY.get(name)


def list_connectors() -> list[dict]:
    """Liste tous les connecteurs avec leur statut de configuration (pour UI/documentation)."""
    return [
        {"name": c.name, "auth_method": c.auth_method, "base_url": c.base_url,
         "configured": c.configured, "credential_env": c.credential_env or None}
        for c in CONNECTOR_REGISTRY.values()
    ]
