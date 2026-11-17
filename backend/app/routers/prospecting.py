"""P27 — Prospection avancée router.

Endpoints (préfixe /api/tenants/{tid}/prospecting) :
  POST   /sequences/{sid}/steps                              — créer un step multicanal (crm.manage).
  GET    /sequences/{sid}/steps/{step_id}/variants           — lister les variantes A/B (content.read).
  POST   /sequences/{sid}/steps/{step_id}/variants           — créer une variante A/B (crm.manage).
  POST   /sequences/{sid}/steps/{step_id}/variants/{vid}/metrics — enregistrer une métrique (crm.manage).
  GET    /sequences/{sid}/steps/{step_id}/ab-results         — résultats A/B d'un step (content.read).
  GET    /contacts/{cid}/intent-signals                      — lister les signaux d'intention (content.read).
  POST   /contacts/{cid}/intent-signals                      — ajouter un signal + recompute score (crm.manage).
  GET    /contacts/intent-leaders                            — top contacts par intent_score (content.read).
  POST   /contacts/{cid}/enrich                              — enrichissement auto (simulé v1) (crm.manage).

RBAC (P27) :
  - content.read (Viewer+) — lecture (variants, intent signals, leaders, ab-results).
  - crm.manage (Editor+) — write (créer step, variante, métriques, signal, enrich).

Sécurité (P27) :
  - enrichment simulé en v1 (aucun appel API réel à un provider tiers — anti-fake).
  - intent_signals source=webhook traités comme data (untrusted — pas comme instructions).
  - Les taux A/B (open_rate, etc.) sont calculés sur sent_count (dénominateur) — sent=0 → taux=0.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..services import prospecting as pros

router = APIRouter(prefix="/api/tenants/{tid}/prospecting", tags=["prospecting"])


# ─── Multichannel steps ──────────────────────────────────────────────────────


@router.post("/sequences/{sid}/steps", response_model=schemas.SequenceStepOut,
             status_code=status.HTTP_201_CREATED)
def create_multichannel_step(
    tid: str,
    sid: str,
    body: schemas.MultichannelStepRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Crée un step multicanal (email|linkedin|call|sms|manual). Pour email → template_id requis.
    Pour autres channels → channel_config requis (linkedin_action, call_script, manual_task, sms_text)."""
    # Vérifie que la séquence appartient au tenant.
    seq = db.query(models.EmailSequence).filter_by(id=sid, tenant_id=tid).first()
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Séquence introuvable")
    try:
        step = pros.create_multichannel_step(
            db, sequence_id=sid, step_order=body.step_order, channel=body.channel,
            delay_days=body.delay_days, template_id=body.template_id,
            channel_config=body.channel_config, user_id=membership.user_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="prospecting.step.create", target_type="sequence_step", target_id=step.id,
              detail={"sequence_id": sid, "channel": body.channel, "step_order": body.step_order})
    db.commit()
    db.refresh(step)
    return step  # retourne le step créé (avec id) — permet aux variants de référencer le step_id


# ─── A/B testing (variants) ──────────────────────────────────────────────────


@router.get("/sequences/{sid}/steps/{step_id}/variants",
            response_model=list[schemas.SequenceStepVariantOut])
def list_variants(
    tid: str, sid: str, step_id: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Liste les variantes A/B d'un step."""
    step = db.query(models.SequenceStep).filter_by(id=step_id, sequence_id=sid).first()
    if not step:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step introuvable")
    return db.query(models.SequenceStepVariant).filter_by(step_id=step_id).all()


@router.post("/sequences/{sid}/steps/{step_id}/variants",
             response_model=schemas.SequenceStepVariantOut, status_code=status.HTTP_201_CREATED)
def create_variant(
    tid: str, sid: str, step_id: str,
    body: schemas.SequenceStepVariantRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Crée une variante A/B (variant='A'|'B'). subject_override + body_override = contenu alternatif."""
    step = db.query(models.SequenceStep).filter_by(id=step_id, sequence_id=sid).first()
    if not step:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step introuvable")
    if body.variant not in ("A", "B"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "variant doit être 'A' ou 'B'")
    # Vérifie qu'une variante avec le même label n'existe pas déjà.
    existing = db.query(models.SequenceStepVariant).filter_by(step_id=step_id, variant=body.variant).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Variante '{body.variant}' existe déjà pour ce step")
    var = models.SequenceStepVariant(
        step_id=step_id,
        variant=body.variant,
        label=body.label,
        subject_override=body.subject_override,
        body_override=body.body_override,
    )
    db.add(var)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="prospecting.variant.create", target_type="sequence_step_variant", target_id=var.id,
              detail={"step_id": step_id, "variant": body.variant})
    db.commit()
    db.refresh(var)
    return var


@router.post("/sequences/{sid}/steps/{step_id}/variants/{vid}/metrics",
             response_model=schemas.SequenceStepVariantOut)
def record_metrics(
    tid: str, sid: str, step_id: str, vid: str,
    body: schemas.VariantMetricsRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Enregistre une métrique sur une variante (event=sent|open|click|reply, count incrément)."""
    var = db.query(models.SequenceStepVariant).filter_by(id=vid, step_id=step_id).first()
    if not var:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variante introuvable")
    try:
        var = pros.record_variant_metrics(db, var, body.event, body.count)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="prospecting.variant.metrics", target_type="sequence_step_variant", target_id=var.id,
              detail={"event": body.event, "count": body.count})
    db.commit()
    db.refresh(var)
    return var


@router.get("/sequences/{sid}/steps/{step_id}/ab-results",
            response_model=list[schemas.ABTestResult])
def ab_results(
    tid: str, sid: str, step_id: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Résultats A/B d'un step : taux d'ouverture/clic/réponse par variante (sur sent_count)."""
    step = db.query(models.SequenceStep).filter_by(id=step_id, sequence_id=sid).first()
    if not step:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step introuvable")
    return pros.ab_test_results(db, step_id)


# ─── Intent signals + leaders ────────────────────────────────────────────────


@router.get("/contacts/{cid}/intent-signals", response_model=list[schemas.IntentSignalOut])
def list_intent_signals(
    tid: str, cid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Liste les signaux d'intention d'un contact (plus récents d'abord)."""
    contact = db.query(models.Contact).filter_by(id=cid, tenant_id=tid).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    return (
        db.query(models.IntentSignal)
        .filter_by(contact_id=cid)
        .order_by(models.IntentSignal.detected_at.desc())
        .all()
    )


@router.post("/contacts/{cid}/intent-signals", response_model=schemas.IntentSignalOut,
             status_code=status.HTTP_201_CREATED)
def add_intent_signal(
    tid: str, cid: str,
    body: schemas.IntentSignalRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Ajoute un signal d'intention + recompute Contact.intent_score (somme des signaux 90j, cap 100)."""
    contact = db.query(models.Contact).filter_by(id=cid, tenant_id=tid).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    try:
        signal = pros.add_intent_signal(
            db, tenant_id=tid, contact_id=cid, signal_type=body.signal_type,
            description=body.description, score=body.score, source=body.source,
            user_id=membership.user_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="prospecting.intent.add", target_type="intent_signal", target_id=signal.id,
              detail={"contact_id": cid, "signal_type": body.signal_type, "score": body.score})
    db.commit()
    db.refresh(signal)
    return signal


@router.get("/contacts/intent-leaders", response_model=list[schemas.IntentLeader])
def intent_leaders(
    tid: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Top contacts par intent_score (leaderboard). Inclut le company name (LEFT JOIN companies) +
    signal_count (nombre de signaux d'intention). Trié par intent_score DESC."""
    # Sous-requête : compte les signaux par contact.
    signal_count_sq = (
        db.query(models.IntentSignal.contact_id,
                 func.count(models.IntentSignal.id).label("signal_count"))
        .group_by(models.IntentSignal.contact_id)
        .subquery()
    )
    rows = (
        db.query(
            models.Contact,
            models.Company.name.label("company_name"),
            func.coalesce(signal_count_sq.c.signal_count, 0).label("signal_count"),
        )
        .outerjoin(models.Company, models.Contact.company_id == models.Company.id)
        .outerjoin(signal_count_sq, models.Contact.id == signal_count_sq.c.contact_id)
        .filter(models.Contact.tenant_id == tid)
        .filter(models.Contact.intent_score > 0)
        .order_by(models.Contact.intent_score.desc())
        .limit(limit)
        .all()
    )
    return [
        schemas.IntentLeader(
            id=c.id, first_name=c.first_name, last_name=c.last_name, email=c.email,
            company=company_name, title=c.title or None,
            intent_score=c.intent_score, signal_count=int(sig_count),
        )
        for c, company_name, sig_count in rows
    ]


# ─── Enrichment (simulated v1) ───────────────────────────────────────────────


@router.post("/contacts/{cid}/enrich", response_model=schemas.EnrichmentResult)
def enrich_contact(
    tid: str, cid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Enrichissement automatique (P27 v1 — SIMULÉ). Marque enriched_at + retourne des data génériques.
    Aucun appel API réel en v1 — anti-fake. La v2 branchera un provider (Apollo/Clearbit/Dropcontact)."""
    contact = db.query(models.Contact).filter_by(id=cid, tenant_id=tid).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    result = pros.trigger_enrichment(db, contact)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="prospecting.enrich", target_type="contact", target_id=cid,
              detail={"simulated": True, "enriched_at": result["enriched_at"]})
    db.commit()
    return result
