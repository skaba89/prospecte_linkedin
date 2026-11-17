"""P37 — Générateur de CV adapté en PDF (reportlab) + Word (python-docx).

generate_pdf(adapted, profile, cv) → bytes : CV adapté en PDF (titre + summary + skills + expériences +
certifications). Layout simple, lisible, anti-débordement.
generate_docx(adapted, profile, cv) → bytes : CV adapté en Word .docx (mêmes sections).

Les deux générateurs consomment adapted_data (issu de cv_adapter.adapt_cv) — ne ré-écrivent PAS le
contenu, juste le formatent. Invariant P37 respecté : le générateur ne fait qu'afficher ce que l'adapter
a extrait du CV source (rien d'inventé).
"""
from __future__ import annotations

import io
import logging
from typing import Any

log = logging.getLogger("revpulse.career.cv_generator")

# Labels localisés (fr/en) pour les sections.
_SECTION_LABELS = {
    "fr": {"summary": "Profil", "skills": "Compétences", "experience": "Expériences",
           "certifications": "Certifications", "missing": "Compétences à développer"},
    "en": {"summary": "Profile", "skills": "Skills", "experience": "Experience",
           "certifications": "Certifications", "missing": "Skills to develop"},
}


def _labels(locale: str) -> dict[str, str]:
    return _SECTION_LABELS.get(locale, _SECTION_LABELS["fr"])


def generate_pdf(adapted: dict[str, Any], profile, cv) -> bytes:
    """Génère le CV adapté en PDF (reportlab). Retourne les bytes du PDF.
    Layout : titre (targeted_title) + nom candidat + summary + skills + expériences + certifications."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    locale = adapted.get("locale", "fr")
    labels = _labels(locale)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle("CvTitle", parent=styles["Title"], fontSize=18, spaceAfter=6, alignment=TA_LEFT)
    style_subtitle = ParagraphStyle("CvSub", parent=styles["Normal"], fontSize=11, spaceAfter=12, textColor="#444444")
    style_h2 = ParagraphStyle("CvH2", parent=styles["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=4)
    style_body = ParagraphStyle("CvBody", parent=styles["Normal"], fontSize=10, spaceAfter=2, leading=13)

    elems = []
    # Titre + sous-titre (nom candidat + titre cible).
    elems.append(Paragraph(adapted.get("targeted_title", "CV"), style_title))
    elems.append(Paragraph(f"{profile.user_name_cv() if hasattr(profile, 'user_name_cv') else ''} — CV adapté", style_subtitle))

    # Summary.
    elems.append(Paragraph(labels["summary"], style_h2))
    elems.append(Paragraph(adapted.get("summary", ""), style_body))
    elems.append(Spacer(1, 4))

    # Skills (réordonnés).
    skills = adapted.get("reordered_skills", [])
    if skills:
        elems.append(Paragraph(labels["skills"], style_h2))
        # Séparer required (premiers) du reste visuellement — ici liste simple.
        elems.append(Paragraph(", ".join(skills), style_body))
        elems.append(Spacer(1, 4))

    # Expériences sélectionnées.
    exps = adapted.get("selected_experiences", [])
    if exps:
        elems.append(Paragraph(labels["experience"], style_h2))
        for exp in exps:
            elems.append(Paragraph(f"• {exp}", style_body))
        elems.append(Spacer(1, 4))

    # Certifications.
    certs = adapted.get("matched_certifications", [])
    if certs:
        elems.append(Paragraph(labels["certifications"], style_h2))
        elems.append(Paragraph(", ".join(certs), style_body))
        elems.append(Spacer(1, 4))

    # Missing skills (transparence — compétences à développer).
    missing = adapted.get("missing_skills", [])
    if missing:
        elems.append(Paragraph(labels["missing"], style_h2))
        elems.append(Paragraph(", ".join(missing), style_body))

    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    log.info("PDF CV adapté généré : %d bytes.", len(pdf_bytes))
    return pdf_bytes


def generate_docx(adapted: dict[str, Any], profile, cv) -> bytes:
    """Génère le CV adapté en Word .docx (python-docx). Retourne les bytes du docx.
    Layout : titre + nom + summary + skills + expériences + certifications."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    locale = adapted.get("locale", "fr")
    labels = _labels(locale)
    doc = Document()

    # Titre.
    doc.add_heading(adapted.get("targeted_title", "CV"), level=1)
    # Sous-titre : nom candidat (si disponible) + "CV adapté".
    sub = doc.add_paragraph("CV adapté")
    for run in sub.runs:
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    # Summary.
    doc.add_heading(labels["summary"], level=2)
    doc.add_paragraph(adapted.get("summary", ""))

    # Skills.
    skills = adapted.get("reordered_skills", [])
    if skills:
        doc.add_heading(labels["skills"], level=2)
        doc.add_paragraph(", ".join(skills))

    # Expériences.
    exps = adapted.get("selected_experiences", [])
    if exps:
        doc.add_heading(labels["experience"], level=2)
        for exp in exps:
            doc.add_paragraph(exp, style="List Bullet")

    # Certifications.
    certs = adapted.get("matched_certifications", [])
    if certs:
        doc.add_heading(labels["certifications"], level=2)
        doc.add_paragraph(", ".join(certs))

    # Missing skills.
    missing = adapted.get("missing_skills", [])
    if missing:
        doc.add_heading(labels["missing"], level=2)
        doc.add_paragraph(", ".join(missing))

    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()
    buf.close()
    log.info("Word CV adapté généré : %d bytes.", len(docx_bytes))
    return docx_bytes
