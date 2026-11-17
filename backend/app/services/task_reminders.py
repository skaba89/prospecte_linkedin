"""P23 — Scheduler de rappels de tâches.

process_due_task_reminders(db) : trouve les Task dont reminder_at <= now AND NOT reminder_sent AND
status != done → crée une Notification in-app (feed) vers l'assignee + flip reminder_sent=True
(idempotence : un rappel n'est envoyé qu'une fois par tâche). Hooké dans start_scheduler
(services/sequences.py) — tourne dans le même background thread daemon que les séquences.

Notification créée :
  - type="in_app", channel="feed" (cloche dashboard, pas d'SMTP)
  - user_id = task.assignee_id (le destinataire du rappel — pour le feed, user_id EST le recipient)
  - recipient_contact_id = task.contact_id (contexte : lien vers le contact concerné)
  - subject="⏰ Rappel : <title>", body_text="Échéance : <due_date>" + nom du contact si lié
  - status="sent" (in-app, pas de file SMTP)

Skip si assignee_id is None (personne à notifier) — reminder_sent reste False jusqu'à ce qu'un
assignee soit set + reminder_at rechuché (le router update_task reset reminder_sent sur changement
de reminder_at). Le scheduler est résilient : catch large, log, continue (jamais de crash).
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from ..audit import log_audit
from ..database import SessionLocal

_log = logging.getLogger("revpulse.task_reminders")


def process_due_task_reminders(db: Session) -> dict:
    """Traite les rappels de tâches dus. Retourne {sent, skipped_no_assignee, processed}.

    Query : reminder_at IS NOT NULL AND reminder_at <= now AND NOT reminder_sent AND status != done.
    Pour chaque tâche :
      - si assignee_id is None → skip (reminder_sent reste False, retentera après assign).
      - sinon → créer Notification in-app feed vers assignee + reminder_sent=True + audit.
    Commit par batch à la fin (atomique)."""
    now = datetime.utcnow()
    due = (
        db.query(models.Task)
        .filter(
            models.Task.reminder_at.isnot(None),
            models.Task.reminder_at <= now,
            ~models.Task.reminder_sent,  # NOT reminder_sent
            models.Task.status != "done",
        )
        .all()
    )
    sent = 0
    skipped_no_assignee = 0
    for t in due:
        if not t.assignee_id:
            skipped_no_assignee += 1
            continue
        # Nom du contact lié (contexte du body)
        contact_name = ""
        if t.contact_id:
            c = db.query(models.Contact).filter_by(id=t.contact_id).first()
            if c:
                contact_name = f" — Contact : {c.first_name} {c.last_name}".strip()
        due_str = t.due_date.isoformat() if t.due_date else "non définie"
        notif = models.Notification(
            tenant_id=t.tenant_id,
            user_id=t.assignee_id,  # destinataire du feed (in-app)
            type="in_app",
            channel="feed",
            recipient_email=None,
            recipient_contact_id=t.contact_id,
            subject=f"⏰ Rappel : {t.title}",
            body_text=f"Échéance : {due_str}{contact_name}",
            status="sent",
        )
        db.add(notif)
        t.reminder_sent = True
        log_audit(
            db,
            tenant_id=t.tenant_id,
            actor_user_id=None,
            actor_name="Task reminder scheduler",
            action="task.reminder_sent",
            target_type="task",
            target_id=t.id,
            detail={"title": t.title, "assignee_id": t.assignee_id, "reminder_at": t.reminder_at.isoformat()},
        )
        sent += 1
    if sent:
        db.commit()
    _log.info("Task reminders: %d sent, %d skipped (no assignee), %d due.", sent, skipped_no_assignee, len(due))
    return {"sent": sent, "skipped_no_assignee": skipped_no_assignee, "processed": len(due)}


def run_once() -> dict:
    """Point d'entrée manuel (tests / admin) : ouvre une session, process, ferme."""
    db = SessionLocal()
    try:
        return process_due_task_reminders(db)
    finally:
        db.close()
