"""P18 — Webhooks outbound : CRUD + test ping + deliveries + retry.

Endpoints (préfixe /api/tenants/{tid}/webhooks) :
  POST   /                — créer un webhook (admin workspace.manage).
  GET    /                — lister les webhooks du tenant.
  GET    /{wid}           — détail.
  PATCH  /{wid}           — update (url/events/active).
  DELETE /{wid}           — delete (soft : hard delete + CASCADE deliveries).
  POST   /{wid}/test      — test ping (crée + livre une delivery `webhook.test`).
  GET    /{wid}/deliveries— lister les deliveries (paginée).
  POST   /{wid}/deliveries/{did}/retry — retry une delivery failed/pending.

RBAC : workspace.manage (admin/owner) — les webhooks portent un secret + peuvent pousser des
données CRM à l'extérieur, donc réservés à l'admin. Audit log sur create/update/delete.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import webhooks as wh_service
from ..services.url_safety import validate_url

router = APIRouter(prefix="/api/tenants/{tid}/webhooks", tags=["webhooks"])


def _get_wh(db: Session, tid: str, wid: str) -> models.Webhook:
    wh = db.query(models.Webhook).filter_by(id=wid, tenant_id=tid).first()
    if not wh:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook introuvable")
    return wh


@router.get("", response_model=list[schemas.WebhookOut])
def list_webhooks(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Liste les webhooks du tenant (plus récent d'abord). Admin (workspace.manage)."""
    q = db.query(models.Webhook).filter_by(tenant_id=tid).order_by(models.Webhook.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("", response_model=schemas.WebhookOut, status_code=status.HTTP_201_CREATED)
def create_webhook(
    tid: str,
    body: schemas.WebhookCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Crée un webhook. events filtrés contre la whitelist SUPPORTED_EVENTS. secret auto-généré
    (32 hex) si non fourni. Audit log webhook.create."""
    try:
        events = wh_service._validate_events(body.events)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    # P24-A : SSRF — valider l'URL avant de persister (bloque private/loopback/metadata/file + DNS rebinding).
    ok, err, _ = validate_url(body.url)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"URL refusée (SSRF) : {err}")
    wh = models.Webhook(
        tenant_id=tid,
        url=body.url,
        events=events,
        active=body.active,
        secret="",  # P24-A : placeholder NOT NULL — le secret chiffré va dans secret_enc
        created_by=membership.user_id,
    )
    wh_service._store_secret(wh, body.secret or wh_service.gen_secret())  # P24-A : encrypt-at-rest
    db.add(wh)
    db.flush()
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="webhook.create",
        target_type="webhook",
        target_id=wh.id,
        detail={"url": wh.url, "events": events},
    )
    db.commit()
    db.refresh(wh)
    return wh


@router.get("/{wid}", response_model=schemas.WebhookOut)
def get_webhook(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Détail d'un webhook."""
    return _get_wh(db, tid, wid)


@router.patch("/{wid}", response_model=schemas.WebhookOut)
def update_webhook(
    tid: str,
    wid: str,
    body: schemas.WebhookUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Update partielle (url/events/active). events filtrés si fournis. Audit webhook.update."""
    wh = _get_wh(db, tid, wid)
    detail = {}
    if body.url is not None:
        # P24-A : SSRF — valider la nouvelle URL avant de persister.
        ok, err, _ = validate_url(body.url)
        if not ok:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"URL refusée (SSRF) : {err}")
        wh.url = body.url
        detail["url"] = body.url
    if body.events is not None:
        try:
            wh.events = wh_service._validate_events(body.events)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
        detail["events"] = wh.events
    if body.active is not None:
        wh.active = body.active
        detail["active"] = body.active
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="webhook.update",
        target_type="webhook",
        target_id=wh.id,
        detail=detail,
    )
    db.commit()
    db.refresh(wh)
    return wh


@router.delete("/{wid}", status_code=status.HTTP_200_OK)
def delete_webhook(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Delete un webhook (hard delete + CASCADE deliveries via ondelete). Audit webhook.delete."""
    wh = _get_wh(db, tid, wid)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="webhook.delete",
        target_type="webhook",
        target_id=wh.id,
    )
    db.delete(wh)
    db.commit()
    return {"deleted": wid}


@router.post("/{wid}/test", response_model=schemas.WebhookTestResult)
def test_webhook(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Test ping : crée + livre une delivery `webhook.test` (bypass whitelist — event interne).
    Retourne le statut de livraison (response_code/error). Utile pour valider l'URL + le secret."""
    wh = _get_wh(db, tid, wid)
    d = models.WebhookDelivery(
        webhook_id=wh.id,
        event="webhook.test",
        payload={
            "event": "webhook.test",
            "tenant_id": tid,
            "webhook_id": wh.id,
            "message": "RevPulse webhook test ping",
            "sent_by": membership.user_id,
        },
        status="pending",
        attempts=0,
    )
    db.add(d)
    db.flush()
    success = wh_service.deliver(db, d)
    db.commit()
    return schemas.WebhookTestResult(
        delivery_id=d.id,
        status=d.status,
        response_code=d.response_code,
        error=None if success else d.last_error,
    )


@router.post("/{wid}/rotate", response_model=schemas.WebhookRotateResult)
def rotate_secret(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Rotation du secret HMAC (P24-A) : génère un nouveau secret 32 hex, le chiffre dans secret_enc
    (Fernet), clear l'ancien. Retourne le nouveau secret EN CLAIR (une seule fois — l'admin le copie
    chez le consumer). Audit webhook.rotate. Le consumer doit reconfigurer sa vérif signature."""
    wh = _get_wh(db, tid, wid)
    new = wh_service.gen_secret()
    wh_service._store_secret(wh, new)  # P24-A : encrypt-at-rest + clear ancien
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="webhook.rotate",
        target_type="webhook",
        target_id=wh.id,
    )
    db.commit()
    return schemas.WebhookRotateResult(webhook_id=wh.id, secret=new)


@router.get("/{wid}/deliveries", response_model=list[schemas.WebhookDeliveryOut])
def list_deliveries(
    tid: str,
    wid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Liste les deliveries d'un webhook (plus récent d'abord). Paginée."""
    _get_wh(db, tid, wid)  # 404 si webhook n'existe pas
    q = db.query(models.WebhookDelivery).filter_by(webhook_id=wid).order_by(models.WebhookDelivery.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/{wid}/deliveries/{did}/retry", response_model=schemas.WebhookDeliveryOut)
def retry_delivery(
    tid: str,
    wid: str,
    did: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Retry une delivery : reset attempts à 0 + status pending + deliver. Audit webhook.retry."""
    _get_wh(db, tid, wid)
    d = db.query(models.WebhookDelivery).filter_by(id=did, webhook_id=wid).first()
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery introuvable")
    d.attempts = 0
    d.status = "pending"
    d.last_error = None
    db.flush()
    wh_service.deliver(db, d)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="webhook.retry",
        target_type="webhook_delivery",
        target_id=d.id,
        detail={"webhook_id": wid, "event": d.event, "result": d.status},
    )
    db.commit()
    db.refresh(d)
    return d
