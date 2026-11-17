"""P28 — Warmup inbox + Sales Intelligence service.

Warmup inbox (gradation du volume d'envoi email pour la réputation de l'expéditeur) + analytics
avancées (funnel conversion, cohortes d'enrollment, prévisions pipeline).

Fonctions :
  - create_warmup : crée une config warmup (status pending, current_day 0, daily_volume 0).
  - advance_warmup_day : incrémente current_day + recompute daily_volume (rampe linéaire) + status.
  - record_warmup_message : enregistre un message de warmup (sent/opened/replied) pour tracker la
    réputation pendant la rampe.
  - warmup_stats : agrège les métriques d'un warmup (daily_volume, open_rate, reply_rate, progress).
  - compute_funnel : contacts groupés par status (new|qualified|contacted|nurture|won|lost) → funnel
    de conversion.
  - compute_cohorts : enrollments groupés par semaine ISO (cohortes d'acquisition) + completion_rate.
  - compute_forecast : pipeline pondéré (sum value * probability / 100) + distribution par stage.

Sécurité (P28) :
  - Warmup v1 : enregistrement des messages simulé (pas d'envoi email réel — le scheduler P15
    process_due_enrollments reste responsable de l'envoi). record_warmup_message est appelé par un
    webhook OR un simulateur de test.
  - Forecast = projection statistique (pas une promesse de revenu — pondéré par probability).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models


def create_warmup(db: Session, tenant_id: str, sender_email: str, target_volume: int,
                  ramp_days: int, user_id: str | None) -> models.InboxWarmup:
    """Crée une config warmup (P28). status=pending, current_day=0, daily_volume=0 (démarrage via
    advance_warmup_day). target_volume 1-1000, ramp_days 7-90 (clamp)."""
    target_volume = max(10, min(1000, target_volume))
    ramp_days = max(7, min(90, ramp_days))
    warmup = models.InboxWarmup(
        tenant_id=tenant_id,
        sender_email=sender_email.lower().strip(),
        target_volume=target_volume,
        ramp_days=ramp_days,
        current_day=0,
        daily_volume=0,
        ramp_status="pending",
    )
    db.add(warmup)
    db.commit()
    db.refresh(warmup)
    return warmup


def advance_warmup_day(db: Session, warmup: models.InboxWarmup) -> models.InboxWarmup:
    """Avance le warmup d'un jour (P28). Incrémente current_day + recompute daily_volume (rampe
    linéaire : daily_volume = round(target_volume * current_day / ramp_days), min 1 une fois démarré).
    Status : ramping (current_day < ramp_days) | active (current_day >= ramp_days)."""
    if warmup.ramp_status == "completed":
        return warmup  # no-op si déjà completed
    if warmup.ramp_status == "pending":
        warmup.started_at = datetime.utcnow()
    warmup.current_day += 1
    # Rampe linéaire : au jour current_day, volume = target * current_day / ramp_days (min 1).
    warmup.daily_volume = max(1, round(warmup.target_volume * warmup.current_day / warmup.ramp_days))
    if warmup.current_day >= warmup.ramp_days:
        warmup.daily_volume = warmup.target_volume  # cap au target une fois la rampe finie
        warmup.ramp_status = "active"
        warmup.completed_at = datetime.utcnow()
    else:
        warmup.ramp_status = "ramping"
    db.commit()
    db.refresh(warmup)
    return warmup


def record_warmup_message(db: Session, warmup_id: str, opened: bool, replied: bool) -> models.WarmupMessage:
    """Enregistre un message de warmup (P28). opened/replied = simulateur OR webhook (pas d'envoi réel
    en v1 — anti-fake). Utilisé pour calculer open_rate/reply_rate via warmup_stats."""
    msg = models.WarmupMessage(
        warmup_id=warmup_id,
        sent_at=datetime.utcnow(),
        opened=opened,
        replied=replied,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def warmup_stats(db: Session, warmup: models.InboxWarmup) -> dict[str, Any]:
    """Agrège les métriques d'un warmup (P28). daily_volume + progress (current_day/ramp_days) +
    sent_count + open_rate + reply_rate (sur sent_count). sent=0 → taux=0."""
    msgs = db.query(models.WarmupMessage).filter_by(warmup_id=warmup.id).all()
    sent = len(msgs)
    opens = sum(1 for m in msgs if m.opened)
    replies = sum(1 for m in msgs if m.replied)
    return {
        "warmup_id": warmup.id,
        "sender_email": warmup.sender_email,
        "target_volume": warmup.target_volume,
        "ramp_days": warmup.ramp_days,
        "current_day": warmup.current_day,
        "daily_volume": warmup.daily_volume,
        "ramp_status": warmup.ramp_status,
        "progress_pct": round(warmup.current_day / warmup.ramp_days * 100, 1) if warmup.ramp_days else 0.0,
        "sent_count": sent,
        "open_count": opens,
        "reply_count": replies,
        "open_rate": round(opens / sent * 100, 1) if sent > 0 else 0.0,
        "reply_rate": round(replies / sent * 100, 1) if sent > 0 else 0.0,
    }


def compute_funnel(db: Session, tenant_id: str) -> dict[str, Any]:
    """Funnel de conversion des contacts (P28). Compte les contacts par status (new|qualified|
    contacted|nurture|won|lost) + taux de conversion (won / total). Retourne les stages dans l'ordre
    du funnel (new → qualified → contacted → nurture → won, lost à part)."""
    stages_order = ["new", "qualified", "contacted", "nurture", "won"]
    rows = (
        db.query(models.Contact.status, func.count(models.Contact.id))
        .filter_by(tenant_id=tenant_id)
        .group_by(models.Contact.status)
        .all()
    )
    counts = dict(rows)
    total = sum(counts.values())
    won = counts.get("won", 0)
    return {
        "stages": [{"status": s, "count": counts.get(s, 0)} for s in stages_order],
        "lost": counts.get("lost", 0),
        "total": total,
        "conversion_rate": round(won / total * 100, 1) if total > 0 else 0.0,
    }


def compute_cohorts(db: Session, tenant_id: str, weeks: int = 8) -> list[dict[str, Any]]:
    """Cohortes d'enrollment (P28). Group les SequenceEnrollment par semaine ISO (enrolled_at) +
    calcule le completion_rate (completed / total) par cohorte. Retourne les `weeks` dernières
    cohortes (plus récentes d'abord)."""
    # Récupère tous les enrollments du tenant (via JOIN sequence → tenant).
    enrollments = (
        db.query(models.SequenceEnrollment, models.EmailSequence.tenant_id.label("seq_tenant"))
        .join(models.EmailSequence, models.SequenceEnrollment.sequence_id == models.EmailSequence.id)
        .filter(models.EmailSequence.tenant_id == tenant_id)
        .all()
    )
    # Groupe par ISO year-week.
    cohorts: dict[str, dict[str, Any]] = {}
    for enr, _ in enrollments:
        iso = enr.enrolled_at.isocalendar()  # (year, week, weekday)
        key = f"{iso[0]}-W{iso[1]:02d}"
        if key not in cohorts:
            cohorts[key] = {"cohort": key, "total": 0, "completed": 0, "active": 0, "paused": 0, "failed": 0}
        cohorts[key]["total"] += 1
        if enr.status == "completed":
            cohorts[key]["completed"] += 1
        elif enr.status == "active":
            cohorts[key]["active"] += 1
        elif enr.status == "paused":
            cohorts[key]["paused"] += 1
        elif enr.status == "failed":
            cohorts[key]["failed"] += 1
    # Trie par cohort DESC + limite à `weeks` + calcule completion_rate.
    result = []
    for key in sorted(cohorts.keys(), reverse=True)[:weeks]:
        c = cohorts[key]
        c["completion_rate"] = round(c["completed"] / c["total"] * 100, 1) if c["total"] > 0 else 0.0
        result.append(c)
    return result


def compute_forecast(db: Session, tenant_id: str) -> dict[str, Any]:
    """Prévisions pipeline (P28). Pipeline pondéré = sum(value * probability / 100) par opportunity
    ouverte (stage != closed_lost). Distribution par stage + total pondéré + total brut. Le forecast
    est une projection statistique (pondérée par probability), pas une promesse de revenu."""
    opps = (
        db.query(models.Opportunity)
        .filter_by(tenant_id=tenant_id)
        .filter(~models.Opportunity.stage.in_(["closed_won", "closed_lost"]))
        .all()
    )
    by_stage: dict[str, dict[str, Any]] = {}
    weighted_total = 0
    raw_total = 0
    for o in opps:
        weighted = o.value * o.probability // 100
        weighted_total += weighted
        raw_total += o.value
        if o.stage not in by_stage:
            by_stage[o.stage] = {"stage": o.stage, "count": 0, "raw_value": 0, "weighted_value": 0}
        by_stage[o.stage]["count"] += 1
        by_stage[o.stage]["raw_value"] += o.value
        by_stage[o.stage]["weighted_value"] += weighted
    return {
        "stages": list(by_stage.values()),
        "total_opportunities": len(opps),
        "raw_pipeline": raw_total,
        "weighted_forecast": weighted_total,
        "currency": "EUR",  # default — v2: agrège par currency
    }
