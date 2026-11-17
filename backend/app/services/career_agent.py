"""career_agent.py — Agent autonome Career (P42).

Orchestre les 10 étapes (mission §11) en une exécution run_once déclenchée manuellement (v1) :
  1. Rechercher les offres (JobPosting actifs filtrés par profil).
  2. Comparer avec le CV actif (matching.compute_match P36).
  3. Classer par score.
  4. Adapter les CV (cv_adapter.adapt_cv P37) — prépare versions ciblées.
  5. Préparer lettres de motivation (cover_letter.generate_cover_letter P38, anti-invention).
  6. Soumettre les candidatures (JobApplication) — UNIQUEMENT si consent_given + auto_apply + sous
     max_applications_per_day. Sinon : préparation seulement (consent invariant).
  7. Surveiller réponses (récap candidatures existantes par statut).
  8. Notifications (récap new_match notifs P40 générées par le matching).
  9. Préparer entretiens (interview_coach.generate_questions P41 pour top matchs).
  10. Rapport quotidien (summary agrégé + analytics snapshot P43).

Invariants P42 :
  - Consent explicite : consent_given=False OU auto_apply=False → étape 6 = préparation seulement (0 envoyée).
  - Limit anti-abus : max_applications_per_day cap + daily_sent_count reset quotidien.
  - Bouton arrêt immédiat : agent.status="stopped" → run interrompu entre étapes (status=stopped).
  - Journalisation complète : chaque run persisté (CareerAgentRun) + steps détaillés [{step, status, count}].
  - Anti-invention : rapport factuel agrégé (offres/scores/candidatures), aucune prédiction ni invention.

v2 : scheduler quotidien (celery/APScheduler) + monitoring continu + envoi auto avec consentement.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from ..services import career_analytics, cover_letter, cv_adapter, interview_coach, matching


def get_or_create_agent(db: Session, profile: models.CareerProfile) -> models.CareerAgent:
    """Récupère ou crée l'agent Career du profil (1 par profil, unique)."""
    agent = db.query(models.CareerAgent).filter_by(career_profile_id=profile.id).first()
    if not agent:
        agent = models.CareerAgent(career_profile_id=profile.id, status="idle")
        db.add(agent)
        db.commit()
        db.refresh(agent)
    return agent


def _reset_daily_if_needed(db: Session, agent: models.CareerAgent) -> None:
    """Reset quotidien du compteur daily_sent_count si > 24h depuis daily_reset_at."""
    now = datetime.utcnow()
    if agent.daily_reset_at is None or (now - agent.daily_reset_at) > timedelta(hours=24):
        agent.daily_sent_count = 0
        agent.daily_reset_at = now
        db.commit()


def run_once(db: Session, profile: models.CareerProfile, agent: models.CareerAgent,
             top_n: int = 5) -> models.CareerAgentRun:
    """Exécute les 10 étapes de l'agent. Retourne le CareerAgentRun (rapport).
    v1 : déclenchement manuel (POST /agent/run). v2 : scheduler quotidien."""
    if agent.status == "running":
        # ne pas démarrer 2 runs concurrents
        raise RuntimeError("Un run est déjà en cours.")
    _reset_daily_if_needed(db, agent)
    agent.status = "running"
    db.commit()
    run = models.CareerAgentRun(career_profile_id=profile.id, agent_id=agent.id,
                                started_at=datetime.utcnow(), status="running", steps=[], summary={})
    db.add(run)
    db.commit()
    db.refresh(run)
    steps: list[dict] = []
    summary: dict[str, Any] = {}
    prepared = 0
    sent = 0

    def step(name: str, status: str, count: int = 0, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "count": count, "detail": detail})

    def stopped_check() -> bool:
        """Bouton arrêt : re-read agent status depuis la DB."""
        db.refresh(agent)
        if agent.status == "stopped":
            run.status = "stopped"
            run.completed_at = datetime.utcnow()
            step("stop", "stopped", 0, "Arrêt immédiat demandé par l'utilisateur.")
            return True
        return False

    try:
        # CV actif requis
        cv = db.query(models.CV).filter_by(career_profile_id=profile.id, is_active=True).first()
        if not cv:
            step("cv_check", "failed", 0, "Aucun CV actif — configurez un CV avant de lancer l'agent.")
            run.status = "failed"
        else:
            # Étape 1 : rechercher offres actives
            q = db.query(models.JobPosting).filter_by(is_expired=False)
            if profile.preferred_location:
                loc = profile.preferred_location.lower()
                if loc != "remote":
                    q = q.filter(models.JobPosting.location.ilike(f"%{loc}%"))
            postings = q.order_by(models.JobPosting.fetched_at.desc()).limit(50).all()
            step("search", "ok", len(postings), f"{len(postings)} offres actives détectées.")
            summary["offers_found"] = len(postings)
            if stopped_check():
                raise RuntimeError("stopped")

            # Étape 2 + 3 : matching + ranking
            scored = []
            for p in postings:
                bd = matching.compute_match(cv, p, profile)
                scored.append((p, bd.total_score, bd))
            scored.sort(key=lambda x: x[1], reverse=True)
            compatible = [(p, s, bd) for p, s, bd in scored if s >= (profile.alert_min_score or 0)]
            step("match_rank", "ok", len(compatible), f"{len(compatible)} offres compatibles (score ≥ {profile.alert_min_score or 0}).")
            summary["offers_compatible"] = len(compatible)
            summary["avg_match_score"] = round(sum(s for _, s, _ in compatible) / len(compatible)) if compatible else 0
            if stopped_check():
                raise RuntimeError("stopped")

            top = compatible[:top_n]
            # Étape 4 : adapter CV pour top matchs (prépare versions ciblées)
            adapted_count = 0
            for p, _s, _bd in top:
                try:
                    cv_adapter.adapt_cv(cv, p, profile)  # prépare (persiste adapted_cv via le service)
                    adapted_count += 1
                except Exception:
                    pass
            step("adapt_cv", "ok", adapted_count, f"{adapted_count} CV adaptés (anti-invention P37).")
            summary["cvs_adapted"] = adapted_count

            # Étape 5 : préparer lettres de motivation (anti-invention)
            letters = 0
            for p, _s, _bd in top:
                try:
                    cover_letter.generate_cover_letter(cv, p, profile, candidate_name="")
                    letters += 1
                except Exception:
                    pass
            step("cover_letters", "ok", letters, f"{letters} lettres préparées (anti-invention P38).")
            summary["cover_letters_prepared"] = letters
            prepared = letters

            # Étape 6 : soumettre candidatures — UNIQUEMENT si consent + auto_apply + sous limite
            can_send = agent.consent_given and agent.auto_apply
            remaining_quota = max(0, agent.max_applications_per_day - agent.daily_sent_count)
            sent_detail = "préparation seulement"
            if can_send:
                sent_detail = f"envoi auto activé (quota restant {remaining_quota})"
                for p, _s, _bd in top[:remaining_quota]:
                    if stopped_check():
                        raise RuntimeError("stopped")
                    try:
                        app = models.JobApplication(
                            career_profile_id=profile.id, posting_id=p.id, company=p.company or "?",
                            job_title=p.title, status="submitted", application_method="manual",
                            applied_at=datetime.utcnow())
                        db.add(app)
                        db.commit()
                        sent += 1
                        agent.daily_sent_count += 1
                    except Exception:
                        pass
            step("submit", "ok" if can_send else "skipped", sent,
                 f"{sent} candidatures envoyées (consent={agent.consent_given}, auto_apply={agent.auto_apply}, {sent_detail}).")
            summary["applications_sent"] = sent
            summary["consent_given"] = agent.consent_given
            summary["auto_apply"] = agent.auto_apply
            if stopped_check():
                raise RuntimeError("stopped")

            # Étape 7 : surveiller réponses (récap candidatures existantes par statut)
            apps = db.query(models.JobApplication).filter_by(career_profile_id=profile.id).all()
            by_status = {"submitted": 0, "interview": 0, "offer": 0, "rejected": 0, "withdrawn": 0}
            for a in apps:
                by_status[a.status] = by_status.get(a.status, 0) + 1
            step("monitor", "ok", len(apps), f"{len(apps)} candidatures suivies : {by_status}.")
            summary["applications_by_status"] = by_status

            # Étape 8 : notifications (new_match générées par matching P40 — récap)
            notifs = db.query(models.CareerNotification).filter_by(
                career_profile_id=profile.id, type="new_match").count()
            unread = db.query(models.CareerNotification).filter_by(
                career_profile_id=profile.id, read_at=None).count()
            step("notifications", "ok", notifs, f"{notifs} notifs new_match total, {unread} non lues.")
            summary["notifications_total"] = notifs
            summary["notifications_unread"] = unread

            # Étape 9 : préparer entretiens (questions générées pour top matchs)
            sessions_prep = 0
            for p, _s, _bd in top:
                try:
                    interview_coach.generate_questions(p.title, None, "rhmixed")
                    sessions_prep += 1
                except Exception:
                    pass
            step("interview_prep", "ok", sessions_prep, f"{sessions_prep} prep entretien générées (P41).")
            summary["interview_preps"] = sessions_prep

            # Étape 10 : rapport — analytics snapshot
            ov = career_analytics.overview(db, profile.id)
            step("report", "ok", 1, "Rapport quotidien généré (analytics P43).")
            summary["analytics"] = {k: ov.get(k) for k in
                                    ["offers_detected", "applications_total", "response_rate",
                                     "interview_rate", "offers_received", "rejections"]}
            summary["note"] = ("Rapport factuel agrégé (anti-invention). Candidatures envoyées uniquement "
                               "avec consentement explicite + auto_apply + quota journalier.")
            run.status = "success"

    except RuntimeError as e:
        if str(e) == "stopped":
            run.status = "stopped"
        else:
            run.status = "failed"
            step("error", "failed", 0, str(e))
    except Exception as e:
        run.status = "failed"
        step("error", "failed", 0, f"Erreur inattendue : {e}")

    # Finalisation
    run.steps = steps
    run.summary = summary
    run.applications_prepared = prepared
    run.applications_sent = sent
    run.completed_at = datetime.utcnow()
    agent.last_run_at = datetime.utcnow()
    if agent.status != "stopped":
        agent.status = "idle"
    db.commit()
    db.refresh(run)
    return run


def stop(db: Session, agent: models.CareerAgent) -> models.CareerAgent:
    """Bouton d'arrêt immédiat : status=stopped. Le run en cours s'interrompt au prochain check."""
    agent.status = "stopped"
    db.commit()
    db.refresh(agent)
    return agent


def list_runs(db: Session, profile_id: str, limit: int = 20) -> list[models.CareerAgentRun]:
    """Historique des runs (plus récents d'abord)."""
    return db.query(models.CareerAgentRun).filter_by(career_profile_id=profile_id).order_by(
        models.CareerAgentRun.started_at.desc()).limit(limit).all()
