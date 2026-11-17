"""P20 — Webhooks inbound : endpoint public de réception + CRUD admin.

Endpoints PUBLIC (préfixe /api/inbound) :
  POST /webhook/{token}  — réception d'un payload → crée/met à jour un Contact. No auth (le token
                             dans l'URL est l'identifiant). 404 si token inconnu, 410 si inactif,
                             422 si payload vide (aucun champ mappable).

Endpoints ADMIN (préfixe /api/tenants/{tid}/inbound-webhooks) :
  POST   /                — créer un inbound webhook (admin workspace.manage). token auto-généré.
  GET    /                — lister.
  GET    /{wid}           — détail.
  PATCH  /{wid}           — update (source_name/field_map/active — token non modifiable).
  DELETE /{wid}           — delete (hard delete + CASCADE tenant).

RBAC admin : workspace.manage (admin/owner) — les inbound webhooks créent des Contacts dans le
tenant, donc réservés à l'admin. Audit log sur create/update/delete. Le POST public logge aussi
(inbound.contact_created/updated, actor = "Inbound webhook: {source}").
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import inbound_webhooks as ibw_service

# Router public : pas de get_tenant, pas de require_permission — le token dans l'URL authentifie.
router = APIRouter(prefix="/api/inbound", tags=["inbound-webhooks"])

# Router admin : CRUD scopé par tenant, RBAC workspace.manage.
router_admin = APIRouter(prefix="/api/tenants/{tid}/inbound-webhooks", tags=["inbound-webhooks"])


def _get_ibw(db: Session, tid: str, wid: str) -> models.InboundWebhook:
    wh = db.query(models.InboundWebhook).filter_by(id=wid, tenant_id=tid).first()
    if not wh:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound webhook introuvable")
    return wh


# ─── Endpoint public de réception ──────────────────────────────────────────────


@router.post("/webhook/{token}", response_model=schemas.InboundResult)
def receive_inbound(
    token: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    """Réception d'un payload inbound : crée ou met à jour un Contact dans le tenant propriétaire
    du token. No auth — le token (32 hex) dans l'URL identifie le tenant + la config. 404 si token
    inconnu, 410 si inactif (suspendu), 422 si payload vide (aucun champ mappable après field_map).
    Retourne l'action (created/updated), le contact_id, la clé de dédup (email/linkedin_url/none)."""
    wh = db.query(models.InboundWebhook).filter_by(token=token).first()
    if not wh:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token inbound inconnu")
    if not wh.active:
        raise HTTPException(status.HTTP_410_GONE, "Inbound webhook suspendu (inactif)")
    # Valide qu'au moins un champ mappable est présent (anti-payload-vide)
    fields = ibw_service._apply_field_map(payload, wh.field_map)
    if not fields:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Payload vide ou aucun champ mappable (attendu : first_name/last_name/email/phone/title/linkedin_url)",
        )
    result = ibw_service.process_inbound(db, webhook=wh, payload=payload)
    db.commit()
    return result


# ─── CRUD admin ────────────────────────────────────────────────────────────────


@router_admin.get("", response_model=list[schemas.InboundWebhookOut])
def list_inbound_webhooks(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Liste les inbound webhooks du tenant (plus récent d'abord). Admin (workspace.manage)."""
    q = (
        db.query(models.InboundWebhook)
        .filter_by(tenant_id=tid)
        .order_by(models.InboundWebhook.created_at.desc())
    )
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router_admin.post("", response_model=schemas.InboundWebhookOut, status_code=status.HTTP_201_CREATED)
def create_inbound_webhook(
    tid: str,
    body: schemas.InboundWebhookCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Crée un inbound webhook. token auto-généré (32 hex) — retourné en Out (l'admin le copie
    dans la config du système source). field_map validé contre WRITABLE_FIELDS (anti-injection).
    Audit log inbound.create."""
    # Valide le field_map custom : les valeurs doivent être dans WRITABLE_FIELDS (anti-injection)
    if body.field_map:
        invalid = [v for v in body.field_map.values() if v not in ibw_service.WRITABLE_FIELDS]
        if invalid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"field_map cible des champs non-autorisés: {invalid} (autorisés: {sorted(ibw_service.WRITABLE_FIELDS)})",
            )
    wh = models.InboundWebhook(
        tenant_id=tid,
        token=ibw_service.gen_token(),
        source_name=body.source_name,
        field_map=body.field_map,
        active=body.active,
        created_by=membership.user_id,
    )
    db.add(wh)
    db.flush()
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="inbound.create",
        target_type="inbound_webhook",
        target_id=wh.id,
        detail={"source_name": wh.source_name, "token": wh.token},
    )
    db.commit()
    db.refresh(wh)
    return wh


@router_admin.get("/{wid}", response_model=schemas.InboundWebhookOut)
def get_inbound_webhook(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Détail d'un inbound webhook."""
    return _get_ibw(db, tid, wid)


@router_admin.patch("/{wid}", response_model=schemas.InboundWebhookOut)
def update_inbound_webhook(
    tid: str,
    wid: str,
    body: schemas.InboundWebhookUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Update partielle (source_name/field_map/active — token non modifiable, rotation = delete +
    recreate). field_map validé si fourni. Audit inbound.update."""
    wh = _get_ibw(db, tid, wid)
    detail = {}
    if body.source_name is not None:
        wh.source_name = body.source_name
        detail["source_name"] = body.source_name
    if body.field_map is not None:
        invalid = [v for v in body.field_map.values() if v not in ibw_service.WRITABLE_FIELDS]
        if invalid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"field_map cible des champs non-autorisés: {invalid} (autorisés: {sorted(ibw_service.WRITABLE_FIELDS)})",
            )
        wh.field_map = body.field_map
        detail["field_map"] = body.field_map
    if body.active is not None:
        wh.active = body.active
        detail["active"] = body.active
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="inbound.update",
        target_type="inbound_webhook",
        target_id=wh.id,
        detail=detail,
    )
    db.commit()
    db.refresh(wh)
    return wh


@router_admin.delete("/{wid}", status_code=status.HTTP_200_OK)
def delete_inbound_webhook(
    tid: str,
    wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Delete un inbound webhook (hard delete). Audit inbound.delete. Le token devient invalide
    (les POST suivants sur /api/inbound/webhook/{token} → 404)."""
    wh = _get_ibw(db, tid, wid)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="inbound.delete",
        target_type="inbound_webhook",
        target_id=wh.id,
    )
    db.delete(wh)
    db.commit()
    return {"deleted": wid}
