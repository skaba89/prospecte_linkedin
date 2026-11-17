"""P38 — Générateur de lettre de motivation (anti-invention P37 invariant).

generate_cover_letter(cv, posting, profile) → str : lettre de motivation ciblée par offre, assemblée à
partir de keywords RÉELS du CV source (skills + expérience + certifications) + templates. JAMAIS
d'invention de contenu (mêmes compétences/expériences que le CV source — juste reformulées en lettre).

Assert_no_invention_cover_letter(letter, cv_parsed) : vérifie que les skills/certs mentionnés dans la
lettre sont bien dans le CV source (lève ValueError si invention). Invariant P37/P38 : anti-invention
strict — la lettre ne contient QUE du contenu du CV source (reformulé).
"""
from __future__ import annotations

import logging
from typing import Any

from . import matching

log = logging.getLogger("revpulse.career.cover_letter")

# Templates de lettre (fr/en) — assemblés avec keywords RÉELS du CV (pas d'invention).
_LETTER_TEMPLATES = {
    "fr": (
        "Madame, Monsieur,\n\n"
        "Je vous adresse ma candidature pour le poste de {target_title}{company_clause}.\n\n"
        "Fort(e) de {years} ans d'expérience, j'ai développé une expertise en {top_skills}. "
        "Mon parcours m'a permis de maîtriser {experience_summary}, des compétences directement "
        "transposables aux exigences de ce poste{missing_clause}.\n\n"
        "{cert_sentence}"
        "Je suis particulièrement intéressé(e) par cette opportunité car elle correspond à mes "
        "objectifs professionnels. Je reste à votre disposition pour un entretien.\n\n"
        "Cordialement,\n{candidate_name}"
    ),
    "en": (
        "Dear Sir/Madam,\n\n"
        "I am writing to apply for the {target_title} position{company_clause}.\n\n"
        "With {years} years of experience, I have developed expertise in {top_skills}. "
        "My background has allowed me to master {experience_summary}, skills directly transferable "
        "to the requirements of this position{missing_clause}.\n\n"
        "{cert_sentence}"
        "I am particularly interested in this opportunity as it aligns with my professional goals. "
        "I remain available for an interview.\n\n"
        "Best regards,\n{candidate_name}"
    ),
}


def _experience_summary(cv_experiences: list[str], max_n: int = 2) -> str:
    """Résume les expériences du CV (prend les 2 premières, joints par ' ; '). N'invente rien."""
    if not cv_experiences:
        return "domaines variés"
    top = cv_experiences[:max_n]
    return " ; ".join(top)


def _cert_sentence(cv_certs: list[str], locale: str) -> str:
    """Phrase sur les certifications (uniquement si présentes dans le CV — pas d'invention)."""
    if not cv_certs:
        return ""
    certs_str = ", ".join(cv_certs)
    if locale == "en":
        return f"I hold the following certifications: {certs_str}. "
    return f"Je détiens les certifications suivantes : {certs_str}. "


def _missing_clause(missing_skills: list[str], locale: str) -> str:
    """Clause sur les compétences à développer (transparence — requis par l'offre, absentes du CV).
    Ne les présente PAS comme acquises (honnêteté — pas d'invention)."""
    if not missing_skills:
        return ""
    top_missing = missing_skills[:3]
    if locale == "en":
        return f". I am also working on developing skills in {', '.join(top_missing)}"
    return f". Je travaille par ailleurs à développer mes compétences en {', '.join(top_missing)}"


def _assert_no_invention_cover_letter(letter: str, cv_parsed: dict[str, Any],
                                      missing_skills: list[str] | None = None) -> None:
    """Invariant P37/P38 : vérifie que les skills/certs ACQUIS mentionnés dans la lettre sont dans le CV source.
    Lève ValueError si un skill inventé est détecté (mentionné comme acquis mais absent du CV). Les
    missing_skills (explicitement listés comme "à développer") sont exclus du check (transparence, pas
    présentés comme acquis)."""
    cv_skills = {s.lower() for s in cv_parsed.get("skills", []) or []}
    letter_lower = letter.lower()
    missing_set = {s.lower() for s in (missing_skills or [])}
    cv_exp_text = " ".join(cv_parsed.get("experience", []) or []).lower()
    # Vérifier que les skills TECH_KEYWORDS mentionnés comme ACQUIS sont bien dans le CV.
    for kw in matching._TECH_KEYWORDS:
        if kw in letter_lower and kw not in cv_skills and kw not in missing_set:
            # Tolérance : kw peut apparaître dans une expérience (texte brut) — vérifier aussi.
            if kw not in cv_exp_text:
                raise ValueError(
                    f"Invariant P38 violé : skill '{kw}' mentionné dans la lettre mais absent du CV source.")
    log.debug("Invariant P38 vérifié : la lettre ne contient que des skills/certs du CV source.")


def generate_cover_letter(cv, posting, profile, candidate_name: str = "Candidat") -> str:
    """Génère une lettre de motivation ciblée par offre (P38). cv = models.CV, posting = models.JobPosting
    (nullable), profile = models.CareerProfile. candidate_name = nom du candidat (passé par le router depuis
    user.name). Retourne la lettre (str).

    Anti-invention (P37/P38 invariant) : assemblée à partir de keywords RÉELS du CV source (skills +
    expérience + certifications) + templates. JAMAIS d'invention de contenu. _assert_no_invention_cover_letter
    vérifie en runtime que les skills mentionnés sont dans le CV source."""
    parsed = cv.parsed_data or {}
    cv_skills = parsed.get("skills", []) or []
    cv_experiences = parsed.get("experience", []) or []
    cv_certs = parsed.get("certifications", []) or []

    locale = cv.locale or "fr"
    lang = locale if locale in _LETTER_TEMPLATES else "fr"
    tmpl = _LETTER_TEMPLATES[lang]

    # Target info (de l'offre OU du profil OU du CV).
    target_title = (posting.title if posting else None) or profile.target_job_title or cv.title
    company_clause = f" chez {posting.company}" if posting and posting.company else ""
    description = posting.description if posting else None

    # Required skills de l'offre (P36 heuristic).
    required_skills = matching.extract_required_skills(description) if description else []
    # Top skills : réordonner (required en premier), comme P37.
    required_set = {s.lower() for s in required_skills}
    matched = [s for s in cv_skills if s.lower() in required_set]
    others = [s for s in cv_skills if s.lower() not in required_set]
    top_skills = (matched + others)[:5]
    skills_str = ", ".join(top_skills) if top_skills else "polyvalence"

    # Missing skills (transparence — requis par l'offre, absents du CV — honnêteté, pas d'invention).
    cv_skills_lower = {s.lower() for s in cv_skills}
    missing = [s for s in required_skills if s.lower() not in cv_skills_lower]

    years = matching.estimate_experience_years(parsed)
    exp_sum = _experience_summary(cv_experiences)
    cert_sent = _cert_sentence(cv_certs, lang)
    missing_clause = _missing_clause(missing, lang)
    candidate_name_local = candidate_name or "Candidat"

    letter = tmpl.format(
        target_title=target_title or "le poste",
        company_clause=company_clause,
        years=years,
        top_skills=skills_str,
        experience_summary=exp_sum,
        missing_clause=missing_clause,
        cert_sentence=cert_sent,
        candidate_name=candidate_name_local,
    )

    # Invariant P38 : vérification runtime anti-invention.
    _assert_no_invention_cover_letter(letter, parsed, missing_skills=missing)

    log.info("Lettre de motivation générée : cv=%s posting=%s len=%d missing=%d",
             cv.id, posting.id if posting else "criteria", len(letter), len(missing))
    return letter
