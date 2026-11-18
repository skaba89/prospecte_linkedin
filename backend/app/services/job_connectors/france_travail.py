"""P35-V2 — Connecteur France Travail RÉEL (API Offres d'emploi v2).

Implémentation concrète du connecteur france_travail (remplace le stub v1). Utilise l'API officielle
France Travail (Pôle Emploi) — gratuite, OAuth2 client credentials (pas de partner agreement requis).

Authentification :
  1. POST {token_url} (Content-Type: application/x-www-form-urlencoded)
     body: grant_type=client_credentials&client_id=...&client_secret=...&scope=api_offresdemploiv2 o2dsoffre
  2. Response: {"access_token": "...", "expires_in": 1799, "token_type": "Bearer"}
  3. Token mis en cache (module-level) jusqu'à expiration (refresh auto).

Recherche :
  GET {base_url} (Authorization: Bearer {token})
  Params: motsCles (keywords), commune (location INSEE code — v2 geocoding), typeContrat (CDI=CDDI,
  CDD=CDD, MISC=freelance, CCE=alternance), range (0-149 max par page).
  Response: {"resultats": [{id, intitule, entreprise.nom, lieuTravail.libelle, typeContrat,
  salaire.libelle, dateCreation, url, description, ...}]}

Mapping → JobPostingData :
  intitule → title, entreprise.nom → company, lieuTravail.libelle → location, url → url
  (origineOffre.urlOrigine fallback), description → description (HTML strippé), typeContrat →
  contract_type (normalisé CDI|CDD|freelance|stage|alternance), salaire.libelle → parse salary
  min/max, dateCreation (epoch ms) → posted_at.

Sécurité (mission P29/P35) :
  - Si credentials absents → status "not_configured" + 0 postings (JAMAIS de faux succès).
  - Si API down/timeout → status "error" + 0 postings + error message.
  - Si 401 (token invalide/expiré) → status "unauthorized" + 0 postings.
  - AUCUN scraping — uniquement l'API officielle (respect ToS).
  - HTTP via urllib (stdlib, pas de dépendance externe). Timeout 10s (anti-blocage).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ...config import settings
from .base import ConnectorResult, JobPostingData, SearchCriteria

# Cache du token OAuth (module-level singleton). France Travail token expire ~30min.
# Évite de re-demander un token à chaque search(). Thread-safe via GIL (single-thread uvicorn workers).
_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": 0.0}

# Mapping typeContrat France Travail → RevPulse (CDI|CDD|freelance|stage|alternance)
_CONTRACT_MAP = {
    "CDI": "CDI", "CDD": "CDD", "CDDI": "CDI",  # CDDI = Contrat à Durée Déterminée Insertion
    "MISC": "freelance",  # MISC = misc (souvent freelance/indépendant)
    "CCE": "alternance",  # CCE = Contrat de Professionnalisation/Apprentissage
    "SAI": "stage",       # SAI = saisonnier (proche stage pour mapping)
    "DIN": "freelance",   # DIN = indépendant
}

# Timeout HTTP (secondes) — anti-blocage si API France Travail lente/down.
_HTTP_TIMEOUT = 10


def _is_configured() -> bool:
    """True si client_id + client_secret sont renseignés en config."""
    return bool(settings.france_travail_client_id and settings.france_travail_client_secret)


def _get_token() -> str | None:
    """Récupère un token OAuth2 France Travail (cache + refresh si expiré).
    Retourne le token ou None si échec (credentials absents/invalides).
    """
    if not _is_configured():
        return None
    now = time.time()
    # Token encore valide (marge 60s pour éviter race condition)
    if _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["access_token"]
    # Demander un nouveau token
    try:
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": settings.france_travail_client_id,
            "client_secret": settings.france_travail_client_secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        }).encode("utf-8")
        req = urllib.request.Request(
            settings.france_travail_token_url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 1800)
        if not token:
            return None
        _TOKEN_CACHE["access_token"] = token
        _TOKEN_CACHE["expires_at"] = now + expires_in
        return token
    except Exception:
        # Token invalide/échec réseau → invalidate cache
        _TOKEN_CACHE["access_token"] = ""
        _TOKEN_CACHE["expires_at"] = 0.0
        return None


def _strip_html(text: str | None) -> str | None:
    """Strip les balises HTML (la description France Travail est en HTML). Garde le texte brut."""
    if not text:
        return None
    # Remplace <p>, <br>, <li> par des newlines, puis strip les autres balises
    cleaned = re.sub(r"<(?:p|br|/p|/li|li)[^>]*>", "\n", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"&nbsp;", " ", cleaned)
    cleaned = re.sub(r"&amp;", "&", cleaned)
    cleaned = re.sub(r"&lt;", "<", cleaned)
    cleaned = re.sub(r"&gt;", ">", cleaned)
    return cleaned.strip()[:5000] if cleaned.strip() else None  # cap 5000 chars (anti-bloat)


def _parse_salary(libelle: str | None) -> tuple[int | None, int | None]:
    """Parse le libellé salaire France Travail (ex. '45 000 - 55 000 Euros/Année').
    Retourne (min, max) en EUR annuel. None si non parsable."""
    if not libelle:
        return (None, None)
    # Cherche des nombres (avec espaces comme séparateurs de milliers)
    nums = re.findall(r"[\d\s]{2,}", libelle.replace("\xa0", " "))
    nums = [int(n.replace(" ", "")) for n in nums if n.strip() and n.replace(" ", "").isdigit()]
    if not nums:
        return (None, None)
    # Heuristique : si 2 nombres → min/max, si 1 → juste min
    if len(nums) >= 2:
        return (min(nums), max(nums))
    return (nums[0], None)


def _parse_date_creation(epoch_ms: int | None) -> datetime | None:
    """Convertit dateCreation (epoch ms) → datetime UTC."""
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, OSError):
        return None


def _map_offre(offre: dict[str, Any]) -> JobPostingData:
    """Mappe une offre France Travail (JSON API) → JobPostingData."""
    intitule = offre.get("intitule") or "Offre sans titre"
    entreprise = offre.get("entreprise") or {}
    company = entreprise.get("nom") if isinstance(entreprise, dict) else None
    lieu = offre.get("lieuTravail") or {}
    location = lieu.get("libelle") if isinstance(lieu, dict) else None
    # URL : origineOffre.urlOrigine (page candidat) ou construction fallback
    origine = offre.get("origineOffre") or {}
    url = origine.get("urlOrigine") if isinstance(origine, dict) else None
    if not url:
        url = f"https://candidat.francetravail.fr/offres/recherche/detail/{offre.get('id', '')}"
    description = _strip_html(offre.get("description"))
    contract_raw = offre.get("typeContrat") or ""
    contract_type = _CONTRACT_MAP.get(contract_raw.upper(), None)
    salaire = offre.get("salaire") or {}
    sal_libelle = salaire.get("libelle") if isinstance(salaire, dict) else None
    salary_min, salary_max = _parse_salary(sal_libelle)
    posted_at = _parse_date_creation(offre.get("dateCreation"))
    return JobPostingData(
        title=intitule, company=company, location=location, url=url,
        description=description, contract_type=contract_type,
        salary_min=salary_min, salary_max=salary_max, currency="EUR",
        posted_at=posted_at, external_id=offre.get("id"),
        raw_data=offre,
    )


def _build_params(criteria: SearchCriteria) -> dict[str, str]:
    """Mappe SearchCriteria → params API France Travail v2.
    motsCles = keywords. typeContrat = code France Travail (CDI, CDD, MISC, CCE).
    location : v2 utilise commune (code INSEE) — v1 passe en motsCles (texte) si pas de code.
    range = 0-149 (150 offres max par page, API cap)."""
    params: dict[str, str] = {"range": "0-149"}
    if criteria.keywords:
        params["motsCles"] = criteria.keywords
    if criteria.location:
        # France Travail v2 attend un code INSEE commune. v1 MVP : on passe le texte dans motsCles
        # (recherche full-text). v2 geocoding : convertir ville → code INSEE via API geo.api.gouv.fr.
        params["motsCles"] = f"{params.get('motsCles', '')} {criteria.location}".strip()
    # Mapping contract_types RevPulse → codes France Travail
    if criteria.contract_types:
        ft_codes = []
        for ct in criteria.contract_types:
            ct_lower = ct.lower()
            if ct_lower == "cdi":
                ft_codes.append("CDI")
            elif ct_lower == "cdd":
                ft_codes.append("CDD")
            elif ct_lower == "freelance":
                ft_codes.append("MISC")
            elif ct_lower == "alternance":
                ft_codes.append("CCE")
            elif ct_lower == "stage":
                ft_codes.append("SAI")
        if ft_codes:
            params["typeContrat"] = ft_codes[0]  # API accepte 1 typeContrat par requête
    return params


def search_france_travail(criteria: SearchCriteria) -> ConnectorResult:
    """Recherche réelle sur l'API France Travail Offres d'emploi v2.
    - Si credentials absents → status "not_configured" + 0 postings (JAMAIS de faux succès).
    - Si token échec → status "unauthorized".
    - Si API error/timeout → status "error".
    - Sinon → status "ok" + postings mappés (peut être vide si 0 résultats légitimes).
    """
    name = "france_travail"
    if not _is_configured():
        return ConnectorResult(
            connector_name=name, status="not_configured",
            error="REVPULSE_FRANCE_TRAVAIL_CLIENT_ID/SECRET absents — connecteur non configuré.",
        )
    token = _get_token()
    if not token:
        return ConnectorResult(
            connector_name=name, status="unauthorized",
            error="Token OAuth France Travail invalide/échec authentification.",
        )
    params = _build_params(criteria)
    query = urllib.parse.urlencode(params)
    url = f"{settings.france_travail_base_url}?{query}"
    try:
        req = urllib.request.Request(
            url, method="GET",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            status_code = resp.status
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Token expiré/invalidé → invalidate cache pour retry au prochain appel
            _TOKEN_CACHE["access_token"] = ""
            _TOKEN_CACHE["expires_at"] = 0.0
            return ConnectorResult(connector_name=name, status="unauthorized",
                                   error=f"France Travail API 401 — token invalide.")
        return ConnectorResult(connector_name=name, status="error",
                               error=f"France Travail API HTTP {e.code} — {e.reason}")
    except Exception as e:
        return ConnectorResult(connector_name=name, status="error",
                               error=f"France Travail API injoignable — {type(e).__name__}: {e}")
    # Mapping des résultats
    resultats = payload.get("resultats") or []
    postings = [_map_offre(offre) for offre in resultats]
    return ConnectorResult(
        connector_name=name, status="ok", postings=postings, fetched_count=len(postings),
    )
