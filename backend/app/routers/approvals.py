"""Approbations : liste (non approuvées), approuver."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/approvals", tags=["approvals"])


@router.get("", response_model=list[schemas.ApprovalOut])
def list_approvals(tid: str, response: Response, page: Page = Depends(page_dep),
                   db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """File d'approbation : contenus en attente uniquement (status='pending'). Paginée."""
    q = (db.query(models.Approval)
         .filter_by(tenant_id=tid, status="pending")
         .order_by(models.Approval.priority.desc()))
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


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


@router.post("/{aid}/reject", status_code=status.HTTP_200_OK)
def reject(tid: str, aid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
           _perm=Depends(require_permission("approvals.manage"))):
    """Refuse un contenu en file (status='rejected', sort de la file pending)."""
    a = db.query(models.Approval).filter_by(id=aid, tenant_id=tid).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approbation introuvable")
    a.status = "rejected"
    a.status_label = "Refusé"
    a.status_color = "#ef4444"
    db.commit()
    return {"rejected": aid}


@router.get("/history", response_model=list[schemas.ApprovalOut])
def approvals_history(tid: str, response: Response, page: Page = Depends(page_dep),
                      db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Contenus traités (approuvés ou refusés), hors file pending. Paginée."""
    q = (db.query(models.Approval)
         .filter(models.Approval.tenant_id == tid, models.Approval.status != "pending")
         .order_by(models.Approval.id))
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()
