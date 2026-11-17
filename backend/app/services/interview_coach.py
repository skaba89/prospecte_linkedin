"""interview_coach.py — Coach IA entretiens Career (P41).

Génère + analyse des simulations d'entretien (technique + RH + négociation). Anti-invariant invention :
  - Questions génériques par catégorie de métier (data|product|finance|cyber|rh|generic) — aucune
    affirmation sur le candidat. expected_keywords = critères techniques vérifiables.
  - analyze_answer = heuristic keywords (score = matched/expected, PAS de note arbitraire). Feedback
    factuel : keywords présents/manquants, forces (réponse longue + keywords), pas d'invention.
  - recommend_missing_skills : cross-référence CV parsed_data vs skills attendus par catégorie
    (transparence — "le CV ne mentionne pas X", pas "vous ne savez pas X").
  - recommend_formations : map skills manquantes → suggestions génériques (catalogue ouvert).
  - negotiation_prep : basé sur CareerProfile.salary_min/max + tjm_min (valeurs du profil, pas inventées).

v2 : LLM réel (7 providers P29) pour feedback qualitatif + questions génératives. v1 = heuristic robuste.
"""
from __future__ import annotations

import random

from sqlalchemy.orm import Session

from .. import models

# ─── Banque de questions par catégorie ────────────────────────────────────────
# Chaque question : {id, text, type (tech|rh|nego), topic, expected_keywords}.
# Anti-invention : questions génériques, expected_keywords = critères techniques vérifiables.
_TECH_QUESTIONS: dict[str, list[dict]] = {
    "data": [
        {"id": "data_q1", "text": "Expliquez la différence entre ETL et ELT, et quand privilégier l'un ou l'autre.", "type": "tech", "topic": "pipelines", "expected_keywords": ["extract", "transform", "load", "warehouse", "lake"]},
        {"id": "data_q2", "text": "Comment garantissez-vous la qualité des données dans un pipeline ?", "type": "tech", "topic": "data quality", "expected_keywords": ["validation", "schema", "tests", "monitoring", "deduplication"]},
        {"id": "data_q3", "text": "Décrivez votre expérience avec Spark ou un moteur distributed.", "type": "tech", "topic": "distributed", "expected_keywords": ["spark", "distributed", "partition", "shuffle", "parallel"]},
        {"id": "data_q4", "text": "Quelles bonnes pratiques pour modéliser un entrepôt (star schema vs snowflake) ?", "type": "tech", "topic": "modeling", "expected_keywords": ["fact", "dimension", "star", "snowflake", "normalize"]},
    ],
    "product": [
        {"id": "prod_q1", "text": "Comment priorisez-vous un backlog ? Décrivez votre framework.", "type": "tech", "topic": "priorization", "expected_keywords": ["value", "effort", "rice", "kano", "impact"]},
        {"id": "prod_q2", "text": "Définissez un OKR et la différence avec un KPI.", "type": "tech", "topic": "metrics", "expected_keywords": ["objective", "key", "result", "kpi", "outcome"]},
        {"id": "prod_q3", "text": "Comment menez-vous une découverte utilisateur ?", "type": "tech", "topic": "discovery", "expected_keywords": ["interview", "survey", "persona", "journey", "hypothesis"]},
        {"id": "prod_q4", "text": "Expliquez le rôle du Product Owner dans un cadre Scrum.", "type": "tech", "topic": "scrum", "expected_keywords": ["backlog", "sprint", "stakeholder", "vision", "increment"]},
    ],
    "finance": [
        {"id": "fin_q1", "text": "Décrivez le cycle de clôture comptable et ses étapes clés.", "type": "tech", "topic": "clôture", "expected_keywords": ["rapprochement", "provisions", "amortissement", "bilan", "resultat"]},
        {"id": "fin_q2", "text": "Quels KPI suivez-vous en contrôle de gestion ?", "type": "tech", "topic": "kpis", "expected_keywords": ["marge", "ebitda", "budget", "ecart", "roi"]},
        {"id": "fin_q3", "text": "Expliquez la différence entre audit interne et audit externe.", "type": "tech", "topic": "audit", "expected_keywords": ["interne", "externe", "commissaire", "risque", "conformite"]},
    ],
    "cyber": [
        {"id": "cyb_q1", "text": "Décrivez le modèle CIA en cybersécurité.", "type": "tech", "topic": "fundamentals", "expected_keywords": ["confidentialite", "integrite", "disponibilite", "cia"]},
        {"id": "cyb_q2", "text": "Comment réagissez-vous à un incident de sécurité (gestion d'incident) ?", "type": "tech", "topic": "incident", "expected_keywords": ["containment", "forensic", "ir", "communication", "postmortem"]},
    ],
    "generic": [
        {"id": "gen_q1", "text": "Décrivez un projet complexe que vous avez mené et les défis rencontrés.", "type": "tech", "topic": "experience", "expected_keywords": ["objectif", "defi", "solution", "resultat", "équipe"]},
        {"id": "gen_q2", "text": "Quelles méthodologies utilisez-vous au quotidien ?", "type": "tech", "topic": "method", "expected_keywords": ["agile", "scrum", "kanban", "waterfall", "lean"]},
    ],
}

_RH_QUESTIONS = [
    {"id": "rh_q1", "text": "Présentez-vous en 2 minutes (pitch).", "type": "rh", "topic": "presentation", "expected_keywords": ["parcours", "competence", "objectif", "valeur"]},
    {"id": "rh_q2", "text": "Quelles sont vos principales forces et un axe d'amélioration ?", "type": "rh", "topic": "forces", "expected_keywords": ["force", "amelioration", "exemple", "concret"]},
    {"id": "rh_q3", "text": "Pourquoi notre entreprise et ce poste ?", "type": "rh", "topic": "motivation", "expected_keywords": ["entreprise", "poste", "valeur", "projet", "alignement"]},
    {"id": "rh_q4", "text": "Décrivez une situation difficile avec un collaborateur et comment vous l'avez gérée.", "type": "rh", "topic": "conflict", "expected_keywords": ["situation", "ecoute", "resolution", "communication", "feedback"]},
]

_NEGO_QUESTIONS = [
    {"id": "nego_q1", "text": "Quelles sont vos prétentions salariales ?", "type": "nego", "topic": "salaire", "expected_keywords": ["fourchette", "marche", "experience", "flexible", "package"]},
    {"id": "nego_q2", "text": "Comment négociez-vous un TJM pour une mission freelance ?", "type": "nego", "topic": "tjm", "expected_keywords": ["tjm", "valeur", "mission", "duree", "marge"]},
]

# Skills attendus par catégorie (pour recommend_missing_skills cross-référence CV)
_EXPECTED_SKILLS: dict[str, list[str]] = {
    "data": ["python", "sql", "spark", "airflow", "aws", "gcp", "docker", "kafka", "snowflake"],
    "product": ["scrum", "jira", "agile", "roadmap", "okr", "kpi", "user", "story", "backlog"],
    "finance": ["comptabilite", "controle", "audit", "ifrs", "budget", "reporting", "excel", "sap"],
    "cyber": ["securite", "iso27001", "soc", "pentest", "forensic", "cryptography", "network", "siem"],
    "generic": ["communication", "organisation", "equipe", "projet"],
}

_FORMATION_CATALOG: dict[str, str] = {
    "spark": "Formation Apache Spark (Databricks Academy / Udemy)",
    "airflow": "Apache Airflow Fundamentals (official)",
    "aws": "AWS Certified Data Analytics / Solutions Architect",
    "gcp": "Google Cloud Professional Data Engineer",
    "docker": "Docker Certified Associate + Kubernetes (CKA)",
    "kafka": "Confluent Developer Certification (Apache Kafka)",
    "scrum": "Scrum Master PSM I / CSPO (Scrum.org)",
    "okr": "Formation OKR (Objectives and Key Results)",
    "ifrs": "Diplôme IFRS (CNCC / ACCA)",
    "sap": "SAP FICO / SAP CO (SAP Learning Hub)",
    "iso27001": "Lead Implementer ISO 27001 (PECB)",
    "pentest": "OSCP / CEH (Offensive Security / EC-Council)",
}


def _category_for(target_job_title: str | None, category: str | None) -> str:
    """Détermine la catégorie depuis le paramètre explicite ou le titre du métier."""
    if category:
        return category.lower()
    t = (target_job_title or "").lower()
    if any(k in t for k in ["data", "engineer", "scientist", "analyst"]):
        return "data"
    if any(k in t for k in ["product", "owner", "scrum", "agile"]):
        return "product"
    if any(k in t for k in ["finance", "comptab", "audit", "controle", "tresor"]):
        return "finance"
    if any(k in t for k in ["cyber", "securit", "soc", "grc"]):
        return "cyber"
    if any(k in t for k in ["rh", "humaine", "recrute"]):
        return "rh"
    return "generic"


def generate_questions(target_job_title: str | None, category: str | None,
                       session_type: str = "rhmixed", num_tech: int = 4,
                       num_rh: int = 3) -> list[dict]:
    """Génère une liste de questions pour la session. session_type=rhmixed → tech + RH + 1 nego ;
    tech → tech uniquement + 1 nego. Anti-invention : questions issues de la banque (pas générées)."""
    cat = _category_for(target_job_title, category)
    tech_pool = _TECH_QUESTIONS.get(cat, _TECH_QUESTIONS["generic"])[:]
    random.shuffle(tech_pool)
    selected_tech = tech_pool[:num_tech]
    questions = list(selected_tech)
    if session_type == "rhmixed":
        rh_pool = _RH_QUESTIONS[:]
        random.shuffle(rh_pool)
        questions += rh_pool[:num_rh]
    # Toujours 1 question négo (prépa négociation)
    questions.append(random.choice(_NEGO_QUESTIONS))
    return questions


def analyze_answer(question: dict, answer_text: str, cv_parsed_data: dict | None) -> dict:
    """Heuristic : score = matched_keywords / expected_keywords * 100. Feedback factuel
    (keywords présents/manquants + forces). Anti-invention : aucune affirmation non vérifiable."""
    answer = (answer_text or "").lower()
    expected = question.get("expected_keywords", [])
    matched = [k for k in expected if k.lower() in answer]
    missing = [k for k in expected if k.lower() not in answer]
    score = round(len(matched) / len(expected) * 100) if expected else 50
    # Forces : réponse suffisamment longue (> 30 mots) + ≥ 60% keywords
    word_count = len(answer.split())
    strengths = []
    if word_count >= 30:
        strengths.append(f"Réponse argumentée ({word_count} mots)")
    if score >= 60:
        strengths.append("Vocabulaire technique pertinent")
    if score >= 80:
        strengths.append("Couverture quasi-complète des points attendus")
    feedback_parts = []
    if matched:
        feedback_parts.append(f"Points couverts : {', '.join(matched)}.")
    if missing:
        feedback_parts.append(f"Points à développer : {', '.join(missing)}.")
    if not strengths:
        feedback_parts.append("Réponse concise — argumentez avec des exemples concrets.")
    feedback = " ".join(feedback_parts)
    return {"question_id": question["id"], "score": score, "feedback": feedback,
            "matched": matched, "missing": missing, "strengths": strengths}


def compute_session_score(answers: list[dict]) -> int:
    """Score global = moyenne des scores de réponses (0-100). 0 si aucune réponse."""
    if not answers:
        return 0
    return round(sum(a.get("score", 0) for a in answers) / len(answers))


def recommend_missing_skills(cv_parsed_data: dict | None, category: str | None,
                             target_job_title: str | None) -> list[str]:
    """Cross-référence CV vs skills attendus par catégorie. Transparence : "le CV ne mentionne pas X"
    (pas d'affirmation sur les compétences réelles du candidat — anti-invention)."""
    cat = _category_for(target_job_title, category)
    cv_skills_raw = (cv_parsed_data or {}).get("skills", []) if cv_parsed_data else []
    cv_skills = [s.lower() for s in cv_skills_raw]
    expected = _EXPECTED_SKILLS.get(cat, _EXPECTED_SKILLS["generic"])
    return [s for s in expected if s not in cv_skills]


def recommend_formations(missing_skills: list[str]) -> list[str]:
    """Map skills manquantes → suggestions formations (catalogue ouvert). Suggestions génériques,
    pas d'affirmation sur le niveau actuel du candidat."""
    out = []
    for s in missing_skills:
        if s in _FORMATION_CATALOG:
            out.append(_FORMATION_CATALOG[s])
    # fallback générique si aucune skill manquante cataloguée
    if not out and missing_skills:
        out.append(f"Formation continue sur : {', '.join(missing_skills[:3])} (MOOC / certification).")
    return out


def negotiation_prep(profile: models.CareerProfile, target_job_title: str | None) -> dict:
    """Prépa négociation basée sur CareerProfile.salary_min/max + tjm_min (valeurs du profil — pas inventées).
    Retourne fourchette + points de discussion + pièges à éviter."""
    cur = profile.currency or "EUR"
    salary_range = None
    tjm = None
    if profile.salary_min or profile.salary_max:
        salary_range = {"min": profile.salary_min, "max": profile.salary_max, "currency": cur}
    if profile.tjm_min:
        tjm = {"min": profile.tjm_min, "currency": cur}
    return {
        "target_job_title": target_job_title,
        "salary_range": salary_range,
        "tjm_min": tjm,
        "talking_points": [
            "Valorisez l'expérience mesurable (projets, impact chiffré).",
            "Ancrez la fourchette sur le marché + votre valeur (pas sur votre salaire précédent).",
            "Discutez le package global (fixe, variable, equity, avantages, télétravail).",
        ],
        "pitfalls": [
            "Donner un chiffre unique trop tôt (préférez une fourchette).",
            "Négliger le package global au profit du fixe seul.",
            "Accepter la première offre sans contre-proposition argumentée.",
        ],
    }


def build_session_feedback(db: Session, session: models.InterviewSession,
                           cv_parsed_data: dict | None) -> dict:
    """Récap global feedback : score moyen + missing_skills + formations + negotiation prep.
    Anti-invention : basé sur answers + CV + profil (transparence)."""
    answers = session.answers or []
    missing = recommend_missing_skills(cv_parsed_data, session.category, session.target_job_title)
    formations = recommend_formations(missing)
    profile = db.query(models.CareerProfile).filter_by(id=session.career_profile_id).first()
    nego = negotiation_prep(profile, session.target_job_title) if profile else {}
    return {
        "overall_score": compute_session_score(answers),
        "answered": len(answers),
        "total_questions": len(session.questions or []),
        "missing_skills": missing,
        "recommended_formations": formations,
        "negotiation_prep": nego,
        "note": "Score = couverture des points techniques attendus (heuristic keywords). "
                "Le feedback est factuel (keywords présents/manquants) — aucune affirmation non vérifiable.",
    }
