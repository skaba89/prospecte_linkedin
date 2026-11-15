"""Membres & RBAC : liste, invitation (token + e-mail SMTP), acceptation, rôle, retrait."""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..mailer import send_invitation_email
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/members", tags=["members"])

VALID_ROLES = {"Owner", "Admin", "Editor", "Contributor", "Viewer"}


@router.get("", response_model=list[schemas.MemberOut])
def list_members(tid: str, response: Response, page: Page = Depends(page_dep),
                 db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    q = db.query(models.Member).filter_by(tenant_id=tid).order_by(models.Member.added_at)
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.post("/invite", response_model=schemas.InviteResult, status_code=status.HTTP_201_CREATED)
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
    expires_at = datetime.utcnow() + timedelta(days=7)
    m = models.Member(id=f"mem-{uuid.uuid4().hex[:10]}", tenant_id=tid, user_id=None,
                      email=body.email.lower(), name=name, role=body.role, status="invited",
                      token=token, invited_by=membership.user_id, expires_at=expires_at)
    db.add(m)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="member.invite", target_type="member", target_id=m.id,
              detail={"email": m.email, "role": m.role})
    db.commit()
    db.refresh(m)
    # Envoi de l'e-mail d'invitation (SMTP réel si activé, dry-run = log sinon).
    invite_link = f"{settings.app_base_url}/accept?token={token}"
    inviter = membership.name or membership.email or "Un administrateur"
    sent = send_invitation_email(m.email, invite_link, _t.name, m.role, inviter)
    return {
        "id": m.id, "tenant_id": m.tenant_id, "email": m.email, "name": m.name,
        "role": m.role, "status": m.status, "token": m.token, "invited_by": m.invited_by,
        "added_at": m.added_at, "expires_at": m.expires_at, "email_sent": sent,
    }


@router.post("/{mid}/accept", response_model=schemas.MemberOut)
def accept_invite(tid: str, mid: str, db: Session = Depends(get_db),
                  _t: models.Tenant = Depends(get_tenant), membership=Depends(require_permission("members.manage"))):
    """Simule l'acceptation d'une invitation : passage à Actif, rattachement au user courant.
    Refuse si l'invitation a expiré (expires_at dépassé)."""
    m = db.query(models.Member).filter_by(id=mid, tenant_id=tid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation introuvable")
    if m.status == "invited" and m.expires_at and m.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_410_GONE, "Invitation expirée")
    m.status = "active"
    m.token = None
    m.user_id = membership.user_id
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="member.accept", target_type="member", target_id=mid,
              detail={"email": m.email})
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
    old_role = m.role
    m.role = body.role
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="member.role_change", target_type="member", target_id=mid,
              detail={"email": m.email, "old": old_role, "new": body.role})
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
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="member.remove", target_type="member", target_id=mid,
              detail={"email": m.email, "role": m.role})
    db.delete(m)
    db.commit()
    return {"removed": mid}
