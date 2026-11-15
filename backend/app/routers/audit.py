"""Audit log : lecture de la trace immuable des actions mutantes d'un tenant.

GET /api/tenants/{tid}/audit — paginée (P3 : ?skip/?limit + X-Total-Count),
plus récent d'abord. Accès Owner uniquement (permission workspace.manage) :
seul le propriétaire de l'espace voit l'historique complet des actions.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/audit", tags=["audit"])


@router.get("", response_model=list[schemas.AuditLogOut])
def list_audit(tid: str, response: Response, page: Page = Depends(page_dep),
               db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
               _perm=Depends(require_permission("workspace.manage"))):
    """Trace des actions mutantes du tenant (approbations, membres, contenu, LinkedIn…).
    Plus récent d'abord. Accès Owner (workspace.manage). Paginée."""
    q = (db.query(models.AuditLog)
         .filter_by(tenant_id=tid)
         .order_by(models.AuditLog.created_at.desc()))
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()
