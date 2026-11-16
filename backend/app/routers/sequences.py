"""Séquences & automatisation (P15). Drip campaigns d'e-mails multi-steps.

Routes (préfixe /api, scopées par tenant via get_tenant) :
  POST   /tenants/{tid}/crm/sequences                   → crée séquence + steps (RBAC crm.manage)
  GET    /tenants/{tid}/crm/sequences                   → liste (tout membre)
  GET    /tenants/{tid}/crm/sequences/{sid}             → détail (steps + count enrollments)
  PATCH  /tenants/{tid}/crm/sequences/{sid}             → update (name/description/status) (crm.manage)
  DELETE /tenants/{tid}/crm/sequences/{sid}             → supprime (cascade steps + enrollments) (crm.manage)
  POST   /tenants/{tid}/crm/sequences/{sid}/enroll      → enrôle un contact (crm.manage, 409 si déjà active)
  GET    /tenants/{tid}/crm/sequences/{sid}/enrollments → liste enrollments (filtre status, tout membre)
  POST   /tenants/{tid}/crm/sequences/{sid}/process     → trigger manuel process_sequence (crm.manage, admin/test)
  GET    /tenants/{tid}/crm/sequences/{sid}/stats       → counts par statut d'enrollment (tout membre)

Le scheduler (services/sequences.py start_scheduler, démarré sur app startup) traite les enrollments
dus auto toutes les SEQUENCE_SCHEDULER_INTERVAL_SECONDS (défaut 300s). /process = trigger manuel.
Multi-tenant strict. Audit crm.sequence_*. Réutilise send_email_to_contact (P13 helper) pour l'envoi.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..services.sequences import process_sequence

router = APIRouter(prefix="/api", tags=["sequences"])
_log = logging.getLogger("revpulse.sequences")

_VALID_TEMPLATES = {"cold_outreach", "follow_up", "intro", "re_engagement"}
_VALID_STATUSES = {"draft", "active", "paused"}


def _to_out(db: Session, seq: models.EmailSequence) -> schemas.SequenceOut:
    """Sérialise une séquence avec ses steps (triés par step_order) + count enrollments."""
    steps = (db.query(models.SequenceStep)
             .filter_by(sequence_id=seq.id)
             .order_by(models.SequenceStep.step_order.asc()).all())
    enr_count = db.query(models.SequenceEnrollment).filter_by(sequence_id=seq.id).count()
    return schemas.SequenceOut(
        id=seq.id, tenant_id=seq.tenant_id, name=seq.name, description=seq.description or "",
        status=seq.status, created_by=seq.created_by, created_at=seq.created_at,
        updated_at=seq.updated_at,
        steps=[schemas.SequenceStepOut(id=s.id, step_order=s.step_order, template_id=s.template_id,
                                       delay_days=s.delay_days) for s in steps],
        enrollments_count=enr_count)


def _enr_to_out(enr: models.SequenceEnrollment) -> schemas.SequenceEnrollmentOut:
    return schemas.SequenceEnrollmentOut(
        id=enr.id, sequence_id=enr.sequence_id, contact_id=enr.contact_id,
        current_step_order=enr.current_step_order, status=enr.status,
        enrolled_by_user_id=enr.enrolled_by_user_id, enrolled_at=enr.enrolled_at,
        next_step_at=enr.next_step_at, completed_at=enr.completed_at, error_detail=enr.error_detail)


@router.post("/tenants/{tid}/crm/sequences", response_model=schemas.SequenceOut,
             status_code=status.HTTP_201_CREATED)
def create_sequence(tid: str, payload: schemas.SequenceCreate,
                    db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                    membership=Depends(require_permission("crm.manage"))):
    """Crée une séquence + ses steps (P15). Valide : ≥1 step, template_id dans {cold_outreach,
    follow_up, intro, re_engagement}. step_order auto-assigné 1..N. Audit crm.sequence_create."""
    if not payload.steps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Une séquence doit avoir au moins 1 step.")
    for s in payload.steps:
        if s.template_id not in _VALID_TEMPLATES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Template inconnu : {s.template_id}. Valides : {', '.join(sorted(_VALID_TEMPLATES))}.")
    if payload.status and payload.status not in _VALID_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Statut invalide : {payload.status}. Valides : {', '.join(sorted(_VALID_STATUSES))}.")
    seq = models.EmailSequence(
        tenant_id=tid, name=payload.name, description=payload.description or "",
        status=payload.status or "draft", created_by=membership.user_id)
    db.add(seq)
    db.flush()
    for i, s in enumerate(payload.steps, 1):
        db.add(models.SequenceStep(sequence_id=seq.id, step_order=i,
                                   template_id=s.template_id, delay_days=s.delay_days))
    db.commit()
    db.refresh(seq)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_create", target_type="sequence", target_id=seq.id,
              detail={"name": seq.name, "steps": len(payload.steps), "status": seq.status})
    db.commit()
    return _to_out(db, seq)


@router.get("/tenants/{tid}/crm/sequences", response_model=list[schemas.SequenceOut])
def list_sequences(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Liste les séquences du tenant (P15), tri desc sur created_at."""
    seqs = (db.query(models.EmailSequence).filter_by(tenant_id=tid)
            .order_by(models.EmailSequence.created_at.desc()).all())
    return [_to_out(db, s) for s in seqs]


@router.get("/tenants/{tid}/crm/sequences/{sid}", response_model=schemas.SequenceOut)
def get_sequence(tid: str, sid: str, db: Session = Depends(get_db),
                 _t: models.Tenant = Depends(get_tenant)):
    """Détail d'une séquence (P15) : steps + count enrollments."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    return _to_out(db, seq)


@router.patch("/tenants/{tid}/crm/sequences/{sid}", response_model=schemas.SequenceOut)
def update_sequence(tid: str, sid: str, payload: schemas.SequenceUpdate,
                    db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                    membership=Depends(require_permission("crm.manage"))):
    """Mise à jour partielle d'une séquence (P15). Champs : name, description, status
    (draft|active|paused). Activer une séquence (draft→active) déclenche le scheduler sur les
    enrollments dus. Audit crm.sequence_update."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    if payload.status and payload.status not in _VALID_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Statut invalide : {payload.status}. Valides : {', '.join(sorted(_VALID_STATUSES))}.")
    if payload.name is not None:
        seq.name = payload.name
    if payload.description is not None:
        seq.description = payload.description
    if payload.status:
        seq.status = payload.status
    db.commit()
    db.refresh(seq)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_update", target_type="sequence", target_id=seq.id,
              detail={"status": seq.status, "name": seq.name})
    db.commit()
    return _to_out(db, seq)


@router.delete("/tenants/{tid}/crm/sequences/{sid}")
def delete_sequence(tid: str, sid: str, db: Session = Depends(get_db),
                    _t: models.Tenant = Depends(get_tenant),
                    membership=Depends(require_permission("crm.manage"))):
    """Supprime une séquence + ses steps + enrollments (P15). Cascade manuelle (le router delete
    les dépendances avant la séquence). Retourne {deleted, name} (200, pas 204 — testable via
    call() qui parse JSON). Audit crm.sequence_delete."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    name = seq.name
    db.query(models.SequenceStep).filter_by(sequence_id=sid).delete()
    db.query(models.SequenceEnrollment).filter_by(sequence_id=sid).delete()
    db.delete(seq)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_delete", target_type="sequence", target_id=sid,
              detail={"name": name})
    db.commit()
    return {"deleted": sid, "name": name}


@router.post("/tenants/{tid}/crm/sequences/{sid}/enroll",
             response_model=schemas.SequenceEnrollmentOut, status_code=status.HTTP_201_CREATED)
def enroll_contact(tid: str, sid: str, payload: schemas.EnrollRequest,
                   db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                   membership=Depends(require_permission("crm.manage"))):
    """Enrôle un contact dans une séquence (P15). current_step_order=1, next_step_at = now + step1.
    delay_days. 409 si déjà enrollé (active). Audit crm.sequence_enroll."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    contact = db.query(models.Contact).filter_by(tenant_id=tid, id=payload.contact_id).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable.")
    existing = (db.query(models.SequenceEnrollment)
                .filter_by(sequence_id=sid, contact_id=payload.contact_id, status="active").first())
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Contact déjà enrollé (active) dans cette séquence.")
    first_step = (db.query(models.SequenceStep).filter_by(sequence_id=sid)
                  .order_by(models.SequenceStep.step_order.asc()).first())
    if not first_step:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Séquence sans step.")
    now = datetime.utcnow()
    enr = models.SequenceEnrollment(
        sequence_id=sid, contact_id=payload.contact_id,
        current_step_order=first_step.step_order, status="active",
        enrolled_by_user_id=membership.user_id, enrolled_at=now,
        next_step_at=now + timedelta(days=first_step.delay_days))
    db.add(enr)
    db.commit()
    db.refresh(enr)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_enroll", target_type="contact", target_id=payload.contact_id,
              detail={"sequence_id": sid, "sequence_name": seq.name})
    db.commit()
    return _enr_to_out(enr)


# Clés de criteria acceptées par _apply_contact_filters (P11 + P16) — filtrage safe pour **kwargs.
_CRITERIA_KEYS = {"search", "status", "company_id", "owner_id", "score_min", "source",
                  "has_email", "no_email_days", "enrolled_in_sequence"}


@router.post("/tenants/{tid}/crm/sequences/{sid}/enroll-batch",
             response_model=schemas.EnrollBatchResult)
def enroll_batch(tid: str, sid: str, payload: schemas.EnrollBatchRequest,
                 db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                 membership=Depends(require_permission("crm.manage"))):
    """Batch enrollment (P16) : enrôle tous les contacts d'un segment (criteria appliqué) OU
    d'une liste explicite contact_ids. Skip les déjà activement enrollés (pas de 409 en batch).
    Connecte segments P11 (criteria étendu P16) → séquences P15. Audit crm.sequence_enroll_batch."""
    from .crm import _apply_contact_filters
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    first_step = (db.query(models.SequenceStep).filter_by(sequence_id=sid)
                  .order_by(models.SequenceStep.step_order.asc()).first())
    if not first_step:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Séquence sans step.")
    # Résolution des contacts : via segment_id (criteria appliqué) OU liste explicite.
    if payload.segment_id:
        seg = db.query(models.Segment).filter_by(tenant_id=tid, id=payload.segment_id).first()
        if not seg:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment introuvable.")
        q = db.query(models.Contact).filter_by(tenant_id=tid)
        crit = {k: v for k, v in (seg.criteria or {}).items() if k in _CRITERIA_KEYS}
        q = _apply_contact_filters(q, tid, **crit)
        contacts = q.all()
    else:
        if not payload.contact_ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "segment_id ou contact_ids requis.")
        contacts = (db.query(models.Contact).filter_by(tenant_id=tid)
                    .filter(models.Contact.id.in_(payload.contact_ids)).all())
    now = datetime.utcnow()
    enrolled = skipped = 0
    for contact in contacts:
        existing = (db.query(models.SequenceEnrollment)
                    .filter_by(sequence_id=sid, contact_id=contact.id, status="active").first())
        if existing:
            skipped += 1
            continue
        enr = models.SequenceEnrollment(
            sequence_id=sid, contact_id=contact.id,
            current_step_order=first_step.step_order, status="active",
            enrolled_by_user_id=membership.user_id, enrolled_at=now,
            next_step_at=now + timedelta(days=first_step.delay_days))
        db.add(enr)
        enrolled += 1
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_enroll_batch", target_type="sequence", target_id=sid,
              detail={"segment_id": payload.segment_id, "contact_ids_count": len(payload.contact_ids),
                      "enrolled": enrolled, "skipped": skipped, "total": enrolled + skipped})
    db.commit()
    return schemas.EnrollBatchResult(enrolled=enrolled, skipped=skipped, total=enrolled + skipped)


@router.get("/tenants/{tid}/crm/sequences/{sid}/enrollments",
            response_model=list[schemas.SequenceEnrollmentOut])
def list_enrollments(tid: str, sid: str,
                     status_filter: str | None = Query(None, alias="status"),
                     db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Liste les enrollments d'une séquence (P15), filtre status optionnel, tri desc sur enrolled_at."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    q = db.query(models.SequenceEnrollment).filter_by(sequence_id=sid)
    if status_filter:
        q = q.filter_by(status=status_filter)
    enrs = q.order_by(models.SequenceEnrollment.enrolled_at.desc()).all()
    return [_enr_to_out(e) for e in enrs]


@router.post("/tenants/{tid}/crm/sequences/{sid}/process",
             response_model=schemas.SequenceProcessResult)
def process_sequence_endpoint(tid: str, sid: str,
                              db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                              membership=Depends(require_permission("crm.manage"))):
    """Trigger manuel du scheduler sur une séquence (P15). Traite les enrollments dus
    (next_step_at <= now, status=active) immédiatement — utile pour admin/test (évite d'attendre
    l'intervalle du scheduler). Audit crm.sequence_process."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    stats = process_sequence(db, sid, tid)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.sequence_process", target_type="sequence", target_id=sid,
              detail={"stats": stats, "sequence_name": seq.name})
    db.commit()
    return schemas.SequenceProcessResult(sequence_id=sid, **stats)


@router.get("/tenants/{tid}/crm/sequences/{sid}/stats", response_model=schemas.SequenceStats)
def sequence_stats(tid: str, sid: str, db: Session = Depends(get_db),
                   _t: models.Tenant = Depends(get_tenant)):
    """Stats d'une séquence (P15) : counts par statut d'enrollment (active/completed/paused/failed)."""
    seq = db.query(models.EmailSequence).filter_by(tenant_id=tid, id=sid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable.")
    enrs = db.query(models.SequenceEnrollment).filter_by(sequence_id=sid).all()
    counts = {"active": 0, "completed": 0, "paused": 0, "failed": 0}
    for e in enrs:
        if e.status in counts:
            counts[e.status] += 1
    return schemas.SequenceStats(
        sequence_id=sid, total_enrollments=len(enrs),
        active=counts["active"], completed=counts["completed"],
        paused=counts["paused"], failed=counts["failed"])
