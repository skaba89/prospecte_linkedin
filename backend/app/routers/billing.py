"""Facturation : plans, usage, changement de plan (quota de sièges par plan)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api/tenants/{tid}/billing", tags=["billing"])

PLANS = {
    "Starter": {"price": 29, "seats": 3, "color": "#64748b"},
    "Pro": {"price": 99, "seats": 10, "color": "#635bff"},
    "Enterprise": {"price": 299, "seats": 50, "color": "#0a66c2"},
}


@router.get("/plans", response_model=list[dict])
def plans():
    return [{"name": k, **v} for k, v in PLANS.items()]


@router.get("", response_model=schemas.BillingOut)
def billing(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    seats = PLANS.get(_t.plan, PLANS["Starter"])["seats"]
    count = db.query(models.Member).filter_by(tenant_id=tid).count()
    return schemas.BillingOut(plan=_t.plan, seats=seats, member_count=count,
                              usage_pct=round(count / seats * 100, 1) if seats else 0)


@router.patch("", status_code=status.HTTP_200_OK)
def change_plan(tid: str, body: schemas.PlanUpdate, db: Session = Depends(get_db),
                tenant: models.Tenant = Depends(get_tenant),
                _perm=Depends(require_permission("billing.manage"))):
    if body.plan not in PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Plan invalide : {body.plan}")
    # Baisser de plan sous le quota courant est interdit
    new_seats = PLANS[body.plan]["seats"]
    count = db.query(models.Member).filter_by(tenant_id=tid).count()
    if count > new_seats:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Impossible de descendre : {count} membres > {new_seats} sièges du plan {body.plan}.")
    tenant.plan = body.plan
    db.commit()
    return {"plan": body.plan, "seats": new_seats, "price": PLANS[body.plan]["price"]}
