"""Approbations : liste (non approuvées), approuver."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api/tenants/{tid}/approvals", tags=["approvals"])


@router.get("", response_model=list[schemas.ApprovalOut])
def list_approvals(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    return (db.query(models.Approval)
            .filter_by(tenant_id=tid, approved=False)
            .order_by(models.Approval.priority.desc()).all())


@router.post("/{aid}/approve", status_code=status.HTTP_200_OK)
def approve(tid: str, aid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
            _perm=Depends(require_permission("approvals.manage"))):
    a = db.query(models.Approval).filter_by(id=aid, tenant_id=tid).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approbation introuvable")
    a.approved = True
    a.status = "approved"
    a.status_label = "Approuvé"
    a.status_color = "#10b981"
    db.commit()
    return {"approved": aid}
