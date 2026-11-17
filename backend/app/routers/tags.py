"""P22 — Tags & catégorisation de contacts.

Endpoints (préfixe /api/tenants/{tid}/tags) :
  GET  /                  — liste les tags du tenant (avec contact_count = nb contacts par tag).
  POST /                  — crée un tag (name + color). RBAC crm.manage. UniqueConstraint(tenant_id, name).
  PATCH /{tag_id}         — renomme/recolore. RBAC crm.manage.
  DELETE /{tag_id}        — supprime le tag (cascade-supprime les associations ContactTag). RBAC crm.manage.
  GET  /contacts/{cid}    — liste les tags d'un contact.
  POST /contacts/{cid}    — assigne des tag_ids à un contact (idempotent : skip les doublons via PK composite).
                             RBAC crm.manage. Valide que les tags appartiennent au tenant.
  DELETE /contacts/{cid}/{tag_id} — détache un tag d'un contact. RBAC crm.manage.

Complète les Segments (P14) : un Segment est un filtre dynamique par requête, un Tag est un label
explicite apposé manuellement. Mutuellement compatibles (un contact peut avoir des tags ET matcher
des segments). RBAC : lecture ouverte à tout membre actif, mutations crm.manage (Owner/Admin/Editor).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/tags", tags=["tags"])


@router.get("", response_model=list[schemas.TagOut])
def list_tags(
    tid: str,
    page: Page = Depends(page_dep),
    response: Response = None,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    """Liste les tags du tenant (avec contact_count = nb contacts par tag), triés par nom.
    Paginée. Lecture ouverte à tout membre actif."""
    # Comptage batch : 1 query group_by tag_id → dict {tag_id: count}
    counts = dict(
        db.query(models.ContactTag.tag_id, func.count(models.ContactTag.contact_id))
        .join(models.Tag, models.Tag.id == models.ContactTag.tag_id)
        .filter(models.Tag.tenant_id == tid)
        .group_by(models.ContactTag.tag_id)
        .all()
    )
    q = db.query(models.Tag).filter_by(tenant_id=tid).order_by(models.Tag.name)
    set_total(response, q.count())
    tags = q.offset(page.skip).limit(page.limit).all()
    for t in tags:
        t.contact_count = counts.get(t.id, 0)  # setattr → lu par TagOut.from_attributes
    return tags


@router.post("", response_model=schemas.TagOut, status_code=status.HTTP_201_CREATED)
def create_tag(
    tid: str,
    body: schemas.TagCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Crée un tag. UniqueConstraint(tenant_id, name) → 409 si le nom existe déjà."""
    if db.query(models.Tag).filter_by(tenant_id=tid, name=body.name).first():
        raise HTTPException(409, f"Tag « {body.name} » existe déjà pour ce tenant")
    t = models.Tag(tenant_id=tid, name=body.name, color=body.color)
    db.add(t)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="tag.create", target_type="tag", target_id=t.id,
              detail={"name": t.name, "color": t.color})
    db.commit()
    db.refresh(t)
    t.contact_count = 0
    return t


@router.patch("/{tag_id}", response_model=schemas.TagOut)
def update_tag(
    tid: str,
    tag_id: str,
    body: schemas.TagUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Renomme et/ou recolore un tag. 404 si introuvable (ou hors tenant)."""
    t = db.query(models.Tag).filter_by(tenant_id=tid, id=tag_id).first()
    if not t:
        raise HTTPException(404, "Tag introuvable")
    changes = {}
    if body.name is not None and body.name != t.name:
        if db.query(models.Tag).filter_by(tenant_id=tid, name=body.name).first():
            raise HTTPException(409, f"Tag « {body.name} » existe déjà pour ce tenant")
        t.name = body.name
        changes["name"] = body.name
    if body.color is not None and body.color != t.color:
        t.color = body.color
        changes["color"] = body.color
    if changes:
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="tag.update", target_type="tag", target_id=t.id, detail=changes)
        db.commit()
    db.refresh(t)
    t.contact_count = (
        db.query(models.ContactTag).filter_by(tag_id=t.id).count()
    )
    return t


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tid: str,
    tag_id: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Supprime un tag. CASCADE-supprime les associations ContactTag (ondelete=CASCADE au niveau DB).
    404 si introuvable."""
    t = db.query(models.Tag).filter_by(tenant_id=tid, id=tag_id).first()
    if not t:
        raise HTTPException(404, "Tag introuvable")
    name = t.name
    db.delete(t)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="tag.delete", target_type="tag", target_id=tag_id, detail={"name": name})
    db.commit()
    return None


@router.get("/contacts/{cid}", response_model=list[schemas.TagOut])
def list_contact_tags(
    tid: str,
    cid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
):
    """Liste les tags apposés sur un contact. Lecture ouverte à tout membre actif. 404 si le
    contact n'existe pas (ou hors tenant)."""
    c = db.query(models.Contact).filter_by(tenant_id=tid, id=cid).first()
    if not c:
        raise HTTPException(404, "Contact introuvable")
    return (
        db.query(models.Tag)
        .join(models.ContactTag, models.ContactTag.tag_id == models.Tag.id)
        .filter(models.ContactTag.contact_id == cid)
        .order_by(models.Tag.name)
        .all()
    )


@router.post("/contacts/{cid}", response_model=list[schemas.TagOut])
def assign_tags(
    tid: str,
    cid: str,
    body: schemas.ContactTagAssign,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Assigne des tag_ids à un contact. Idempotent : skip les associations déjà existantes (PK
    composite contact_id+tag_id). Valide que les tags appartiennent au tenant (sinon 422).
    Retourne la liste à jour des tags du contact. 404 si le contact n'existe pas."""
    c = db.query(models.Contact).filter_by(tenant_id=tid, id=cid).first()
    if not c:
        raise HTTPException(404, "Contact introuvable")
    # Valide les tag_ids : doivent appartenir au tenant
    valid_tags = (
        db.query(models.Tag)
        .filter(models.Tag.tenant_id == tid, models.Tag.id.in_(body.tag_ids))
        .all()
    )
    valid_ids = {t.id for t in valid_tags}
    invalid = set(body.tag_ids) - valid_ids
    if invalid:
        raise HTTPException(422, f"Tags inconnus ou hors tenant: {sorted(invalid)}")
    # Idempotent : skip les déjà-existants
    existing = {ct.tag_id for ct in db.query(models.ContactTag).filter_by(contact_id=cid).all()}
    added = [tid_ for tid_ in body.tag_ids if tid_ not in existing]
    for tid_ in added:
        db.add(models.ContactTag(contact_id=cid, tag_id=tid_))
    if added:
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="contact.tag_assign", target_type="contact", target_id=cid,
                  detail={"tag_ids": added})
        db.commit()
    return (
        db.query(models.Tag)
        .join(models.ContactTag, models.ContactTag.tag_id == models.Tag.id)
        .filter(models.ContactTag.contact_id == cid)
        .order_by(models.Tag.name)
        .all()
    )


@router.delete("/contacts/{cid}/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_tag(
    tid: str,
    cid: str,
    tag_id: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Détache un tag d'un contact. 404 si l'association n'existe pas."""
    ct = db.query(models.ContactTag).filter_by(contact_id=cid, tag_id=tag_id).first()
    if not ct:
        raise HTTPException(404, "Association tag/contact introuvable")
    db.delete(ct)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="contact.tag_unassign", target_type="contact", target_id=cid,
              detail={"tag_id": tag_id})
    db.commit()
    return None
