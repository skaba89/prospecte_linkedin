"""Scheduler & logique d'automation des séquences (P15).

process_due_enrollments(db) : traite les enrollments dus (status=active, next_step_at <= now).
Pour chaque : envoie l'e-mail du step courant via send_email_to_contact (P13 helper), avance le
step, calcule next_step_at (now + next_step.delay_days). Dernier step → status=completed. Échec
envoi (real failure, pas skip RGPD) → status=failed. Skip RGPD/placeholder → avance quand même
(ne bloque pas la séquence sur un email désinscrit).

start_scheduler() : background thread daemon (stdlib threading, pas de nouvelle dep). Tourne
toutes les SEQUENCE_SCHEDULER_INTERVAL_SECONDS (défaut 300s, override pour tests). Désactivé si
interval <= 0. Démarré depuis main._startup. Multi-worker caveat : en production multi-workers,
chaque worker lance son scheduler → utiliser --workers 1 OU un process scheduler dédié (noté
dans le README). Pour cette app (single-worker uvicorn), OK.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Contact, EmailSequence, SequenceEnrollment, SequenceStep, Tenant
from .task_reminders import process_due_task_reminders  # P23 : rappels de tâches dans le même loop
from .webhooks import emit_event

_log = logging.getLogger("revpulse.sequences")

# Statuts de send_email_to_contact considérés comme « envoi OK » (avance la séquence).
# skipped = RGPD/placeholder — on avance (ne bloque pas la séquence sur un email désinscrit).
# failed = échec réel (SMTP/template) → on bloque l'enrollment.
_OK_STATUSES = {"sent", "dry_run", "skipped"}


def process_due_enrollments(db: Session) -> dict:
    """Traite tous les enrollments dus (next_step_at <= now, status=active). Une passe par appel —
    chaque enrollment est traité au plus une fois par passe (la liste `due` est figée au début).
    Retourne {processed, sent, skipped, failed, completed}. Commit à la fin."""
    # Import lazy pour éviter tout cycle d'import au chargement du module.
    from ..routers.notifications import send_email_to_contact

    now = datetime.utcnow()
    due = (
        db.query(SequenceEnrollment)
        .filter(SequenceEnrollment.status == "active", SequenceEnrollment.next_step_at <= now)
        .all()
    )
    stats = {"processed": 0, "sent": 0, "skipped": 0, "failed": 0, "completed": 0}
    for enr in due:
        stats["processed"] += 1
        seq = db.query(EmailSequence).filter_by(id=enr.sequence_id).first()
        # Séquence supprimée ou pausée/draft → skip (le scheduler ne traite que active).
        if not seq or seq.status != "active":
            continue
        step = db.query(SequenceStep).filter_by(sequence_id=enr.sequence_id, step_order=enr.current_step_order).first()
        contact = db.query(Contact).filter_by(id=enr.contact_id).first()
        if not contact or not step:
            enr.status = "failed"
            enr.error_detail = "Contact ou step introuvable."
            stats["failed"] += 1
            continue
        tenant = db.query(Tenant).filter_by(id=seq.tenant_id).first()
        tenant_name = tenant.name if tenant else ""
        result = send_email_to_contact(
            db,
            seq.tenant_id,
            contact,
            step.template_id,
            actor_user_id=enr.enrolled_by_user_id,
            actor_name="Sequence scheduler",
            tenant_name=tenant_name,
        )
        if result.status not in _OK_STATUSES:
            # Échec réel (SMTP down, template inconnu) → bloque l'enrollment.
            enr.status = "failed"
            enr.error_detail = result.detail
            stats["failed"] += 1
            continue
        # Envoi OK (sent/dry_run) ou skip RGPD → avance la séquence.
        if result.status in ("sent", "dry_run"):
            stats["sent"] += 1
        else:
            stats["skipped"] += 1
        next_order = step.step_order + 1
        next_step = db.query(SequenceStep).filter_by(sequence_id=enr.sequence_id, step_order=next_order).first()
        if next_step:
            enr.current_step_order = next_order
            enr.next_step_at = now + timedelta(days=next_step.delay_days)
        else:
            enr.status = "completed"
            enr.completed_at = now
            stats["completed"] += 1
        emit_event(
            db,
            tenant_id=seq.tenant_id,
            event="sequence.step_completed",
            payload={
                "enrollment_id": enr.id,
                "tenant_id": seq.tenant_id,
                "sequence_id": enr.sequence_id,
                "contact_id": enr.contact_id,
                "step_order": step.step_order,
                "template_id": step.template_id,
                "completed": enr.status == "completed",
            },
        )
    db.commit()
    if stats["processed"]:
        _log.info("process_due_enrollments: %s", stats)
    return stats


def process_sequence(db: Session, sequence_id: str, tenant_id: str) -> dict:
    """Traite les enrollments dus d'UNE séquence (manual trigger — endpoint /process). Filtre
    par sequence_id + tenant_id (multi-tenant strict). Retourne les mêmes stats que process_due."""
    from ..routers.notifications import send_email_to_contact

    now = datetime.utcnow()
    due = (
        db.query(SequenceEnrollment)
        .join(EmailSequence, SequenceEnrollment.sequence_id == EmailSequence.id)
        .filter(
            SequenceEnrollment.status == "active",
            SequenceEnrollment.next_step_at <= now,
            EmailSequence.id == sequence_id,
            EmailSequence.tenant_id == tenant_id,
        )
        .all()
    )
    stats = {"processed": 0, "sent": 0, "skipped": 0, "failed": 0, "completed": 0}
    for enr in due:
        stats["processed"] += 1
        step = db.query(SequenceStep).filter_by(sequence_id=enr.sequence_id, step_order=enr.current_step_order).first()
        contact = db.query(Contact).filter_by(id=enr.contact_id).first()
        if not contact or not step:
            enr.status = "failed"
            enr.error_detail = "Contact ou step introuvable."
            stats["failed"] += 1
            continue
        tenant = db.query(Tenant).filter_by(id=tenant_id).first()
        result = send_email_to_contact(
            db,
            tenant_id,
            contact,
            step.template_id,
            actor_user_id=enr.enrolled_by_user_id,
            actor_name="Sequence scheduler (manual)",
            tenant_name=tenant.name if tenant else "",
        )
        if result.status not in _OK_STATUSES:
            enr.status = "failed"
            enr.error_detail = result.detail
            stats["failed"] += 1
            continue
        if result.status in ("sent", "dry_run"):
            stats["sent"] += 1
        else:
            stats["skipped"] += 1
        next_order = step.step_order + 1
        next_step = db.query(SequenceStep).filter_by(sequence_id=enr.sequence_id, step_order=next_order).first()
        if next_step:
            enr.current_step_order = next_order
            enr.next_step_at = now + timedelta(days=next_step.delay_days)
        else:
            enr.status = "completed"
            enr.completed_at = now
            stats["completed"] += 1
        emit_event(
            db,
            tenant_id=tenant_id,
            event="sequence.step_completed",
            payload={
                "enrollment_id": enr.id,
                "tenant_id": tenant_id,
                "sequence_id": enr.sequence_id,
                "contact_id": enr.contact_id,
                "step_order": step.step_order,
                "template_id": step.template_id,
                "completed": enr.status == "completed",
            },
        )
    db.commit()
    return stats


def start_scheduler() -> None:
    """Démarre le scheduler de séquences (background thread daemon, stdlib threading — pas de
    nouvelle dep). Tourne toutes les SEQUENCE_SCHEDULER_INTERVAL_SECONDS (défaut 300s). Désactivé
    si interval <= 0. Une boucle infinie : process_due_enrollments → sleep(interval)."""
    interval = int(os.getenv("SEQUENCE_SCHEDULER_INTERVAL_SECONDS", "300"))
    if interval <= 0:
        _log.info("Sequence scheduler disabled (SEQUENCE_SCHEDULER_INTERVAL_SECONDS=%s).", interval)
        return

    def _loop():
        while True:
            db = SessionLocal()
            try:
                process_due_enrollments(db)
                process_due_task_reminders(db)  # P23 : rappels de tâches (même thread, après séquences)
            except Exception as e:  # scheduler ne doit jamais crasher (catch large volontaire)
                _log.exception("Sequence scheduler error: %s", e)
            finally:
                db.close()
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="revpulse-seq-scheduler")
    t.start()
    _log.info("Sequence scheduler started (interval=%ss).", interval)
