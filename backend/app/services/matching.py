"""P36 — Moteur de matching intelligent CV↔offre (score hybride explicable 0-100).

compute_match(cv, posting, profile) → MatchingBreakdown :
  - 6 critères pondérés : skills (40%), experience (20%), certifications (10%), location (10%),
    salary (10%), languages (10%). Chaque critère = sub-score 0-100 + détail + flag eliminatory.
  - Score total = somme pondérée (0-100). Si critère éliminatoire (compétence obligatoire absente
    OU salaire < 70% du min CV) → score plafonné à 60 + flag.
  - missing_skills : compétences requises (détectées dans la description) non présentes dans le CV.
  - disclaimer : "Score de compatibilité — NE PAS présenter comme probabilité de recrutement" (anti-
    sur-déclaration — le score indique un alignement de profil, pas une prédiction d'embauche).

Anti-sur-déclaration (P36 invariant) :
  - Le score est un INDICATEUR DE COMPATIBILITÉ, pas une probabilité de recrutement. Il ne tient pas
    compte de la concurrence, du timing, du fit culturel, du biais recruteur. Disclaimer explicite.
  - Heuristic v1 : extraction de compétences requises par keyword matching (TECH_KEYWORDS), estimation
    d'années d'expérience par parsing "X ans" + comptage d'expériences. Pas présenté comme ML parfait.
  - v2 : embeddings NLP (P25 AI Gateway) + LLM pour extraction sémantique des requis + pondération
    apprise (feedback candidat/recruteur P43).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .career_cv import _TECH_KEYWORDS

# Poids des critères (somme = 1.0). Configurable v2 (par profil/recruteur).
WEIGHTS = {
    "skills": 0.40,
    "experience": 0.20,
    "certifications": 0.10,
    "location": 0.10,
    "salary": 0.10,
    "languages": 0.10,
}

# Seuils eliminatory.
SALARY_ELIMINATORY_RATIO = 0.70  # posting < 70% du salary_min CV → éliminatoire.
ELIMINATORY_SCORE_CAP = 60.0  # score plafonné si un critère éliminatoire échoue.

DISCLAIMER = (
    "Score de compatibilité — indicateur d'alignement de profil (compétences/expérience/critères "
    "objectifs), NE PAS présenter comme une probabilité de recrutement. Ne tient pas compte de la "
    "concurrence, du timing, du fit culturel ou des biais recruteurs. Heuristic v1 (extraction "
    "par keywords) — v2 : embeddings NLP + LLM."
)


@dataclass
class CriterionScore:
    """Sous-score d'un critère (P36). score 0-100, weight 0-1, contribution = weight*score."""
    name: str  # skills|experience|certifications|location|salary|languages
    weight: float
    score: float  # 0-100
    detail: str  # explication lisible (ex. "8/10 compétences requises présentes")
    eliminatory: bool = False  # True si critère obligatoire échoué (cap le total)


@dataclass
class MatchingBreakdown:
    """Résultat du matching CV↔offre (P36). total_score = somme pondérée (capée si éliminatoire)."""
    cv_id: str
    posting_id: str
    total_score: float  # 0-100
    criteria: list[CriterionScore] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    eliminatory_flags: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


# ─── Heuristics d'extraction ──────────────────────────────────────────────────

def extract_required_skills(description: str | None) -> list[str]:
    """Extrait les compétences requises depuis la description de l'offre (heuristic v1).
    Match les TECH_KEYWORDS du référentiel career_cv dans le texte. v2 : embeddings NLP."""
    if not description:
        return []
    text_lower = description.lower()
    return sorted({kw for kw in _TECH_KEYWORDS if kw in text_lower})


def estimate_experience_years(parsed_data: dict[str, Any]) -> int:
    """Estime les années d'expérience du CV (heuristic v1).
    1. Parse "X ans" / "X years" dans le texte d'expérience.
    2. Sinon : compte les entrées d'expérience * 2 ans (estimation moyenne par poste).
    Cap à 40 ans (anti-abus)."""
    experiences = parsed_data.get("experience", []) or []
    text = " ".join(experiences).lower()
    # Pattern "X ans" / "X years" / "X années".
    matches = re.findall(r"(\d+)\s*(?:ans|années|years|ans d)", text)
    if matches:
        years = max(int(m) for m in matches if int(m) <= 40)
        return min(years, 40)
    # Fallback : compter les entrées * 2 ans (estimation moyenne).
    return min(len(experiences) * 2, 40)


def estimate_seniority_years(title: str | None) -> int:
    """Estime les années d'expérience requises par le titre (heuristic v1).
    Senior/Lead/Principal → 5+, Junior/Trainee → 0-2, default → 3."""
    if not title:
        return 3
    t = title.lower()
    if any(k in t for k in ["lead", "principal", "staff", "expert", "head"]):
        return 7
    if any(k in t for k in ["senior", "sr", "sénior", "confirmé"]):
        return 5
    if any(k in t for k in ["junior", "trainee", "stagiaire", "intern", "débutant"]):
        return 1
    return 3  # mid-level default.


# ─── Sous-scores par critère ──────────────────────────────────────────────────

def _score_skills(cv_skills: list[str], required: list[str]) -> tuple[float, list[str], bool, str]:
    """Score compétences (0-100). missing = requis non présents. eliminatory si requis > 0 et matché 0."""
    if not required:
        return 50.0, [], False, "Aucune compétence requise détectée dans la description (neutre 50%)."
    cv_set = {s.lower() for s in cv_skills}
    matched = [r for r in required if r.lower() in cv_set]
    missing = [r for r in required if r.lower() not in cv_set]
    score = (len(matched) / len(required)) * 100
    eliminatory = len(matched) == 0 and len(required) >= 3  # 0 compétences sur 3+ requis → éliminatoire.
    detail = f"{len(matched)}/{len(required)} compétences requises présentes."
    if missing:
        detail += f" Manquantes : {', '.join(missing[:5])}."
    return score, missing, eliminatory, detail


def _score_experience(cv_years: int, required_years: int) -> tuple[float, bool, str]:
    """Score expérience (0-100). eliminatory si CV < 50% du requis."""
    if required_years <= 0:
        return 50.0, False, "Aucune exigence d'expérience détectée (neutre 50%)."
    ratio = cv_years / required_years
    score = min(ratio, 1.0) * 100
    eliminatory = cv_years < required_years * 0.5  # < 50% du requis → éliminatoire.
    detail = f"CV ~{cv_years} ans / requis ~{required_years} ans."
    if eliminatory:
        detail += " Éliminatoire (< 50% du requis)."
    return score, eliminatory, detail


def _score_certifications(cv_certs: list[str], description: str | None) -> tuple[float, bool, str]:
    """Score certifications (0-100). Si l'offre mentionne certif + CV n'en a pas → 0 (éliminatoire si
    "obligatoire" mentionné). Sinon neutre 50%."""
    if not description:
        return 50.0, False, "Pas de description (neutre 50%)."
    desc_lower = description.lower()
    requires_cert = any(k in desc_lower for k in ["certification", "certifié", "certif", "habilitation",
                                                   "certified", "aws certified", "scrum master", "pmp"])
    if not requires_cert:
        return 50.0, False, "Aucune certification requise détectée (neutre 50%)."
    cv_cert_text = " ".join(cv_certs).lower()
    if cv_cert_text and any(k in cv_cert_text for k in ["certif", "scrum", "pmp", "aws", "azure", "cissp"]):
        return 100.0, False, "Certification requise détectée + présente dans le CV."
    mandatory = "obligatoire" in desc_lower or "required" in desc_lower
    detail = "Certification requise mais absente du CV."
    if mandatory:
        detail += " Éliminatoire (mentionnée obligatoire)."
    return 0.0, mandatory, detail


def _score_location(posting_location: str | None, profile_preferred: str | None,
                    mobility: bool) -> tuple[float, bool, str]:
    """Score localisation (0-100). remote/mobility → 100. Match ville → 100. Sinon 0."""
    if not posting_location:
        return 50.0, False, "Localisation de l'offre non précisée (neutre 50%)."
    loc_lower = posting_location.lower()
    if "remote" in loc_lower or "télétravail" in loc_lower or "anywhere" in loc_lower:
        return 100.0, False, "Offre en remote/télétravail (match plein)."
    if not profile_preferred:
        return 40.0, False, "Préférence localisation candidat non précisée (neutre 40%)."
    pref_lower = profile_preferred.lower()
    # Match si la ville du profil est dans la loc de l'offre OU inverse.
    if pref_lower in loc_lower or loc_lower in pref_lower:
        return 100.0, False, f"Localisation candidat ({profile_preferred}) matche l'offre."
    # Match par mot-clé ville (Paris/Lyon/Marseille/...).
    cities = ["paris", "lyon", "marseille", "bordeaux", "lille", "toulouse", "nantes", "nice"]
    for c in cities:
        if c in loc_lower and c in pref_lower:
            return 100.0, False, f"Ville commune détectée ({c})."
    # Pas de match exact : mobility = fallback (accepte relocalisation → match partiel 80%).
    if mobility:
        return 80.0, False, "Candidat mobile (accepte relocalisation) — match partiel 80%."
    return 0.0, False, f"Localisation non matchée (offre : {posting_location}, candidat : {profile_preferred})."


def _score_salary(posting_min: int | None, posting_max: int | None,
                  cv_min: int | None, cv_max: int | None) -> tuple[float, bool, str]:
    """Score salaire (0-100). Overlap range → 100. Posting < 70% du CV min → éliminatoire."""
    if posting_max is None and posting_min is None:
        return 50.0, False, "Salaire de l'offre non précisé (neutre 50%)."
    if cv_min is None and cv_max is None:
        return 50.0, False, "Salaire attendu candidat non précisé (neutre 50%)."
    p_min = posting_min or 0
    p_max = posting_max or p_min
    c_min = cv_min or 0
    c_max = cv_max or c_min
    # Overlap : les ranges se croisent.
    if p_max >= c_min and c_max >= p_min:
        return 100.0, False, f"Ranges salaires compatibles (offre {p_min}-{p_max}, CV {c_min}-{c_max})."
    # Posting trop bas : éliminatoire si < 70% du CV min.
    if p_max < c_min * SALARY_ELIMINATORY_RATIO:
        return 0.0, True, (f"Offre ({p_max}) < 70% du min CV ({c_min}) — éliminatoire (sous-évalué).")
    # Posting trop haut (sur-qualifié salaire) — pas éliminatoire, score partiel.
    if p_min > c_max:
        return 70.0, False, f"Offre au-dessus des attentes CV (offre {p_min} > CV max {c_max}) — acceptable."
    return 30.0, False, f"Ranges salaires partiellement compatibles (offre {p_min}-{p_max}, CV {c_min}-{c_max})."


def _score_languages(posting_description: str | None, cv_languages: list[str] | None) -> tuple[float, bool, str]:
    """Score langues (0-100). Détecte exigences (anglais/espagnol/allemand) dans la description."""
    if not posting_description:
        return 50.0, False, "Pas de description (neutre 50%)."
    desc_lower = posting_description.lower()
    required_langs: list[str] = []
    if any(k in desc_lower for k in ["anglais", "english", "fluent english", "english fluent"]):
        required_langs.append("en")
    if any(k in desc_lower for k in ["espagnol", "spanish"]):
        required_langs.append("es")
    if any(k in desc_lower for k in ["allemand", "german"]):
        required_langs.append("de")
    if not required_langs:
        return 50.0, False, "Aucune exigence linguistique détectée (neutre 50%)."
    cv_langs = {lang.lower() for lang in (cv_languages or [])}
    matched = [lang for lang in required_langs if lang in cv_langs]
    score = (len(matched) / len(required_langs)) * 100
    eliminatory = len(matched) == 0 and "obligatoire" in desc_lower
    detail = f"{len(matched)}/{len(required_langs)} langues requises présentes ({', '.join(required_langs)})."
    if eliminatory:
        detail += " Éliminatoire (langue obligatoire absente)."
    return score, eliminatory, detail


# ─── Orchestrateur ────────────────────────────────────────────────────────────

def compute_match(cv, posting, profile) -> MatchingBreakdown:
    """Calcule le score de matching CV↔offre (P36). cv = models.CV, posting = models.JobPosting,
    profile = models.CareerProfile. Retourne MatchingBreakdown (total 0-100 + critères + missing +
    eliminatory_flags + disclaimer)."""
    parsed = cv.parsed_data or {}
    cv_skills = parsed.get("skills", []) or []
    cv_certs = parsed.get("certifications", []) or []
    cv_languages = profile.languages or []

    required_skills = extract_required_skills(posting.description)
    cv_years = estimate_experience_years(parsed)
    required_years = estimate_seniority_years(posting.title)

    # ── Sous-scores ──
    sk_score, missing, sk_elim, sk_detail = _score_skills(cv_skills, required_skills)
    ex_score, ex_elim, ex_detail = _score_experience(cv_years, required_years)
    ce_score, ce_elim, ce_detail = _score_certifications(cv_certs, posting.description)
    lo_score, lo_elim, lo_detail = _score_location(posting.location, profile.preferred_location, profile.mobility)
    sa_score, sa_elim, sa_detail = _score_salary(posting.salary_min, posting.salary_max,
                                                  profile.salary_min, profile.salary_max)
    la_score, la_elim, la_detail = _score_languages(posting.description, cv_languages)

    criteria = [
        CriterionScore("skills", WEIGHTS["skills"], sk_score, sk_detail, sk_elim),
        CriterionScore("experience", WEIGHTS["experience"], ex_score, ex_detail, ex_elim),
        CriterionScore("certifications", WEIGHTS["certifications"], ce_score, ce_detail, ce_elim),
        CriterionScore("location", WEIGHTS["location"], lo_score, lo_detail, lo_elim),
        CriterionScore("salary", WEIGHTS["salary"], sa_score, sa_detail, sa_elim),
        CriterionScore("languages", WEIGHTS["languages"], la_score, la_detail, la_elim),
    ]

    # ── Total = somme pondérée ──
    total = sum(c.weight * c.score for c in criteria)

    # ── Flags éliminatoires + cap ──
    eliminatory_flags: list[str] = []
    has_eliminatory = False
    for c in criteria:
        if c.eliminatory:
            eliminatory_flags.append(f"{c.name} : {c.detail}")
            has_eliminatory = True
    if has_eliminatory:
        total = min(total, ELIMINATORY_SCORE_CAP)

    return MatchingBreakdown(
        cv_id=cv.id, posting_id=posting.id, total_score=round(total, 1),
        criteria=criteria, missing_skills=missing, eliminatory_flags=eliminatory_flags,
        disclaimer=DISCLAIMER,
    )
