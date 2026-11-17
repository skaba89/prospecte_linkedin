"""P35 — Framework de connecteurs job boards (architecture extensible).

base.py :
  - SearchCriteria (dataclass) : critères de recherche multicritère (keywords, location, contract_types,
    salary_min/max, radius_km, source_connectors).
  - ConnectorResult (dataclass) : résultat d'un connecteur (status ok|not_configured|error|unauthorized,
    postings, error).
  - JobConnector (ABC) : interface (name, auth_method, base_url, configured, search()).

Sécurité (P35) :
  - Un connecteur non configuré (pas de credentials) retourne status="not_configured" + 0 postings.
    JAMAIS de faux succès (postings inventés) — interdiction absolue (mission P29/P35).
  - Utilise UNIQUEMENT les API/flux/méthodes autorisés (official API, RSS, partner agreement).
    AUCUN scraping anti-bot (respect ToS LinkedIn, Indeed, etc.).
  - v2 : implémentation concrète par connecteur (OAuth LinkedIn Jobs, API Free-Work, RSS France Travail,
    partner Hays/Apec, etc.). MVP v1 : stubs configurables (status not_configured sauf si credentials).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SearchCriteria:
    """Critères de recherche multicritère (P35). keywords = texte libre (titre/mots-clés métier).
    location = localisation (ville/pays/remote). contract_types = CDI|CDD|freelance|stage|alternance.
    salary_min/max (annuel brut). radius_km = rayon autour de location. source_connectors = liste de
    connecteurs à interroger (None = tous configurés)."""
    keywords: str | None = None
    location: str | None = None
    contract_types: list[str] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    radius_km: int | None = None
    source_connectors: list[str] | None = None  # None = tous configurés

    def to_dict(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords, "location": self.location,
            "contract_types": self.contract_types, "salary_min": self.salary_min,
            "salary_max": self.salary_max, "radius_km": self.radius_km,
            "source_connectors": self.source_connectors,
        }


@dataclass
class JobPostingData:
    """Données d'une offre (transférées du connecteur → ORM JobPosting par l'orchestrateur).
    dedup_hash calculé par l'orchestrateur (sha256 title+company+location normalisé)."""
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    description: str | None = None
    contract_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "EUR"
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    external_id: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResult:
    """Résultat d'un connecteur après search(). status :
      - "ok" : recherche effectuée, postings retournés (peut être vide si 0 résultats légitimes).
      - "not_configured" : connecteur sans credentials → 0 postings (JAMAIS de faux succès).
      - "error" : erreur technique (timeout, API down) → 0 postings + error message.
      - "unauthorized" : credentials invalides / token expiré → 0 postings + error.
    """
    connector_name: str
    status: str  # ok|not_configured|error|unauthorized
    postings: list[JobPostingData] = field(default_factory=list)
    error: str | None = None
    fetched_count: int = 0


class JobConnector(ABC):
    """Interface d'un connecteur job board (P35). Chaque implémentation concrète (v2) branche l'API/flux
    officiel du provider. MVP v1 : stub retournant not_configured sauf si credentials env présents."""

    name: str = "abstract"
    auth_method: str = "none"  # oauth2|api_key|rss|partner|none
    base_url: str = ""
    credential_env: str = ""  # nom de la variable d'env contenant le credential ("" = aucun requis)

    @property
    def configured(self) -> bool:
        """True si le credential requis est présent en env. Pour RSS (aucun credential), toujours True."""
        import os
        if not self.credential_env:
            return True  # RSS / flux public (France Travail RSS, sites carrières)
        return bool(os.getenv(self.credential_env))

    @abstractmethod
    def search(self, criteria: SearchCriteria) -> ConnectorResult:
        """Recherche des offres selon criteria. Retourne ConnectorResult (status + postings).
        v1 stub : si non configuré → not_configured. Si configuré → v2 implémente l'appel API."""
        ...
