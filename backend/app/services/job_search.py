"""P35 — Orchestrateur de recherche d'opportunités.

search_jobs(db, criteria) :
  - Interroge les connecteurs configurés (registry.CONNECTOR_REGISTRY) en parallèle logique.
  - Pour chaque ConnectorResult ok : persiste les JobPostingData en JobPosting (dedup via hash).
  - Déduplication cross-connecteurs : dedup_hash = sha256(title+company+location normalisé). Une même
    offre chez 2 connecteurs = 2 rows (source_connector différent) mais même dedup_hash (détectable).
  - Détecte les offres expirées : si posted_at + âge > EXPIRY_DAYS (sans expires_at explicite) → is_expired.
  - Retourne SearchResponse (postings actifs + stats par connecteur + status par connecteur).

Sécurité (P35) :
  - Aucun posting inventé : seuls les postings retournés par un connecteur ok sont persistés.
  - Connecteurs not_configured → 0 postings (status visible dans la réponse, pas masqué).
  - raw_data stocké (audit/re-parse) — description tronquée à 2000 chars anti-abus.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from .job_connectors import SearchCriteria, get_connector
from .job_connectors.registry import CONNECTOR_REGISTRY

log = logging.getLogger("revpulse.career.search")

# Âge max d'une offre sans expires_at avant marquage expired (heuristic — 30 jours).
EXPIRY_HEURISTIC_DAYS = 30
# Description max (anti-abus — tronquée à la persistence).
DESC_MAX_LEN = 2000


def dedup_hash(title: str, company: str | None, location: str | None) -> str:
    """sha256(title + company + location normalisé) — déduplication cross-connecteurs.
    Normalisation : strip + lower + collapse whitespace (anti-variantes mineures)."""
    import re
    def norm(s: str | None) -> str:
        if not s:
            return ""
        return re.sub(r"\s+", " ", s.strip().lower())
    raw = f"{norm(title)}|{norm(company)}|{norm(location)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_posting(data, connector_name: str) -> models.JobPosting:
    """Convertit un JobPostingData (connecteur) → models.JobPosting (ORM). dedup_hash calculé ici."""
    h = dedup_hash(data.title, data.company, data.location)
    desc = (data.description or "")[:DESC_MAX_LEN]
    return models.JobPosting(
        dedup_hash=h,
        source_connector=connector_name,
        external_id=data.external_id,
        title=data.title,
        company=data.company,
        location=data.location,
        url=data.url,
        description=desc or None,
        contract_type=data.contract_type,
        salary_min=data.salary_min,
        salary_max=data.salary_max,
        currency=data.currency,
        posted_at=data.posted_at,
        expires_at=data.expires_at,
        fetched_at=datetime.utcnow(),
        is_expired=False,
        raw_data=data.raw_data or None,
    )


def _is_heuristic_expired(posting: models.JobPosting) -> bool:
    """Heuristic expired : si expires_at explicite → si passé. Sinon si posted_at > EXPIRY_HEURISTIC_DAYS."""
    now = datetime.utcnow()
    if posting.expires_at and posting.expires_at < now:
        return True
    if posting.posted_at and posting.posted_at < now - timedelta(days=EXPIRY_HEURISTIC_DAYS):
        return True
    return False


def detect_expired(db: Session) -> int:
    """Marque les offres expirées (heuristic). Retourne le nb d'offres nouvellement marquées expired.
    À appeler périodiquement (v2 : scheduler cron) ou avant chaque search."""
    active = db.query(models.JobPosting).filter_by(is_expired=False).all()
    marked = 0
    for p in active:
        if _is_heuristic_expired(p):
            p.is_expired = True
            marked += 1
    if marked:
        db.commit()
        log.info("detect_expired : %d offres marquées expirées (heuristic %d jours).", marked, EXPIRY_HEURISTIC_DAYS)
    return marked


def search_jobs(db: Session, criteria: SearchCriteria) -> dict[str, Any]:
    """Exécute la recherche multicritère across les connecteurs configurés. Persiste les postings.
    Retourne {postings, connector_statuses, total, expired_marked}.

    Un connecteur non configuré → status "not_configured" (visible, 0 postings — JAMAIS masqué).
    Un connecteur ok → postings persistés (dedup via hash, upsert sur dedup_hash+source_connector)."""
    # Détection expired avant recherche (rafraîchit le cache).
    expired_marked = detect_expired(db)

    # Sélection des connecteurs à interroger (tous si criteria.source_connectors est None).
    if criteria.source_connectors:
        names = [n for n in criteria.source_connectors if get_connector(n)]
    else:
        names = list_connectors_name()

    connector_statuses: list[dict] = []
    new_postings: list[models.JobPosting] = []

    for name in names:
        connector = get_connector(name)
        if not connector:
            connector_statuses.append({"name": name, "status": "unknown", "count": 0,
                                       "error": f"Connecteur inconnu : {name}"})
            continue
        result = connector.search(criteria)
        connector_statuses.append({
            "name": name, "status": result.status, "count": len(result.postings),
            "error": result.error, "configured": connector.configured,
        })
        if result.status == "ok":
            for pdata in result.postings:
                posting = _to_posting(pdata, name)
                # Upsert : si (dedup_hash, source_connector) existe déjà → refresh fetched_at + raw_data.
                existing = db.query(models.JobPosting).filter_by(
                    dedup_hash=posting.dedup_hash, source_connector=name).first()
                if existing:
                    existing.fetched_at = datetime.utcnow()
                    existing.is_expired = False
                    existing.raw_data = posting.raw_data
                    new_postings.append(existing)
                else:
                    db.add(posting)
                    new_postings.append(posting)
    db.commit()

    # Postings actifs correspondant aux critères (filtrage post-persist pour la réponse).
    active = db.query(models.JobPosting).filter_by(is_expired=False).all()
    filtered = _filter_postings(active, criteria)
    return {
        "postings": filtered[:100],  # cap 100 anti-abus
        "connector_statuses": connector_statuses,
        "total": len(filtered),
        "expired_marked": expired_marked,
    }


def list_connectors_name() -> list[str]:
    """Noms des connecteurs du registre."""
    return list(CONNECTOR_REGISTRY.keys())


def _filter_postings(postings: list[models.JobPosting], criteria: SearchCriteria) -> list[models.JobPosting]:
    """Filtrage post-persist : keywords (title/description), contract_types, salary_min/max, location."""
    result = postings
    if criteria.keywords:
        kw = criteria.keywords.lower()
        result = [p for p in result if kw in (p.title or "").lower() or kw in (p.description or "").lower()]
    if criteria.contract_types:
        result = [p for p in result if p.contract_type in (criteria.contract_types or [])]
    if criteria.salary_min:
        result = [p for p in result if p.salary_max and p.salary_max >= criteria.salary_min]
    if criteria.salary_max:
        result = [p for p in result if p.salary_min and p.salary_min <= criteria.salary_max]
    if criteria.location:
        loc = criteria.location.lower()
        result = [p for p in result if loc in (p.location or "").lower()]
    return result


def run_saved_search(db: Session, saved: models.SavedSearch) -> dict[str, Any]:
    """Exécute une recherche sauvegardée + met à jour last_run_at + last_run_postings_count."""
    crit_dict = saved.criteria or {}
    criteria = SearchCriteria(
        keywords=crit_dict.get("keywords"),
        location=crit_dict.get("location"),
        contract_types=crit_dict.get("contract_types"),
        salary_min=crit_dict.get("salary_min"),
        salary_max=crit_dict.get("salary_max"),
        radius_km=crit_dict.get("radius_km"),
        source_connectors=crit_dict.get("source_connectors"),
    )
    response = search_jobs(db, criteria)
    saved.last_run_at = datetime.utcnow()
    saved.last_run_postings_count = response["total"]
    db.commit()
    return response
