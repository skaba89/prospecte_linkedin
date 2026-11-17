"""career_notifier.py — Service notifications Career (P40).

Génère + persiste les notifications Career (ISOLÉES du Business Notification tenant_id).
3 types :
  - new_match : une offre matche le profil avec score P36 ≥ alert_min_score (hook matching/search).
  - application_status : changement statut candidature → interview/offer/rejected (hook PATCH /applications).
  - interview_reminder : rappel entretien à venir (v2 scheduler).

Anti-spam (P40 invariant) :
  - alert_enabled=False (opt-out) → JAMAIS de notification (notify_* retourne None).
  - dedup par (user_id, type, payload_key) : une seule notif par posting (new_match) / par
    (application_id, new_status) (application_status). Évite le spam si matching relancé N fois.
  - channel : si "in_app" in alert_channels → notif in_app ; "email" → v2 envoi email réel (v1 in_app only).

v2 : scheduler alertes périodiques (daily/weekly) + envoi email réel via email_sender OAuth2.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models


def _channels_active(profile: models.CareerProfile) -> list[str]:
    """Canaux activés (défaut in_app si non configuré)."""
    ch = profile.alert_channels or ["in_app"]
    return ch if isinstance(ch, list) else ["in_app"]


def _dedup_exists(db: Session, user_id: str, ntype: str, payload_key: str, payload_value: str) -> bool:
    """Anti-spam : une notif existe déjà pour (user, type, payload[key]=value) ?"""
    notifs = db.query(models.CareerNotification).filter_by(user_id=user_id, type=ntype).all()
    for n in notifs:
        p = n.payload or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {}
        if str(p.get(payload_key)) == str(payload_value):
            return True
    return False


def notify_new_match(db: Session, profile: models.CareerProfile, cv_id: str | None,
                     posting: models.JobPosting, score: float) -> models.CareerNotification | None:
    """Génère une notif new_match si : alert_enabled ET score ≥ alert_min_score ET "in_app" in channels
    ET pas déjà notifié pour ce posting (dedup anti-spam). Retourne la notif créée ou None."""
    if not profile.alert_enabled:
        return None  # opt-out explicite (anti-spam invariant)
    if score < (profile.alert_min_score or 0):
        return None  # sous le seuil P36 configuré
    if "in_app" not in _channels_active(profile):
        return None  # canal in_app désactivé
    if _dedup_exists(db, profile.user_id, "new_match", "posting_id", posting.id):
        return None  # déjà notifié pour ce posting (anti-spam dedup)
    title = f"Nouvelle offre compatible : {posting.title}"
    body = (
        f"{posting.title} chez {posting.company or '?'} — score de compatibilité {int(score)}/100. "
        f"Lieu : {posting.location or 'non précisé'}. Source : {posting.source_connector or '?'}."
    )
    notif = models.CareerNotification(
        user_id=profile.user_id, career_profile_id=profile.id, type="new_match", channel="in_app",
        title=title, body=body,
        payload={"posting_id": posting.id, "cv_id": cv_id, "score": int(score),
                 "job_title": posting.title, "company": posting.company},
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return notif


def notify_application_status(db: Session, profile: models.CareerProfile,
                              application: models.JobApplication,
                              old_status: str, new_status: str) -> models.CareerNotification | None:
    """Génère une notif application_status sur changement vers interview/offer/rejected (statuts notables).
    Dedup par (application_id, new_status) — une seule notif par transition. Opt-out si alert_enabled=False."""
    if not profile.alert_enabled:
        return None
    # Seuls les statuts notables déclenchent une notif (pas submitted/withdrawn — bruit).
    notable = {"interview": "Invitation à un entretien", "offer": "Proposition reçue", "rejected": "Réponse : refus"}
    if new_status not in notable:
        return None
    if _dedup_exists(db, profile.user_id, "application_status", "application_id_status",
                     f"{application.id}_{new_status}"):
        return None
    label = notable[new_status]
    title = f"{label} : {application.job_title}"
    body = (
        f"Candidature « {application.job_title} » chez {application.company} — statut passé "
        f"de « {old_status} » à « {new_status} »."
    )
    notif = models.CareerNotification(
        user_id=profile.user_id, career_profile_id=profile.id, type="application_status", channel="in_app",
        title=title, body=body,
        payload={"application_id": application.id, "application_id_status": f"{application.id}_{new_status}",
                 "old_status": old_status, "new_status": new_status,
                 "job_title": application.job_title, "company": application.company},
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return notif


def list_notifications(db: Session, user_id: str, unread_only: bool = False,
                       limit: int = 50) -> list[models.CareerNotification]:
    """Liste les notifs Career du user (plus récentes d'abord). unread_only filtre read_at is null."""
    q = db.query(models.CareerNotification).filter_by(user_id=user_id)
    if unread_only:
        q = q.filter(models.CareerNotification.read_at.is_(None))
    return q.order_by(models.CareerNotification.created_at.desc()).limit(limit).all()


def unread_count(db: Session, user_id: str) -> int:
    """Nombre de notifs non lues (badge dashboard)."""
    return db.query(models.CareerNotification).filter_by(user_id=user_id, read_at=None).count()


def mark_read(db: Session, user_id: str, notif_id: str) -> models.CareerNotification | None:
    """Marque une notif comme lue (idempotent : si déjà lue, retourne telle quelle). Isolation user_id."""
    n = db.query(models.CareerNotification).filter_by(id=notif_id, user_id=user_id).first()
    if not n:
        return None
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
        db.refresh(n)
    return n


def mark_all_read(db: Session, user_id: str) -> int:
    """Marque toutes les notifs non lues du user comme lues. Retourne le nombre marqué."""
    notifs = db.query(models.CareerNotification).filter_by(user_id=user_id, read_at=None).all()
    count = 0
    for n in notifs:
        n.read_at = datetime.utcnow()
        count += 1
    if count:
        db.commit()
    return count
