"""Dashboard : expose les modules de données (clé → payload JSON), scopés par tenant."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_tenant

router = APIRouter(prefix="/api/tenants/{tid}/dashboard", tags=["dashboard"])


@router.get("/modules", response_model=dict[str, Any])
def list_modules(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    rows = db.query(models.DashboardModule).filter_by(tenant_id=tid).all()
    return {r.module_key: r.payload for r in rows}


@router.get("/modules/{key}", response_model=Any)
def get_module(tid: str, key: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    r = db.query(models.DashboardModule).filter_by(tenant_id=tid, module_key=key).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Module '{key}' introuvable")
    return r.payload
