"""Pagination standard pour les endpoints liste (GET).

Query params : ?skip=0&limit=100 (limit plafonnée à 200). Retro-compatible :
sans paramètres, retourne jusqu'à 100 lignes (couvre le volume démo sans
tronquer les listes existantes). Le total est exposé via l'en-tête X-Total-Count
(exposé au navigateur par CORS dans main.py). Les tests API existants
(counts < 100) ne sont pas affectés.
"""
from fastapi import Query
from pydantic import BaseModel, Field

MAX_LIMIT = 200
DEFAULT_LIMIT = 100


class Page(BaseModel):
    skip: int = Field(0, ge=0)
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


def page_dep(skip: int = Query(0, ge=0, description="Nombre de résultats à sauter"),
             limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                                description="Nombre max de résultats (≤ 200)")) -> Page:
    """Dépendance FastAPI : parse skip/limit depuis la query string avec validation."""
    return Page(skip=skip, limit=limit)


def set_total(response, total: int) -> None:
    """Positionne X-Total-Count sur la réponse (le CORS expose cet en-tête au navigateur)."""
    response.headers["X-Total-Count"] = str(total)
