"""Membres & RBAC : liste, invitation (token), acceptation, changement de rôle, retrait."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api/tenants/{tid}/members", tags=["members"])

VALID_ROLES = {"Owner", "Admin", "Editor", "Contributor", "Viewer"}


@router.get("", response_model=list[schemas.MemberOut])
def list_members(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    return db.query(models.Member).filter_by(tenant_id=tid).order_by(models.Member.added_at).all()


@router.post("/invite", response_model=schemas.MemberOut, status_code=status.HTTP_201_CREATED)
def invite(tid: str, body: schemas.InviteCreate, db: Session = Depends(get_db),
           _t: models.Tenant = Depends(get_tenant), membership=Depends(require_permission("members.manage"))):
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Rôle invalide : {body.role}")
    # Quota de sièges selon le plan
    from ..routers.tenants import PLANS
    seats = PLANS.get(_t.plan, 3)
    count = db.query(models.Member).filter_by(tenant_id=tid).count()
    if count >= seats:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"Quota atteint ({seats} sièges au plan {_t.plan}). Passez à un plan supérieur.")
    if db.query(models.Member).filter_by(tenant_id=tid, email=body.email.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est déjà invité ou membre.")
    name = body.name or body.email.split("@")[0]
    token = uuid.uuid4().hex[:10]
    m = models.Member(id=f"mem-{uuid.uuid4().hex[:10]}", tenant_id=tid, user_id=None,
                      email=body.email.lower(), name=name, role=body.role, status="invited",
                      token=token, invited_by=membership.user_id)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.post("/{mid}/accept", response_model=schemas.MemberOut)
def accept_invite(tid: str, mid: str, db: Session = Depends(get_db),
                  _t: models.Tenant = Depends(get_tenant), membership=Depends(require_permission("members.manage"))):
    """Simule l'acceptation d'une invitation : passage à Actif, rattachement au user courant."""
    m = db.query(models.Member).filter_by(id=mid, tenant_id=tid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation introuvable")
    m.status = "active"
    m.token = None
    m.user_id = membership.user_id
    db.commit()
    db.refresh(m)
    return m


@router.patch("/{mid}", response_model=schemas.MemberOut)
def change_role(tid: str, mid: str, body: schemas.RoleUpdate, db: Session = Depends(get_db),
                _t: models.Tenant = Depends(get_tenant), _perm=Depends(require_permission("members.manage"))):
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Rôle invalide : {body.role}")
    m = db.query(models.Member).filter_by(id=mid, tenant_id=tid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre introuvable")
    if m.role == "Owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le rôle Owner ne peut pas être modifié")
    m.role = body.role
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{mid}", status_code=status.HTTP_200_OK)
def remove_member(tid: str, mid: str, db: Session = Depends(get_db),
                  _t: models.Tenant = Depends(get_tenant), _perm=Depends(require_permission("members.manage"))):
    m = db.query(models.Member).filter_by(id=mid, tenant_id=tid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membre introuvable")
    if m.role == "Owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le Owner ne peut pas être retiré")
    db.delete(m)
    db.commit()
    return {"removed": mid}
