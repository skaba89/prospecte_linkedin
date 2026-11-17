"""P34 — Career CV service : import (text/PDF/Word) + extraction heuristique structurée.

Import :
  - import_cv_text : texte brut (collé par le candidat).
  - import_cv_pdf : fichier PDF → texte via pdfminer.six (extraction layout-aware).
  - import_cv_docx : fichier Word → texte via python-docx (paragraphes + tableaux).

Extraction (v1 — heuristique keyword-based, PAS LLM) :
  - parse_cv(raw_text, locale) → parsed_data {skills, experiences, certifications, education}.
    Détecte les sections par headers (Expérience, Compétences, Formation, Certifications) + extrait
    les items (bullet points, lignes). Skills = keywords techniques (Python, SQL, Scrum...).
  - detect_transferrable_skills(parsed_data) → skills transférables (soft skills + skills cross-domain).

Sécurité (P34) :
  - raw_text stocké (jamais le binaire) — le fichier PDF/Word n'est PAS persisté (anti-fuite).
  - Extraction v1 heuristique — clairement marquée heuristic=True (pas présentée comme ML/NLP parfait).
    v2 branchera le LLM (P25 AI Gateway) pour extraction sémantique + matching P36.
  - Ne JAMAIS inventer de compétence ou expérience (P37 invariant) — parse_cv n'ajoute que ce qui est
    explicitement dans le texte.
"""
from __future__ import annotations

import io
import re
from typing import Any

from sqlalchemy.orm import Session

from .. import models

# Headers de sections CV (fr + en) pour l'extraction heuristique.
_SECTION_HEADERS = {
    "experience": ["expérience", "experience", "expérience professionnelle", "professional experience",
                   "parcours", "work experience", "historique"],
    "skills": ["compétences", "competences", "skills", "compétences techniques", "technical skills",
               "savoir-faire", "technologies", "stack technique"],
    "education": ["formation", "education", "diplômes", "diplomas", "cursus", "academic background",
                  "études", "studies"],
    "certifications": ["certifications", "certificats", "certifs", "labels", "habilitations"],
}

# Keywords techniques fréquents (v1 — subset; v2 : référentiel ESCO skills).
_TECH_KEYWORDS = {
    "python", "java", "javascript", "typescript", "sql", "nosql", "react", "vue", "angular", "node",
    "fastapi", "django", "flask", "spring", "docker", "kubernetes", "aws", "azure", "gcp", "terraform",
    "git", "ci/cd", "jenkins", "scrum", "kanban", "agile", "safe", "itil", "pmp", "prince2",
    "tableau", "powerbi", "excel", "vba", "sap", "salesforce", "hubspot", "oracle", "mysql", "postgres",
    "mongodb", "redis", "kafka", "spark", "hadoop", "airflow", "dbt", "snowflake", "machine learning",
    "deep learning", "tensorflow", "pytorch", "nlp", "llm", "prompt engineering",
}

# Soft skills transférables (cross-domain).
_SOFT_SKILLS = {
    "leadership", "communication", "négociation", "negotiation", "gestion de projet", "project management",
    "résolution de problèmes", "problem solving", "esprit d'analyse", "analytical", "creativity",
    "créativité", "team management", "management d'équipe", "mentorat", "mentoring", "presentation",
    "prise de parole", "public speaking", "cross-functional", "transversal", "stakeholder management",
}


def _extract_text_pdf(file_bytes: bytes) -> str:
    """Extrait le texte d'un PDF via pdfminer.six (layout-aware). Retourne texte brut."""
    from pdfminer.high_level import extract_text
    return extract_text(io.BytesIO(file_bytes)) or ""


def _extract_text_docx(file_bytes: bytes) -> str:
    """Extrait le texte d'un .docx via python-docx (paragraphes + cellules de tableaux)."""
    import docx
    doc = docx.Document(io.BytesIO(file_bytes))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _next_version(db: Session, profile_id: str) -> int:
    """Version auto-incrément par profile (versionning CV)."""
    last = db.query(models.CV).filter_by(career_profile_id=profile_id).order_by(
        models.CV.version.desc()).first()
    return (last.version + 1) if last else 1


def _create_cv(db: Session, profile: models.CareerProfile, title: str, source_type: str,
               raw_text: str, locale: str, source_filename: str | None) -> models.CV:
    """Crée un CV (commun aux 3 sources). Parse le texte + store parsed_data. Désactive les autres."""
    parsed = parse_cv(raw_text, locale)
    cv = models.CV(
        career_profile_id=profile.id,
        title=title,
        source_type=source_type,
        source_filename=source_filename,
        raw_text=raw_text,
        locale=locale,
        version=_next_version(db, profile.id),
        is_active=False,
        parsed_data=parsed,
    )
    db.add(cv)
    db.flush()  # id disponible
    set_active_cv(db, cv)  # le nouveau CV devient actif (désactive les autres)
    db.commit()
    db.refresh(cv)
    return cv


def import_cv_text(db: Session, profile: models.CareerProfile, title: str, text: str,
                   locale: str = "fr") -> models.CV:
    """Importe un CV depuis un texte brut (collé). source_type=text."""
    if not text or not text.strip():
        raise ValueError("Texte vide — impossible d'importer le CV.")
    return _create_cv(db, profile, title, "text", text.strip(), locale, None)


def import_cv_pdf(db: Session, profile: models.CareerProfile, title: str, filename: str,
                 file_bytes: bytes, locale: str = "fr") -> models.CV:
    """Importe un CV depuis un PDF. Extrait le texte via pdfminer. source_type=pdf."""
    if not file_bytes:
        raise ValueError("Fichier PDF vide.")
    raw = _extract_text_pdf(file_bytes)
    if not raw.strip():
        raise ValueError("Extraction PDF vide — PDF peut-être scanné (image, pas de texte). v2 : OCR.")
    return _create_cv(db, profile, title, "pdf", raw.strip(), locale, filename)


def import_cv_docx(db: Session, profile: models.CareerProfile, title: str, filename: str,
                   file_bytes: bytes, locale: str = "fr") -> models.CV:
    """Importe un CV depuis un .docx. Extrait le texte via python-docx. source_type=docx."""
    if not file_bytes:
        raise ValueError("Fichier Word vide.")
    raw = _extract_text_docx(file_bytes)
    if not raw.strip():
        raise ValueError("Extraction Word vide — fichier corrompu ou sans texte.")
    return _create_cv(db, profile, title, "docx", raw.strip(), locale, filename)


def parse_cv(raw_text: str, locale: str = "fr") -> dict[str, Any]:
    """Extraction heuristique (v1 — PAS LLM). Détecte les sections (expérience, compétences, formation,
    certifications) par headers + extrait les items. Skills = keywords techniques matchés dans le texte.
    Retourne parsed_data {skills, experiences, certifications, education, heuristic=True}.

    Invariant P37 : n'ajoute QUE ce qui est explicitement dans le texte — n'invente JAMAIS."""
    text_lower = raw_text.lower()
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    # Section detection : trouve l'index du header de chaque section.
    sections: dict[str, int] = {}
    for sec, headers in _SECTION_HEADERS.items():
        for i, ln in enumerate(lines):
            if any(ln.lower().rstrip(":").strip() == h for h in headers):
                sections[sec] = i
                break

    # Extrait les items de chaque section (lignes jusqu'au prochain header).
    section_order = sorted(sections.items(), key=lambda x: x[1])
    extracted: dict[str, list[str]] = {"experience": [], "skills": [], "education": [], "certifications": []}
    for idx, (sec, start) in enumerate(section_order):
        end = section_order[idx + 1][1] if idx + 1 < len(section_order) else len(lines)
        for ln in lines[start + 1:end]:
            # Bullet points ou lignes non-vides (strip puces -, •, *, →).
            clean = re.sub(r"^[-•*→\d.)\s]+", "", ln).strip()
            if clean and len(clean) > 2:
                extracted[sec].append(clean[:300])  # cap longueur anti-abus

    # Skills : match keywords techniques dans le texte complet (au-delà de la section skills).
    found_skills = sorted({kw for kw in _TECH_KEYWORDS if kw in text_lower})

    return {
        "skills": found_skills,  # TECH keywords matchés dans le texte (python, sql, scrum...).
        "skills_section": extracted["skills"][:30],  # lignes brutes de la section Compétences (pour soft skills).
        "experiences": extracted["experience"][:20],  # cap 20 anti-abus
        "certifications": extracted["certifications"][:15],
        "education": extracted["education"][:10],
        "heuristic": True,  # v1 — extraction keyword-based, pas ML/NLP parfait.
        "locale": locale,
    }


def set_active_cv(db: Session, cv: models.CV) -> models.CV:
    """Active un CV (désactive les autres du même profile — 1 seul actif)."""
    db.query(models.CV).filter(
        models.CV.career_profile_id == cv.career_profile_id,
        models.CV.id != cv.id,
    ).update({"is_active": False})
    cv.is_active = True
    db.commit()
    db.refresh(cv)
    return cv


def detect_transferrable_skills(parsed_data: dict[str, Any]) -> list[str]:
    """Détecte les compétences transférables (soft skills cross-domain) dans tout le contenu parsé
    du CV (skills tech + skills_section raw + experiences + certifications + education). Retourne la
    liste des soft skills trouvés. Utile pour un changement de métier (P36 matching)."""
    text = " ".join(
        parsed_data.get("skills", [])
        + parsed_data.get("skills_section", [])
        + parsed_data.get("experiences", [])
        + parsed_data.get("certifications", [])
        + parsed_data.get("education", [])
    ).lower()
    return sorted({sk for sk in _SOFT_SKILLS if sk in text})
