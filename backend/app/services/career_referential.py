"""P34 — Référentiel métiers universel (ROME / ESCO / O*NET).

seed_referential(db) : peuple la table job_references avec un subset MVP couvrant les métiers
prioritaires (finance, product, PMO, data, cyber, IA, RH, commerce, santé, éducation). v2 : import
complet des référentiels selon leurs licences (ROME Open Data France Travail, ESCO EU Open Data,
O*NET US Department of Labor — tous libres d'usage).

search_jobs(db, query, source, category) : recherche full-text sur label/code + filtre source/category.

Sécurité (P34) : référentiel global (pas de scope user/tenant) — lecture publique pour tous les
candidats authentifiés. Pas d'écriture via API (admin only — v2 : endpoint admin).
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models

# Subset MVP — métiers prioritaires par catégorie (code ROME fr + label).
# v2 : import complet ROME (11000+ codes) + ESCO + O*NET selon licences.
_MVP_REFERENTIAL = [
    # Finance / comptabilité / contrôle de gestion / audit
    ("C1101", "rome", "Comptable", "finance"),
    ("C1201", "rome", "Auditeur / Auditrice", "finance"),
    ("C1301", "rome", "Contrôleur de gestion", "finance"),
    ("D1101", "rome", "Analyste financier", "finance"),
    # Product Owner / Scrum Master / Product Manager
    ("M1805", "rome", "Product Owner / Product Manager", "product"),
    ("M1806", "rome", "Scrum Master / Coach Agile", "product"),
    # Chef de projet / PMO
    ("M1401", "rome", "Chef de projet IT", "project"),
    ("M1402", "rome", "PMO / Project Management Officer", "project"),
    # Data / cybersécurité / IA / informatique
    ("M1801", "rome", "Data Scientist", "data"),
    ("M1802", "rome", "Data Engineer", "data"),
    ("M1803", "rome", "Data Analyst", "data"),
    ("M1807", "rome", "Ingénieur cybersécurité", "cyber"),
    ("M1808", "rome", "Architecte logiciel", "it"),
    ("M1810", "rome", "Développeur full-stack", "it"),
    ("M1811", "rome", "Ingénieur IA / Machine Learning", "ai"),
    # Télécommunications
    ("M1901", "rome", "Ingénieur télécoms / réseaux", "telecom"),
    # Industrie / mines / BTP / énergie / logistique
    ("H2601", "rome", "Ingénieur industriel", "industrie"),
    ("F1101", "rome", "Conducteur de travaux BTP", "btp"),
    ("K2101", "rome", "Responsable logistique", "logistics"),
    # RH / commerce / marketing
    ("G1201", "rome", "Chargé de recrutement RH", "rh"),
    ("G1301", "rome", "Responsable ressources humaines", "rh"),
    ("D1401", "rome", "Responsable commercial", "sales"),
    ("E1101", "rome", "Responsable marketing", "marketing"),
    # Santé / éducation / administration
    ("J1101", "rome", "Médecin", "health"),
    ("J1301", "rome", "Infirmier / Infirmière", "health"),
    ("K2110", "rome", "Enseignant / Enseignante", "education"),
    ("K2102", "rome", "Formateur / Formatrice", "education"),
]


def seed_referential(db: Session) -> int:
    """Peuple job_references avec le subset MVP (idempotent — skip si déjà seedé). Retourne le nb d'entrées ajoutées."""
    existing = db.query(models.JobReference).count()
    if existing > 0:
        return 0  # déjà seedé — idempotent
    added = 0
    for code, source, label, category in _MVP_REFERENTIAL:
        db.add(models.JobReference(
            code=code, source=source, label=label, category=category, locale="fr"))
        added += 1
    db.commit()
    return added


def search_jobs(db: Session, query: str | None = None, source: str | None = None,
                category: str | None = None, limit: int = 50) -> list[models.JobReference]:
    """Recherche dans le référentiel métiers. Filtre optionnel par query (label/code), source, category.
    Retourne les `limit` premiers résultats triés par label."""
    q = db.query(models.JobReference)
    if source:
        q = q.filter(models.JobReference.source == source)
    if category:
        q = q.filter(models.JobReference.category == category)
    if query:
        pattern = f"%{query.lower()}%"
        q = q.filter(or_(
            models.JobReference.label.ilike(pattern),
            models.JobReference.code.ilike(pattern),
        ))
    return q.order_by(models.JobReference.label).limit(limit).all()
