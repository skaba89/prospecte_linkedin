"""P37 — Adaptateur de CV (génération de versions ciblées par offre, anti-invention).

adapt_cv(cv, posting, profile) → adapted_data dict :
  - targeted_title : titre cible (posting.title OU profile.target_job_title OU cv.title).
  - summary : résumé objectif ciblé construit à partir des skills/expérience du CV (PAS inventé —
    assemble des phrases templates + keywords réels du CV + titre cible).
  - reordered_skills : skills du CV réordonnés par pertinence (matching posting required skills en premier).
  - selected_experiences : top 3 expériences du CV les plus alignées (mentionnent keywords de l'offre).
  - matched_certifications : certifications du CV pertinentes pour l'offre (mentionnent keywords).
  - missing_skills : compétences requises par l'offre absentes du CV (transparence — pour formation P40).
  - match_score : score P36 matching (référence).

Invariant P37 (anti-invention) — VÉRIFIÉ en runtime :
  - adapted_data ne contient QUE du contenu issu du CV source (réordonné, sélectionné, résumé).
  - JAMAIS d'invention de skills/expériences/certifications.
  - Le summary est construit à partir des keywords réels du CV + templates (pas de génération libre).
  - _assert_no_invention(adapted_data, cv_parsed) lève ValueError si un item n'est pas dans le CV source.
"""
from __future__ import annotations

import logging
from typing import Any

from . import matching

log = logging.getLogger("revpulse.career.cv_adapter")

# Templates de summary (assemblés avec keywords RÉELS du CV — pas d'invention).
_SUMMARY_TEMPLATES = {
    "fr": (
        "{target_title} avec {years} ans d'expérience. "
        "Compétences clés : {top_skills}. "
        "Recherche une opportunité{company_clause} dans le domaine {domain}."
    ),
    "en": (
        "{target_title} with {years} years of experience. "
        "Key skills: {top_skills}. "
        "Seeking an opportunity{company_clause} in the {domain} field."
    ),
}


def _build_summary(target_title: str, years: int, top_skills: list[str], company: str | None,
                   locale: str) -> str:
    """Construit le résumé ciblé à partir de keywords RÉELS du CV (pas d'invention).
    Templates assemblés avec target_title + years + top_skills (issus du CV) + company (de l'offre)."""
    lang = locale if locale in _SUMMARY_TEMPLATES else "fr"
    tmpl = _SUMMARY_TEMPLATES[lang]
    skills_str = ", ".join(top_skills[:5]) if top_skills else "polyvalent"
    company_clause = f" chez {company}" if company else ""
    domain = target_title.split()[0].lower() if target_title else "professionnel"
    return tmpl.format(
        target_title=target_title or "Professionnel",
        years=years,
        top_skills=skills_str,
        company_clause=company_clause,
        domain=domain,
    )


def _reorder_skills(cv_skills: list[str], required_skills: list[str]) -> list[str]:
    """Réordonne les skills du CV : ceux requis par l'offre en premier (par ordre de requêtes),
    puis les autres. N'ajoute JAMAIS de skill non présent dans le CV."""
    required_set = {s.lower() for s in required_skills}
    matched = [s for s in cv_skills if s.lower() in required_set]
    others = [s for s in cv_skills if s.lower() not in required_set]
    return matched + others


def _select_experiences(cv_experiences: list[str], posting_keywords: list[str], top_n: int = 3) -> list[str]:
    """Sélectionne les top_n expériences les plus alignées (mentionnent le plus de keywords de l'offre).
    N'ajoute JAMAIS d'expérience non présente dans le CV. Fallback : premières expériences si aucun match."""
    if not cv_experiences:
        return []
    kw_lower = [k.lower() for k in posting_keywords]
    scored = []
    for exp in cv_experiences:
        exp_lower = exp.lower()
        score = sum(1 for k in kw_lower if k in exp_lower)
        scored.append((score, exp))
    # Tri par score décroissant, puis ordre original (stable).
    scored.sort(key=lambda x: -x[0])
    selected = [exp for _, exp in scored[:top_n]]
    # Si aucun match (tous score=0), on garde les premières expériences (ordre original).
    return selected


def _matched_certifications(cv_certs: list[str], description: str | None) -> list[str]:
    """Filtre les certifications du CV pertinentes pour l'offre (mentionnent cert keywords dans la description).
    N'ajoute JAMAIS de cert non présente dans le CV. Retourne toutes les certs si description vide."""
    if not cv_certs:
        return []
    if not description:
        return cv_certs  # transparence : toutes les certs si on ne peut pas filtrer.
    desc_lower = description.lower()
    cert_kw = ["scrum", "pmp", "aws", "azure", "cissp", "cism", "itil", "prince2", "certif"]
    relevant = [c for c in cv_certs if any(k in c.lower() or k in desc_lower for k in cert_kw)]
    return relevant if relevant else cv_certs  # fallback : toutes si aucune cert kw dans la desc.


def _assert_no_invention(adapted_data: dict[str, Any], cv_parsed: dict[str, Any]) -> None:
    """Invariant P37 : vérifie que adapted_data ne contient QUE du contenu du CV source.
    Lève ValueError si un skill/expérience/cert de adapted_data n'est pas dans le CV source."""
    cv_skills = {s.lower() for s in cv_parsed.get("skills", []) or []}
    cv_experiences_set = {e.lower() for e in cv_parsed.get("experience", []) or []}
    cv_certs_set = {c.lower() for c in cv_parsed.get("certifications", []) or []}

    for skill in adapted_data.get("reordered_skills", []):
        if skill.lower() not in cv_skills:
            raise ValueError(f"Invariant P37 violé : skill '{skill}' inventé (absent du CV source).")
    for exp in adapted_data.get("selected_experiences", []):
        if exp.lower() not in cv_experiences_set:
            raise ValueError("Invariant P37 violé : expérience inventée (absente du CV source).")
    for cert in adapted_data.get("matched_certifications", []):
        if cert.lower() not in cv_certs_set:
            raise ValueError(f"Invariant P37 violé : certification '{cert}' inventée (absente du CV source).")
    log.debug("Invariant P37 vérifié : adapted_data ne contient que du contenu du CV source.")


def adapt_cv(cv, posting, profile) -> dict[str, Any]:
    """Génère un CV adapté à l'offre cible (P37). cv = models.CV, posting = models.JobPosting (nullable),
    profile = models.CareerProfile. Retourne adapted_data dict (structure pour cv_generator).

    Anti-invention (P37 invariant) : ne réordonne/sélectionne/résume QUE du contenu du CV source.
    _assert_no_invention vérifie en runtime que rien n'est inventé."""
    parsed = cv.parsed_data or {}
    cv_skills = parsed.get("skills", []) or []
    cv_experiences = parsed.get("experience", []) or []
    cv_certs = parsed.get("certifications", []) or []

    # Target info (de l'offre OU du profil OU du CV).
    target_title = (posting.title if posting else None) or profile.target_job_title or cv.title
    target_company = posting.company if posting else None
    description = posting.description if posting else None

    # Required skills de l'offre (P36 heuristic).
    required_skills = matching.extract_required_skills(description) if description else []

    # Reorder skills (required en premier).
    reordered = _reorder_skills(cv_skills, required_skills)

    # Select top experiences (alignées avec keywords de l'offre).
    posting_keywords = required_skills + (target_title.split() if target_title else [])
    selected_exp = _select_experiences(cv_experiences, posting_keywords, top_n=3)

    # Matched certifications.
    matched_certs = _matched_certifications(cv_certs, description)

    # Missing skills (transparence — requis par l'offre, absents du CV).
    cv_skills_lower = {s.lower() for s in cv_skills}
    missing = [s for s in required_skills if s.lower() not in cv_skills_lower]

    # Summary (keywords RÉELS du CV + templates — pas d'invention).
    years = matching.estimate_experience_years(parsed)
    summary = _build_summary(target_title, years, reordered, target_company, cv.locale or "fr")

    # Match score P36 (référence).
    match_score = None
    if posting:
        bd = matching.compute_match(cv, posting, profile)
        match_score = bd.total_score

    adapted_data = {
        "targeted_title": target_title,
        "summary": summary,
        "reordered_skills": reordered,
        "selected_experiences": selected_exp,
        "matched_certifications": matched_certs,
        "missing_skills": missing,
        "match_score": match_score,
        "years_experience": years,
        "locale": cv.locale or "fr",
    }

    # Invariant P37 : vérification runtime anti-invention.
    _assert_no_invention(adapted_data, parsed)

    log.info("CV adapté généré : cv=%s posting=%s score=%s missing=%d",
             cv.id, posting.id if posting else "criteria", match_score, len(missing))
    return adapted_data
