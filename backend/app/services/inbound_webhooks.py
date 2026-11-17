"""P20 — Webhooks inbound : process_inbound (parse payload → create/update Contact) + gen_token.

process_inbound est appelé par le router POST /api/inbound/webhook/{token}. Mappe le payload via
field_map (default: identity sur first_name/last_name/email/phone/title/linkedin_url), dédup par
email (normalisé lowercase+strip) OU linkedin_url au sein du tenant, crée ou met à jour le Contact
(source="inbound"). Pour les nouveaux : emit contact.created (→ déclenche les webhooks outbound
P18 : la chaîne in→out est complète — un formulaire pousse → RevPulse crée le contact → notifie
Slack/CRM externe). Audit log inbound.contact_created/updated (actor = "Inbound webhook: {source}").

Sécurité : WRITABLE_FIELDS whitelist (anti-injection — un field_map custom ne peut pas écraser
tenant_id/id/score/status/source). gen_token = 32 hex (128 bits), public dans l'URL (contrairement
au secret HMAC outbound jamais exposé).
"""

import hashlib
import json
import logging
import secrets as _secrets
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..audit import log_audit
from . import webhooks as wh_outbound  # emit_event pour la chaîne in→out

log = logging.getLogger("revpulse.inbound_webhooks")

# Default field map : payload_key → contact_field. Si le payload source utilise des clés
# standards (first_name, last_name, email, ...), l'identity map suffit. field_map custom permet
# de mapper un payload non-standard (ex. {"prénom": "first_name", "courriel": "email"}).
DEFAULT_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "title": "title",
    "linkedin_url": "linkedin_url",
}

# Champs Contact qu'on autorise à être mappés (anti-injection : un field_map custom ne peut pas
# écraser des champs sensibles comme tenant_id/id/score/status/source/owner_id).
WRITABLE_FIELDS = {"first_name", "last_name", "email", "phone", "title", "linkedin_url"}


def gen_token() -> str:
    """Génère un token inbound 32 chars hex (128 bits) — public, dans l'URL."""
    return _secrets.token_hex(16)


def _normalize_email(email: str) -> str:
    """Normalise email : lowercase + strip (aligné Contact.email indexé)."""
    return (email or "").strip().lower()


def _apply_field_map(payload: dict, field_map: dict | None) -> dict:
    """Mappe le payload via field_map (default si None) → dict {contact_field: value}.
    Filtre les champs non-writables (anti-injection via field_map custom). Ignore les valeurs
    vides (un champ absent ou "" du payload n'écrase pas un champ existant)."""
    fmap = field_map or DEFAULT_FIELD_MAP
    out = {}
    for payload_key, contact_field in fmap.items():
        if contact_field not in WRITABLE_FIELDS:
            continue  # anti-injection : ignore les champs sensibles
        val = payload.get(payload_key)
        if val is not None and str(val).strip():
            out[contact_field] = str(val).strip()
    return out


def process_inbound(db: Session, *, webhook: models.InboundWebhook, payload: dict,
                    event_id: str | None = None) -> dict:
    """Parse le payload → crée ou met à jour un Contact. Retourne {action, contact_id, matched_by}.
    Dédup par email (normalisé) OU linkedin_url au sein du tenant. source="inbound". Pour les
    nouveaux : emit contact.created (→ outbound webhooks P18). Audit log. Ne commit pas (le router
    commit). Best-effort sur le mapping (payload vide → 422 côté router avant cet appel).
    P24-A : idempotence — si event_id fourni (header X-Webhook-Id/Webhook-Id ou payload event_id),
    insert dans inbound_events (UniqueConstraint tenant/webhook/event_id) via SAVEPOINT ; si doublon
    → retourne {action: 'duplicate', ...} sans retraiter (anti double-create sur retry/replay)."""
    # P24-A : idempotence — dedup par event_id (anti double-traitement sur retry/replay sender).
    if event_id:
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        sp = db.begin_nested()  # SAVEPOINT — rollback isolé si doublon (préserve la txn externe)
        try:
            db.add(models.InboundEvent(
                tenant_id=webhook.tenant_id,
                webhook_id=webhook.id,
                event_id=event_id,
                payload_hash=payload_hash,
            ))
            db.flush()  # déclenche IntegrityError si (tenant, webhook, event_id) existe déjà
            sp.commit()
        except IntegrityError:
            sp.rollback()
            return {
                "action": "duplicate",
                "contact_id": None,
                "matched_by": "event_id",
                "event_id": event_id,
            }
    fields = _apply_field_map(payload, webhook.field_map)
    email = _normalize_email(fields.get("email", ""))
    linkedin_url = fields.get("linkedin_url", "")

    # Dédup : email d'abord (plus fiable), puis linkedin_url
    existing = None
    matched_by = "none"
    if email:
        existing = (
            db.query(models.Contact)
            .filter(
                models.Contact.tenant_id == webhook.tenant_id,
                models.Contact.email == email,
            )
            .first()
        )
        if existing:
            matched_by = "email"
    if not existing and linkedin_url:
        existing = (
            db.query(models.Contact)
            .filter(
                models.Contact.tenant_id == webhook.tenant_id,
                models.Contact.linkedin_url == linkedin_url,
            )
            .first()
        )
        if existing:
            matched_by = "linkedin_url"

    if existing:
        # Update : ne remplace que les champs non-vides du payload (ne vide pas un champ existant)
        for k, v in fields.items():
            if v:
                setattr(existing, k, v)
        existing.updated_at = datetime.utcnow()
        db.flush()
        action = "updated"
        contact_id = existing.id
        audit_action = "inbound.contact_updated"
    else:
        # Create : nouveau contact source=inbound, status=new, score=0
        contact = models.Contact(
            tenant_id=webhook.tenant_id,
            first_name=fields.get("first_name", ""),
            last_name=fields.get("last_name", ""),
            email=email or fields.get("email", ""),
            phone=fields.get("phone", ""),
            title=fields.get("title", ""),
            linkedin_url=linkedin_url,
            source="inbound",
            status="new",
            score=0,
        )
        db.add(contact)
        db.flush()
        action = "created"
        contact_id = contact.id
        audit_action = "inbound.contact_created"
        # Chaîne in→out : emit contact.created déclenche les webhooks outbound P18
        wh_outbound.emit_event(
            db,
            tenant_id=webhook.tenant_id,
            event="contact.created",
            payload={
                "event": "contact.created",
                "tenant_id": webhook.tenant_id,
                "contact_id": contact.id,
                "source": "inbound",
                "source_name": webhook.source_name,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
            },
        )

    log_audit(
        db,
        tenant_id=webhook.tenant_id,
        actor_user_id=webhook.created_by,
        actor_name=f"Inbound webhook: {webhook.source_name}",
        action=audit_action,
        target_type="contact",
        target_id=contact_id,
        detail={
            "inbound_webhook_id": webhook.id,
            "source_name": webhook.source_name,
            "matched_by": matched_by,
        },
    )
    return {"action": action, "contact_id": contact_id, "matched_by": matched_by}
