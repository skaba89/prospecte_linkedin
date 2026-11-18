"""Facturation : plans, usage, changement de plan (quota de sièges + IA par plan), checkout Stripe
(sandbox/mock), devise tenant (P30 Commercialisation).

Le webhook Stripe est global (pas de tenant context) — enregistré séparément dans main.py
(/api/billing/webhook). Les endpoints tenant-scoped sont sous /api/tenants/{tid}/billing.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..services import billing_service

router = APIRouter(prefix="/api/tenants/{tid}/billing", tags=["billing"])


@router.get("/plans", response_model=list[dict])
def plans(tid: str):
    """Liste les 4 plans (Free|Starter|Pro|Enterprise) avec prix EUR, sièges, quotas IA, features.
    Prix en EUR (le frontend convertit selon tenant.currency)."""
    out = []
    for name, cfg in billing_service.PLANS.items():
        out.append({"name": name, "price_eur": cfg["price_eur"], "seats": cfg["seats"],
                    "ia_calls_month": cfg["ia_calls_month"], "color": cfg["color"],
                    "tagline": cfg["tagline"], "features": cfg["features"]})
    return out


@router.get("/currencies", response_model=list[dict])
def currencies(tid: str):
    """Liste les devises supportées (EUR|USD|GNF|XOF) avec symbole + nom."""
    return [{"code": k, "symbol": v["symbol"], "name": v["name"]} for k, v in billing_service.CURRENCIES.items()]


@router.get("", response_model=schemas.BillingOut)
def billing(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """État facturation : plan courant, sièges (usage/count), quota IA utilisé/limite, devise."""
    plan_cfg = billing_service.PLANS.get(_t.plan, billing_service.PLANS["Free"])
    seats = plan_cfg["seats"]
    count = db.query(models.Member).filter_by(tenant_id=tid).count()
    sub = billing_service.get_or_create_subscription(db, tid)
    return schemas.BillingOut(plan=_t.plan, seats=seats, member_count=count,
                              usage_pct=round(count / seats * 100, 1) if seats else 0,
                              ia_calls_used=sub.ia_calls_used, ia_calls_limit=plan_cfg["ia_calls_month"],
                              currency=_t.currency or "EUR")


@router.get("/subscription", response_model=schemas.SubscriptionOut)
def subscription(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Détail abonnement : plan, status, quota IA utilisé/limite/reset, période, stripe IDs, simulated."""
    sub = billing_service.get_or_create_subscription(db, tid)
    plan_cfg = billing_service.PLANS.get(sub.plan, billing_service.PLANS["Free"])
    return schemas.SubscriptionOut(
        plan=sub.plan, status=sub.status,
        ia_calls_used=sub.ia_calls_used, ia_calls_limit=plan_cfg["ia_calls_month"],
        ia_calls_reset_at=sub.ia_calls_reset_at.isoformat() if sub.ia_calls_reset_at else None,
        current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
        stripe_customer_id=sub.stripe_customer_id,
        simulated=not bool(settings.stripe_secret_key),
    )


@router.patch("", status_code=status.HTTP_200_OK)
def change_plan(tid: str, body: schemas.PlanUpdate, db: Session = Depends(get_db),
                tenant: models.Tenant = Depends(get_tenant),
                _perm=Depends(require_permission("billing.manage"))):
    """Change de plan (legacy — sans Stripe). Pour upgrade payant → POST /billing/checkout.
    Baisser de plan sous le quota courant (sièges) est interdit."""
    if body.plan not in billing_service.PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Plan invalide : {body.plan}")
    new_seats = billing_service.PLANS[body.plan]["seats"]
    count = db.query(models.Member).filter_by(tenant_id=tid).count()
    if count > new_seats:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Impossible de descendre : {count} membres > {new_seats} sièges du plan {body.plan}.")
    old_plan = tenant.plan
    tenant.plan = body.plan
    sub = billing_service.get_or_create_subscription(db, tid)
    sub.plan = body.plan
    # P48 : audit log immuable (changement de plan = sensitive billing change — SOC2/GDPR).
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="billing.plan.change", target_type="tenant", target_id=tid,
              detail={"before": {"plan": old_plan}, "after": {"plan": body.plan, "seats": new_seats}})
    db.commit()
    return {"plan": body.plan, "seats": new_seats, "price_eur": billing_service.PLANS[body.plan]["price_eur"]}


@router.post("/checkout", response_model=schemas.CheckoutOut)
def checkout(tid: str, body: schemas.CheckoutRequest, db: Session = Depends(get_db),
             _t: models.Tenant = Depends(get_tenant),
             _perm=Depends(require_permission("billing.manage"))):
    """Crée une Stripe Checkout Session pour upgrader vers target_plan.
    Si STRIPE_SECRET_KEY non configuré → mode mock (simulated=True, URL de test).
    Plan Free → upgrade direct sans paiement. Anti-fake : simulated=True JAMAIS présenté comme réel."""
    try:
        result = billing_service.create_checkout_session(db, tid, body.target_plan, body.success_url, body.cancel_url)
        return result
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.patch("/currency", status_code=status.HTTP_200_OK)
def update_currency(tid: str, body: schemas.CurrencyUpdate, db: Session = Depends(get_db),
                    tenant: models.Tenant = Depends(get_tenant),
                    _perm=Depends(require_permission("billing.manage"))):
    """Change la devise de facturation du tenant (EUR|USD|GNF|XOF). Affecte l'affichage des prix."""
    try:
        old_currency = tenant.currency
        tenant.currency = billing_service.validate_currency(body.currency)
        # P48 : audit log immuable (changement de devise = sensitive billing change).
        log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
                  action="billing.currency.change", target_type="tenant", target_id=tid,
                  detail={"before": {"currency": old_currency}, "after": {"currency": tenant.currency}})
        db.commit()
        return {"currency": tenant.currency, "symbol": billing_service.CURRENCIES[tenant.currency]["symbol"]}
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
