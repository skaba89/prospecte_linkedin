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


@router.post("/tenants/{tid}/crm/contacts/{ctid}/email", response_model=schemas.EmailSendResult)
def email_contact(tid: str, ctid: str, payload: schemas.EmailSendRequest,
                  db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                  membership=Depends(require_permission("crm.manage"))):
    """Envoie un e-mail templated à un contact CRM (P13). RGPD : skip si email en suppression
    list OU placeholder (@linkedin.sync). Crée une Notification (log durable) + envoie via SMTP
    (dry_run si smtp_enabled=false). Audit crm.email_contact. Retourne EmailSendResult."""
    contact = db.query(models.Contact).filter_by(tenant_id=tid, id=ctid).first()
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable.")
    email = (contact.email or "").strip().lower()

    # RGPD : désinscription respectée (SuppressionList).
    if email and db.query(models.SuppressionList).filter_by(tenant_id=tid, email=email).first():
        notif = models.Notification(
            tenant_id=tid, user_id=membership.user_id, type="email", channel="email",
            recipient_email=email, recipient_contact_id=ctid,
            subject=f"[SKIPPED-RGPD] {payload.template}",
            body_text="Désinscription RGPD — envoi bloqué.", template_id=payload.template,
            status="failed", error_detail="Email en suppression list (RGPD) — envoi bloqué.")
        db.add(notif)
        db.commit()
        db.refresh(notif)
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="crm.email_contact", target_type="contact", target_id=ctid,
                  detail={"template": payload.template, "status": "skipped_rgpd", "email": email})
        db.commit()
        return schemas.EmailSendResult(
            notification_id=notif.id, status="skipped", email_sent=False,
            detail="Email en suppression list (RGPD) — envoi bloqué.")

    # Placeholder (sync LinkedIn sans email réel) → skip (pas un destinataire valide).
    if not email or _is_placeholder_email(email):
        notif = models.Notification(
            tenant_id=tid, user_id=membership.user_id, type="email", channel="email",
            recipient_email=email, recipient_contact_id=ctid,
            subject=f"[SKIPPED-NO-EMAIL] {payload.template}",
            body_text="Pas d'email réel (placeholder sync LinkedIn) — envoi impossible.",
            template_id=payload.template, status="failed",
            error_detail="Email placeholder (@linkedin.sync) ou absent — pas un destinataire réel.")
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return schemas.EmailSendResult(
            notification_id=notif.id, status="skipped", email_sent=False,
            detail="Le contact n'a pas d'email réel (sync LinkedIn sans email exposé).")

    # Rendu du template (KeyError → 400 si template_id inconnu).
    company_name = ""
    if getattr(contact, "company_id", None):
        comp = db.query(models.Company).filter_by(tenant_id=tid, id=contact.company_id).first()
        company_name = comp.name if comp else ""
    try:
        subject, body_text, body_html = templates.render(
            payload.template,
            contact_name=f"{contact.first_name} {contact.last_name}".strip(),
            company=company_name, tenant_name=_t.name,
            sender_name=membership.name, topic=contact.title or "",
            custom_message=payload.custom_message)
    except KeyError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Template inconnu : {payload.template}. Disponibles : cold_outreach, follow_up, intro, re_engagement.") from None

    # Envoi SMTP (dry-run si smtp_enabled=false).
    ok, detail = send_email(email, subject, body_text, body_html, from_name=membership.name)
    notif_status = "sent" if (ok and settings.smtp_enabled) else ("dry_run" if ok else "failed")
    notif = models.Notification(
        tenant_id=tid, user_id=membership.user_id, type="email", channel="email",
        recipient_email=email, recipient_contact_id=ctid, subject=subject,
        body_text=body_text, body_html=body_html, template_id=payload.template,
        status=notif_status, sent_at=datetime.utcnow() if ok else None,
        error_detail=None if ok else detail)
    db.add(notif)
    db.commit()
    db.refresh(notif)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="crm.email_contact", target_type="contact", target_id=ctid,
              detail={"template": payload.template, "status": notif_status, "email": email})
    db.commit()
    return schemas.EmailSendResult(
        notification_id=notif.id, status=notif_status, email_sent=ok,
        detail=detail if not ok else
        f"E-mail {'envoyé' if settings.smtp_enabled else 'simulé (dry-run)'} → {email}.")


@router.get("/tenants/{tid}/notifications", response_model=list[schemas.NotificationOut])
def list_notifications(tid: str, response: Response, page: Page = Depends(page_dep),
                       ntype: str | None = Query(None),
                       status_filter: str | None = Query(None, alias="status"),
                       db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
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
def notifications_feed(tid: str, db: Session = Depends(get_db),
                       _t: models.Tenant = Depends(get_tenant)):
    """Feed in-app : notifications non lues (read_at IS NULL) pour la cloche dashboard.
    Limite 20 (plus récentes d'abord). Marquées lues via POST /notifications/{nid}/read."""
    return (db.query(models.Notification).filter_by(tenant_id=tid, read_at=None)
            .order_by(models.Notification.created_at.desc()).limit(20).all())


@router.post("/tenants/{tid}/notifications/{nid}/read", response_model=schemas.NotificationOut)
def mark_notification_read(tid: str, nid: str, db: Session = Depends(get_db),
                           _t: models.Tenant = Depends(get_tenant)):
    """Marque une notification comme lue (in-app feed). Idempotente (read_at déjà set → no-op)."""
    n = db.query(models.Notification).filter_by(tenant_id=tid, id=nid).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification introuvable.")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
        db.refresh(n)
    return n
