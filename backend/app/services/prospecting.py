"""P27 — Prospection avancée service.

Multicanal sequences (email|linkedin|call|sms|manual) + A/B testing (variantes A/B avec métriques) +
détection d'intention (IntentSignal → Contact.intent_score).

Fonctions :
  - create_multichannel_step : crée un step avec channel + channel_config (linkedin_action, call_script,
    manual_task, sms_text). Pour email channel → template_id requis (P15 compat). Pour autres channels
    → template_id NULL + channel_config requis.
  - record_variant_metrics : enregistre les métriques d'une variante (sent/open/click/reply incrément).
  - add_intent_signal : ajoute un signal d'intention + recompute Contact.intent_score (somme des scores
    des signals des 90 derniers jours — fenêtre glissante pour éviter l'inflation).
  - compute_intent_score : somme des IntentSignal.score récents (90 jours) pour un contact.
  - trigger_enrichment : enrichment automatique (simulé en v1 — marque enriched_at + retourne des data
    génériques. La v2 branchera un vrai provider enrichment — Apollo, Clearbit, Dropcontact).

Sécurité (P27) :
  - Aucune action externe en v1 (enrichment simulé — pas d'appel API réel à un provider tiers).
  - Les intent_signals source=webhook sont traités comme data (pas comme instructions — untrusted).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models

# Fenêtre glissante pour le calcul d'intent_score (signaux des 90 derniers jours).
_INTENT_WINDOW_DAYS = 90


def create_multichannel_step(db: Session, sequence_id: str, step_order: int, channel: str,
                             delay_days: int, template_id: str | None, channel_config: dict | None,
                             user_id: str | None) -> models.SequenceStep:
    """Crée un step multicanal (P27). channel = email|linkedin|call|sms|manual.
    - email : template_id requis (P15 compat), channel_config optionnel.
    - linkedin : template_id NULL, channel_config.linkedin_action requis (connection_request|message|
      profile_view|like_comment).
    - call : template_id NULL, channel_config.call_script recommandé.
    - sms : template_id NULL, channel_config.sms_text requis.
    - manual : template_id NULL, channel_config.manual_task requis.
    """
    valid_channels = ("email", "linkedin", "call", "sms", "manual")
    if channel not in valid_channels:
        raise ValueError(f"channel invalide : {channel} (valides : {', '.join(valid_channels)})")
    if channel == "email" and not template_id:
        raise ValueError("channel=email → template_id requis")
    if channel != "email" and not channel_config:
        raise ValueError(f"channel={channel} → channel_config requis")
    if channel == "linkedin":
        action = (channel_config or {}).get("linkedin_action")
        if action not in ("connection_request", "message", "profile_view", "like_comment"):
            raise ValueError("linkedin → channel_config.linkedin_action requis (connection_request|message|profile_view|like_comment)")
    if channel == "sms" and not (channel_config or {}).get("sms_text"):
        raise ValueError("sms → channel_config.sms_text requis")
    if channel == "manual" and not (channel_config or {}).get("manual_task"):
        raise ValueError("manual → channel_config.manual_task requis")
    step = models.SequenceStep(
        sequence_id=sequence_id,
        step_order=step_order,
        template_id=template_id,
        delay_days=delay_days,
        channel=channel,
        channel_config=channel_config,
    )
    db.add(step)
    db.flush()
    db.commit()
    db.refresh(step)
    return step


def record_variant_metrics(db: Session, variant: models.SequenceStepVariant,
                           event: str, count: int = 1) -> models.SequenceStepVariant:
    """Enregistre une métrique sur une variante (P27 A/B testing). event = sent|open|click|reply.
    Incrémente le count correspondant. sent_count =基数 (dénominateur pour les taux)."""
    if event not in ("sent", "open", "click", "reply"):
        raise ValueError(f"event invalide : {event} (valides : sent, open, click, reply)")
    if event == "sent":
        variant.sent_count += count
    elif event == "open":
        variant.open_count += count
    elif event == "click":
        variant.click_count += count
    elif event == "reply":
        variant.reply_count += count
    db.commit()
    db.refresh(variant)
    return variant


def compute_intent_score(db: Session, contact_id: str) -> int:
    """Recompute Contact.intent_score = somme des IntentSignal.score des 90 derniers jours (fenêtre
    glissante — évite l'inflation par accumulation de vieux signaux). Retourne le nouveau score."""
    cutoff = datetime.utcnow() - timedelta(days=_INTENT_WINDOW_DAYS)
    signals = (
        db.query(models.IntentSignal)
        .filter_by(contact_id=contact_id)
        .filter(models.IntentSignal.detected_at >= cutoff)
        .all()
    )
    score = min(100, sum(s.score for s in signals))  # cap à 100
    contact = db.query(models.Contact).filter_by(id=contact_id).first()
    if contact:
        contact.intent_score = score
        db.commit()
    return score


def add_intent_signal(db: Session, tenant_id: str, contact_id: str, signal_type: str,
                      description: str | None, score: int, source: str, user_id: str | None) -> models.IntentSignal:
    """Ajoute un IntentSignal + recompute Contact.intent_score. signal_type = job_change|funding|
    hire_spike|news|engagement|website_visit. score 1-100 (clamp). source = manual|webhook|enrichment."""
    valid_types = ("job_change", "funding", "hire_spike", "news", "engagement", "website_visit")
    if signal_type not in valid_types:
        raise ValueError(f"signal_type invalide : {signal_type} (valides : {', '.join(valid_types)})")
    if source not in ("manual", "webhook", "enrichment"):
        raise ValueError(f"source invalide : {source} (valides : manual, webhook, enrichment)")
    score = max(1, min(100, score))  # clamp 1-100
    signal = models.IntentSignal(
        tenant_id=tenant_id,
        contact_id=contact_id,
        signal_type=signal_type,
        description=description[:1000] if description else None,
        score=score,
        source=source,
        detected_at=datetime.utcnow(),
        created_by=user_id,
    )
    db.add(signal)
    db.flush()
    # Recompute intent_score (somme des signaux récents).
    compute_intent_score(db, contact_id)
    db.commit()
    db.refresh(signal)
    return signal


def trigger_enrichment(db: Session, contact: models.Contact) -> dict[str, Any]:
    """Enrichissement automatique (P27 v1 — SIMULÉ). Marque enriched_at + retourne des data génériques
    (title/company/phone enrichment simulé). La v2 branchera un vrai provider (Apollo, Clearbit,
    Dropcontact) via une EnrichmentProvider config + API key chiffrée (pattern AIProviderConfig P24-B).
    Aucun appel API réel en v1 — anti-fake (jamais présenté comme réel)."""
    contact.enriched_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    # Data simulée (v1) — clairement marquée simulated=True.
    return {
        "contact_id": contact.id,
        "enriched_at": contact.enriched_at.isoformat() if contact.enriched_at else None,
        "simulated": True,
        "data": {
            "title_confidence": "low",
            "company_employee_count": None,
            "company_industry": None,
            "phone_verified": False,
            "note": "Enrichment v1 simulé — connecter un provider (Apollo/Clearbit) en v2 pour des data réelles.",
        },
    }


def ab_test_results(db: Session, step_id: str) -> list[dict[str, Any]]:
    """Agrège les métriques A/B d'un step (P27). Retourne une liste de variants avec leurs taux
    (open_rate, click_rate, reply_rate calculés sur sent_count). Si sent_count=0 → taux=0."""
    variants = db.query(models.SequenceStepVariant).filter_by(step_id=step_id).all()
    results = []
    for v in variants:
        sent = max(v.sent_count, 1)  # évite div by zero (si 0 → taux 0 car counts=0)
        results.append({
            "variant_id": v.id,
            "variant": v.variant,
            "label": v.label,
            "sent_count": v.sent_count,
            "open_count": v.open_count,
            "click_count": v.click_count,
            "reply_count": v.reply_count,
            "open_rate": round(v.open_count / sent * 100, 1) if v.sent_count > 0 else 0.0,
            "click_rate": round(v.click_count / sent * 100, 1) if v.sent_count > 0 else 0.0,
            "reply_rate": round(v.reply_count / sent * 100, 1) if v.sent_count > 0 else 0.0,
        })
    return results
