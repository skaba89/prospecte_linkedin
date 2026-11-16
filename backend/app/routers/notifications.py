"""Notifications & e-mails de prospection (P13). Pont P12 (contacts syncés) → outreach e-mail.

Routes (préfixe /api, toutes scopées par tenant via get_tenant) :
  GET  /tenants/{tid}/notifications/templates         → liste les templates (tout membre)
  POST /tenants/{tid}/crm/contacts/{ctid}/email       → envoie un e-mail templated au contact (RBAC crm.manage)
  GET  /tenants/{tid}/notifications                   → liste paginée (filtre type/status)
  GET  /tenants/{tid}/notifications/feed              → feed in-app non lus (cloche dashboard, tout membre)
  POST /tenants/{tid}/notifications/{nid}/read        → marque comme lu (idempotent)

RGPD : skip si l'email du contact est dans SuppressionList (désinscription respectée).
Skip aussi les emails placeholders (@linkedin.sync — pas un email réel, sync P12 sans email).
Audit : chaque envoi tracé (action crm.email_contact). SMTP via mailer.send_email (dry-run si
smtp_enabled=false → statut dry_run). Multi-tenant strict (filtrage tenant_id partout).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..mailer import send_email
from ..pagination import Page, page_dep, set_total
from ..services import templates
from ..services.webhooks import emit_event

router = APIRouter(prefix="/api", tags=["notifications"])
_log = logging.getLogger("revpulse.notifications")

# Emails synthétiques (sync LinkedIn P12 sans email réel) — pas un destinataire valide.
_PLACEHOLDER_DOMAINS = ("@linkedin.sync", "@local.revpulse.io")


def _is_placeholder_email(email: str) -> bool:
    e = (email or "").strip().lower()
    return any(e.endswith(d) for d in _PLACEHOLDER_DOMAINS)


@router.get("/tenants/{tid}/notifications/templates", response_model=list[schemas.TemplateInfo])
def list_templates_endpoint(tid: str, _t: models.Tenant = Depends(get_tenant)):
    """Liste les templates d'e-mail de prospection disponibles (P13)."""
    return [schemas.TemplateInfo(**t) for t in templates.list_templates()]


def send_email_to_contact(
    db: Session,
    tenant_id: str,
    contact: models.Contact,
    template_id: str,
    actor_user_id: str | None,
    actor_name: str,
    tenant_name: str,
    custom_message: str = "",
) -> schemas.EmailSendResult:
    """Envoie un e-mail templated à un contact CRM (helper P13/P15 — réutilisé par l'endpoint
    email_contact ET par le scheduler de séquences P15). Ne lève PAS d'HTTPException (le scheduler
    n'a pas de contexte HTTP) ; l'endpoint traduit les cas d'erreur (contact/template) en HTTP.

    RGPD : skip si email en suppression list OU placeholder (@linkedin.sync). Crée une Notification
    (log durable) + SMTP (dry-run si smtp_enabled=false). Audit crm.email_contact.
    Retourne EmailSendResult (status: sent|dry_run|failed|skipped)."""
    tid = tenant_id
    ctid = contact.id
    email = (contact.email or "").strip().lower()

    # RGPD : désinscription respectée (SuppressionList).
    if email and db.query(models.SuppressionList).filter_by(tenant_id=tid, email=email).first():
        notif = models.Notification(
            tenant_id=tid,
            user_id=actor_user_id,
            type="email",
            channel="email",
            recipient_email=email,
            recipient_contact_id=ctid,
            subject=f"[SKIPPED-RGPD] {template_id}",
            body_text="Désinscription RGPD — envoi bloqué.",
            template_id=template_id,
            status="failed",
            error_detail="Email en suppression list (RGPD) — envoi bloqué.",
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        log_audit(
            db,
            tenant_id=tid,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            action="crm.email_contact",
            target_type="contact",
            target_id=ctid,
            detail={"template": template_id, "status": "skipped_rgpd", "email": email},
        )
        db.commit()
        return schemas.EmailSendResult(
            notification_id=notif.id,
            status="skipped",
            email_sent=False,
            detail="Email en suppression list (RGPD) — envoi bloqué.",
        )

    # Placeholder (sync LinkedIn sans email réel) → skip (pas un destinataire valide).
    if not email or _is_placeholder_email(email):
        notif = models.Notification(
            tenant_id=tid,
            user_id=actor_user_id,
            type="email",
            channel="email",
            recipient_email=email,
            recipient_contact_id=ctid,
            subject=f"[SKIPPED-NO-EMAIL] {template_id}",
            body_text="Pas d'email réel (placeholder sync LinkedIn) — envoi impossible.",
            template_id=template_id,
            status="failed",
            error_detail="Email placeholder (@linkedin.sync) ou absent — pas un destinataire réel.",
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return schemas.EmailSendResult(
            notification_id=notif.id,
            status="skipped",
            email_sent=False,
            detail="Le contact n'a pas d'email réel (sync LinkedIn sans email exposé).",
        )

    # Rendu du template (KeyError → result failed ; l'endpoint traduira en 400).
    company_name = ""
    if getattr(contact, "company_id", None):
        comp = db.query(models.Company).filter_by(tenant_id=tid, id=contact.company_id).first()
        company_name = comp.name if comp else ""
    try:
        subject, body_text, body_html = templates.render(
            template_id,
            contact_name=f"{contact.first_name} {contact.last_name}".strip(),
            company=company_name,
            tenant_name=tenant_name,
            sender_name=actor_name,
            topic=contact.title or "",
            custom_message=custom_message,
        )
    except KeyError:
        return schemas.EmailSendResult(
            notification_id=None,
            status="failed",
            email_sent=False,
            detail=f"Template inconnu : {template_id}. Disponibles : cold_outreach, follow_up, intro, re_engagement.",
        )

    # Envoi SMTP (dry-run si smtp_enabled=false).
    ok, detail = send_email(email, subject, body_text, body_html, from_name=actor_name)
    notif_status = "sent" if (ok and settings.smtp_enabled) else ("dry_run" if ok else "failed")
    notif = models.Notification(
        tenant_id=tid,
        user_id=actor_user_id,
        type="email",
        channel="email",
        recipient_email=email,
        recipient_contact_id=ctid,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        template_id=template_id,
        status=notif_status,
        sent_at=datetime.utcnow() if ok else None,
        error_detail=None if ok else detail,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    # P17 : recompute le score du contact (l'engagement a changé — un e-mail de plus envoyé).
    from .crm import _score_contact

    contact = db.query(models.Contact).filter_by(id=ctid, tenant_id=tid).first()
    if contact:
        _score_contact(db, contact)
        db.commit()
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        action="crm.email_contact",
        target_type="contact",
        target_id=ctid,
        detail={"template": template_id, "status": notif_status, "email": email},
    )
    if ok:  # email réellement envoyé (sent ou dry_run) → emit webhook email.sent
        emit_event(
            db,
            tenant_id=tid,
            event="email.sent",
            payload={
                "notification_id": notif.id,
                "tenant_id": tid,
                "contact_id": ctid,
                "template": template_id,
                "status": notif_status,
                "email": email,
                "subject": subject,
            },
        )
    db.commit()
    return schemas.EmailSendResult(
        notification_id=notif.id,
        status=notif_status,
        email_sent=ok,
        detail=detail if not ok else f"E-mail {'envoyé' if settings.smtp_enabled else 'simulé (dry-run)'} → {email}.",
    )


@router.post("/tenants/{tid}/crm/contacts/{ctid}/email", response_model=schemas.EmailSendResult)
def email_contact(
    tid: str,
    ctid: str,
    payload: schemas.EmailSendRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Envoie un e-mail templated à un contact CRM (P13). Délègue à send_email_to_contact
    (helper réutilisé par le scheduler de séquences P15). Traduit les erreurs en HTTP
    (404 contact introuvable, 400 template inconnu)."""
    contact = db.query(models.Contact).filter_by(tenant_id=tid, id=ctid).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable.")
    result = send_email_to_contact(
        db, tid, contact, payload.template, membership.user_id, membership.name, _t.name, payload.custom_message
    )
    if result.status == "failed" and (result.detail or "").startswith("Template inconnu"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.detail) from None
    return result


@router.get("/tenants/{tid}/notifications", response_model=list[schemas.NotificationOut])
def list_notifications(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    ntype: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    """Liste paginée des notifications (filtre type/status). Tri desc sur created_at."""
    q = db.query(models.Notification).filter_by(tenant_id=tid)
    if ntype:
        q = q.filter(models.Notification.type == ntype)
    if status_filter:
        q = q.filter(models.Notification.status == status_filter)
    q = q.order_by(models.Notification.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.get("/tenants/{tid}/notifications/feed", response_model=list[schemas.NotificationOut])
def notifications_feed(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Feed in-app : notifications non lues (read_at IS NULL) pour la cloche dashboard.
    Limite 20 (plus récentes d'abord). Marquées lues via POST /notifications/{nid}/read."""
    return (
        db.query(models.Notification)
        .filter_by(tenant_id=tid, read_at=None)
        .order_by(models.Notification.created_at.desc())
        .limit(20)
        .all()
    )


@router.post("/tenants/{tid}/notifications/{nid}/read", response_model=schemas.NotificationOut)
def mark_notification_read(tid: str, nid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Marque une notification comme lue (in-app feed). Idempotente (read_at déjà set → no-op)."""
    n = db.query(models.Notification).filter_by(tenant_id=tid, id=nid).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification introuvable.")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
        db.refresh(n)
    return n
