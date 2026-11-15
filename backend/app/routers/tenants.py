"""Tenants : liste, création, détail, renommage, suppression, bascule."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_tenant, require_permission

router = APIRouter(prefix="/api/tenants", tags=["tenants"])

PLANS = {"Starter": 3, "Pro": 10, "Enterprise": 50}


def _serialize(t: models.Tenant, db: Session, user_id: str) -> schemas.TenantOut:
    profile = db.query(models.Profile).filter_by(tenant_id=t.id).first()
    members = db.query(models.Member).filter_by(tenant_id=t.id).all()
    membership = next((m for m in members if m.user_id == user_id), None)
    role = membership.role if membership else ("Owner" if t.owner_id == user_id else None)
    return schemas.TenantOut(
        id=t.id, name=t.name, plan=t.plan, owner_id=t.owner_id, created_at=t.created_at,
        role=role, member_count=len(members),
        profile=schemas.ProfileOut(type=profile.type, name=profile.name, headline=profile.headline,
                                    company=profile.company, followers=profile.followers,
                                    initials=profile.initials) if profile else None,
    )


@router.get("", response_model=list[schemas.TenantOut])
def list_tenants(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Tous les espaces où l'utilisateur est membre ou propriétaire."""
    owned = db.query(models.Tenant).filter_by(owner_id=user.id).all()
    member_of = (db.query(models.Tenant)
                 .join(models.Member, models.Member.tenant_id == models.Tenant.id)
                 .filter(models.Member.user_id == user.id, models.Tenant.owner_id != user.id).all())
    tenants = {t.id: t for t in owned + member_of}.values()
    return [_serialize(t, db, user.id) for t in tenants]


@router.post("", response_model=schemas.TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: schemas.TenantCreate, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    tid = f"tnt-{uuid.uuid4().hex[:10]}"
    t = models.Tenant(id=tid, name=body.name, plan=body.plan, owner_id=user.id, created_at=datetime.utcnow())
    db.add(t)
    db.flush()
    db.add(models.Member(id=f"mem-{uuid.uuid4().hex[:10]}", tenant_id=tid, user_id=user.id, email=user.email,
                         name=user.name, role="Owner", status="active", invited_by="self",
                         added_at=datetime.utcnow()))
    db.commit()
    db.refresh(t)
    return _serialize(t, db, user.id)


@router.get("/{tid}", response_model=schemas.TenantOut)
def get_tenant_detail(tid: str, db: Session = Depends(get_db), tenant: models.Tenant = Depends(get_tenant),
                      user: models.User = Depends(get_current_user)):
    return _serialize(tenant, db, user.id)


@router.patch("/{tid}", response_model=schemas.TenantOut)
def rename_tenant(tid: str, body: schemas.TenantRename, db: Session = Depends(get_db),
                  tenant: models.Tenant = Depends(get_tenant),
                  _perm=Depends(require_permission("workspace.manage"))):
    tenant.name = body.name
    db.commit()
    db.refresh(tenant)
    return _serialize(tenant, db, tenant.owner_id)


@router.delete("/{tid}", status_code=status.HTTP_200_OK)
def delete_tenant(tid: str, db: Session = Depends(get_db), tenant: models.Tenant = Depends(get_tenant),
                  _perm=Depends(require_permission("workspace.manage"))):
    """Supprime l'espace et toutes ses données (cascade manuelle)."""
    db.query(models.DashboardModule).filter_by(tenant_id=tid).delete()
    db.query(models.Approval).filter_by(tenant_id=tid).delete()
    db.query(models.Member).filter_by(tenant_id=tid).delete()
    db.query(models.Profile).filter_by(tenant_id=tid).delete()
    db.delete(tenant)
    db.commit()
    return {"deleted": tid, "remaining": [_serialize(t, db, tenant.owner_id).id for t in
                                           db.query(models.Tenant).filter_by(owner_id=tenant.owner_id).all()]}


@router.post("/{tid}/switch", response_model=schemas.TenantOut)
def switch_tenant(tid: str, db: Session = Depends(get_db), tenant: models.Tenant = Depends(get_tenant),
                  user: models.User = Depends(get_current_user)):
    """Informe l'API de l'espace actif côté client (stateless : renvoie juste le détail)."""
    return _serialize(tenant, db, user.id)


@router.post("/{tid}/profile", response_model=schemas.ProfileOut)
def set_profile(tid: str, body: schemas.ProfileOut, db: Session = Depends(get_db),
                tenant: models.Tenant = Depends(get_tenant),
                _perm=Depends(require_permission("workspace.manage"))):
    """Crée ou met à jour le profil connecté de l'espace (LinkedIn simulé ou natif)."""
    p = db.query(models.Profile).filter_by(tenant_id=tid).first()
    if p:
        for k in ("type", "name", "headline", "company", "followers", "initials"):
            setattr(p, k, getattr(body, k))
    else:
        p = models.Profile(tenant_id=tid, type=body.type, name=body.name, headline=body.headline,
                           company=body.company, followers=body.followers, initials=body.initials)
        db.add(p)
    db.commit()
    db.refresh(p)
    return p
