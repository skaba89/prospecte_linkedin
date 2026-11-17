"""career_analytics.py — Analytics Career (P43).

Calcule les KPIs Career depuis les modèles existants (JobPosting, JobApplication, CV,
CareerNotification, InterviewSession). Aucune nouvelle table — agrégation pure (read-only).

KPIs (mission §12) :
  - offres_détectées (JobPosting actifs), offres_compatibles (new_match notifs distinctes).
  - candidatures_envoyées (JobApplication count), par statut (pipeline distribution).
  - score_moyen_matching (avg payload.score des notifs new_match).
  - taux_réponse (apps non submitted / total), taux_entretien (apps interview|offer / total).
  - refus, propositions (status counts).
  - performance_par_plateforme (apps groupées par posting.source_connector).
  - performance_par_cv (apps groupées par adapted_cv_id).
  - évolution_salaires_tjm (avg salary_min/max + tjm des postings candidatés, par mois).
  - sessions_entretien + score_moyen_entretien (InterviewSession P41).

Isolation : tous les KPIs scopés par career_profile_id (user). Anti-invention : statistiques
factuelles agrégées — aucune interprétation ni prédiction.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models


def _apps_for(db: Session, profile_id: str) -> list:
    return db.query(models.JobApplication).filter_by(career_profile_id=profile_id).all()


def overview(db: Session, profile_id: str) -> dict[str, Any]:
    """Vue d'ensemble des KPIs Career (P43). Agrégation factuelle — anti-invention."""
    apps = _apps_for(db, profile_id)
    total_apps = len(apps)
    by_status = {"submitted": 0, "interview": 0, "offer": 0, "rejected": 0, "withdrawn": 0}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1

    # Offres détectées (globales, pool non user-scoped) + compatibles (new_match distinct)
    offers_detected = db.query(models.JobPosting).filter_by(is_expired=False).count()
    new_match_notifs = db.query(models.CareerNotification).filter_by(
        career_profile_id=profile_id, type="new_match").all()
    offers_compatible = len(new_match_notifs)

    # Score moyen matching (avg payload.score des new_match)
    scores = []
    for n in new_match_notifs:
        p = n.payload or {}
        if isinstance(p, dict) and isinstance(p.get("score"), (int, float)):
            scores.append(p["score"])
    avg_match_score = round(sum(scores) / len(scores)) if scores else 0

    # Taux réponse / entretien (sur apps envoyées — exclut withdrawn pour taux réponse)
    non_withdrawn = total_apps - by_status["withdrawn"]
    responded = by_status["interview"] + by_status["offer"] + by_status["rejected"]
    response_rate = round(responded / non_withdrawn * 100) if non_withdrawn else 0
    interviewed = by_status["interview"] + by_status["offer"]
    interview_rate = round(interviewed / total_apps * 100) if total_apps else 0

    # Sessions entretien (P41)
    sessions = db.query(models.InterviewSession).filter_by(career_profile_id=profile_id).all()
    completed = [s for s in sessions if s.status == "completed" and s.score is not None]
    avg_interview_score = round(sum(s.score for s in completed) / len(completed)) if completed else 0

    return {
        "offers_detected": offers_detected,
        "offers_compatible": offers_compatible,
        "applications_total": total_apps,
        "applications_by_status": by_status,
        "avg_match_score": avg_match_score,
        "response_rate": response_rate,
        "interview_rate": interview_rate,
        "rejections": by_status["rejected"],
        "offers_received": by_status["offer"],
        "interview_sessions": len(sessions),
        "interview_sessions_completed": len(completed),
        "avg_interview_score": avg_interview_score,
        "cvs_count": db.query(models.CV).filter_by(career_profile_id=profile_id).count(),
        "note": "Statistiques factuelles agrégées (anti-invention) — aucune interprétation ni prédiction.",
    }


def performance_by_platform(db: Session, profile_id: str) -> list[dict]:
    """Applications groupées par plateforme (posting.source_connector). Pour chaque plateforme :
    total, par statut, taux réponse. Jointure app.posting_id → JobPosting.source_connector."""
    apps = _apps_for(db, profile_id)
    by_plat: dict[str, dict] = {}
    for a in apps:
        plat = "manuel"
        if a.posting_id:
            p = db.query(models.JobPosting).filter_by(id=a.posting_id).first()
            if p:
                plat = p.source_connector
        d = by_plat.setdefault(plat, {"platform": plat, "total": 0, "submitted": 0, "interview": 0,
                                       "offer": 0, "rejected": 0, "withdrawn": 0})
        d["total"] += 1
        d[a.status] = d.get(a.status, 0) + 1
    # taux réponse par plateforme
    for d in by_plat.values():
        nw = d["total"] - d["withdrawn"]
        d["response_rate"] = round((d["interview"] + d["offer"] + d["rejected"]) / nw * 100) if nw else 0
    return sorted(by_plat.values(), key=lambda x: x["total"], reverse=True)


def performance_by_cv(db: Session, profile_id: str) -> list[dict]:
    """Applications groupées par version de CV (adapted_cv_id). Pour chaque CV : total + statuts.
    Les candidatures sans adapted_cv sont groupées sous 'original'."""
    apps = _apps_for(db, profile_id)
    by_cv: dict[str, dict] = {}
    for a in apps:
        key = a.adapted_cv_id or "original"
        d = by_cv.setdefault(key, {"cv_id": key, "total": 0, "interview": 0, "offer": 0, "rejected": 0})
        d["total"] += 1
        d[a.status] = d.get(a.status, 0) + 1
    # libellé du CV (titre si adapted exists)
    for d in by_cv.values():
        if d["cv_id"] != "original":
            ac = db.query(models.AdaptedCV).filter_by(id=d["cv_id"]).first()
            d["label"] = ac.title if ac else d["cv_id"]
        else:
            d["label"] = "CV original"
    return sorted(by_cv.values(), key=lambda x: x["total"], reverse=True)


def salary_trends(db: Session, profile_id: str, months: int = 6) -> list[dict]:
    """Évolution des salaires + TJM : pour chaque mois (N derniers), avg salary_min/max des postings
    candidatés ce mois. Factuel — pas de prédiction. Mois sans candidature → absent (pas de 0 inventé)."""
    apps = _apps_for(db, profile_id)
    since = datetime.utcnow() - timedelta(days=months * 30)
    by_month: dict[str, list] = {}
    for a in apps:
        if a.applied_at < since or not a.posting_id:
            continue
        p = db.query(models.JobPosting).filter_by(id=a.posting_id).first()
        if not p or (p.salary_min is None and p.salary_max is None):
            continue
        key = a.applied_at.strftime("%Y-%m")
        by_month.setdefault(key, []).append((p.salary_min, p.salary_max))
    out = []
    for month, vals in sorted(by_month.items()):
        mins = [v[0] for v in vals if v[0] is not None]
        maxs = [v[1] for v in vals if v[1] is not None]
        out.append({
            "month": month,
            "avg_salary_min": round(sum(mins) / len(mins)) if mins else None,
            "avg_salary_max": round(sum(maxs) / len(maxs)) if maxs else None,
            "applications": len(vals),
        })
    return out


def pipeline(db: Session, profile_id: str) -> dict[str, int]:
    """Distribution simple des candidatures par statut (pour graphique funnel)."""
    apps = _apps_for(db, profile_id)
    out = {"submitted": 0, "interview": 0, "offer": 0, "rejected": 0, "withdrawn": 0}
    for a in apps:
        out[a.status] = out.get(a.status, 0) + 1
    return out
