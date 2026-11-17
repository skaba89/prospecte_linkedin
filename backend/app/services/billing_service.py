"""P30 Commercialisation — service facturation.

PLANS (Free|Starter|Pro|Enterprise) avec quotas IA (ia_calls_month) + sièges + prix + features.
get_or_create_subscription : lazily crée la Subscription par tenant (défaut Free).
check_and_increment_ia_quota : guard appelé par services/ai/gateway.generate() avant le provider —
  check ia_calls_used < PLANS[plan].ia_calls_month, incrémente si OK, reset mensuel si période dépassée.
  Retourne (allowed, used, limit) — le gateway fallback SimulatedProvider si pas allowed + flag
  quota_exceeded (jamais lève — best-effort).
Stripe : si settings.stripe_secret_key configuré → vraies Checkout Sessions + webhook signature verify.
  Sinon → mode mock (URL de test, webhook accepte sans vérif). Anti-fake : checkout porte simulated=True
  quand mock — JAMAIS présenté comme un paiement réel.
Devises : CURRENCIES = EUR|USD|GNF|XOF (symbole + code Stripe). Prix stockés en EUR, convertis pour
  l'affichage (taux fixes simplifiés — v2 : taux dynamiques).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from ..config import settings

# ─── Plans (étendu P30 : Free + quotas IA + devises) ────────────────────────
# ia_calls_month = limite d'appels IA par mois (gateway.generate() guard). null = illimité (Enterprise).
# prices_eur = prix mensuel en EUR (converti selon tenant.currency pour l'affichage).
PLANS: dict[str, dict[str, Any]] = {
    "Free": {
        "price_eur": 0, "seats": 1, "ia_calls_month": 10, "color": "#64748b",
        "features": ["1 siège", "10 appels IA / mois", "CRM basics", "1 séquence"],
        "tagline": "Découverte",
    },
    "Starter": {
        "price_eur": 29, "seats": 3, "ia_calls_month": 200, "color": "#06b6d4",
        "features": ["3 sièges", "200 appels IA / mois", "CRM + séquences", "Analytics basics"],
        "tagline": "Solo / petit team",
    },
    "Pro": {
        "price_eur": 99, "seats": 10, "ia_calls_month": 2000, "color": "#635bff",
        "features": ["10 sièges", "2 000 appels IA / mois", "Coach IA entretiens", "Agent autonome Career", "Analytics avancés"],
        "tagline": "Teams en croissance",
    },
    "Enterprise": {
        "price_eur": 299, "seats": 50, "ia_calls_month": None, "color": "#0a66c2",  # None = illimité
        "features": ["50 sièges", "Appels IA illimités", "SSO + audit log", "Support dédié", "SLA 99.9%"],
        "tagline": "Entreprise",
    },
}

# ─── Devises supportées (P30 multi-monnaies) ────────────────────────────────
# EUR (€, euro), USD ($, dollar), GNF (GNF, franc guinéen), XOF (CFA, franc CFA West).
# Taux de conversion simplifiés (1 EUR = X). v2 : taux dynamiques via API.
CURRENCIES: dict[str, dict[str, str | float]] = {
    "EUR": {"symbol": "€", "name": "Euro", "stripe_code": "eur", "rate_from_eur": 1.0},
    "USD": {"symbol": "$", "name": "US Dollar", "stripe_code": "usd", "rate_from_eur": 1.08},
    "GNF": {"symbol": "GNF", "name": "Franc guinéen", "stripe_code": None, "rate_from_eur": 9050.0},  # Stripe ne supporte pas GNF → EUR fallback
    "XOF": {"symbol": "CFA", "name": "Franc CFA", "stripe_code": "xof", "rate_from_eur": 655.957},
}
SUPPORTED_CURRENCIES = list(CURRENCIES.keys())


def validate_currency(code: str) -> str:
    """Valide + normalise un code devise (EUR|USD|GNF|XOF). Lève ValueError si invalide."""
    if not code:
        return "EUR"
    code = code.upper().strip()
    if code not in CURRENCIES:
        raise ValueError(f"Devise non supportée : {code}. Supportées : {', '.join(SUPPORTED_CURRENCIES)}")
    return code


def convert_price(eur_amount: float, target_currency: str) -> tuple[float, str]:
    """Convertit un prix EUR vers la devise cible. Retourne (montant_arrondi, symbole).
    GNF/XOF arrondis à l'entier (pas de décimales). EUR/USD 2 décimales."""
    cur = CURRENCIES.get(validate_currency(target_currency), CURRENCIES["EUR"])
    converted = eur_amount * cur["rate_from_eur"]
    if target_currency in ("GNF", "XOF"):
        return round(converted), cur["symbol"]
    return round(converted, 2), cur["symbol"]


def format_price(eur_amount: float, currency: str) -> str:
    """Formate un prix avec devise (ex. '29,99 €', '31,32 $', '262 455 CFA')."""
    amount, symbol = convert_price(eur_amount, currency)
    if currency in ("GNF", "XOF"):
        formatted = f"{amount:,}".replace(",", " ")
        return f"{formatted} {symbol}"
    formatted = f"{amount:.2f}".replace(".", ",")
    return f"{formatted} {symbol}"


# ─── Subscription management ────────────────────────────────────────────────
def get_or_create_subscription(db: Session, tenant_id: str) -> models.Subscription:
    """Récupère ou crée la Subscription d'un tenant (défaut Free). Sync plan avec tenant.plan."""
    sub = db.query(models.Subscription).filter_by(tenant_id=tenant_id).first()
    if sub:
        # Sync P30 : subscription.plan est autoritaire (état billing) → propage vers tenant.plan
        # si divergence (ex: webhook Stripe a upgradé subscription mais tenant.plan pas encore sync).
        if sub.plan and sub.plan != _tenant_plan(db, tenant_id):
            tenant = db.query(models.Tenant).filter_by(id=tenant_id).first()
            if tenant and tenant.plan != sub.plan:
                tenant.plan = sub.plan
                db.commit()
        return sub
    # Création — plan hérité du tenant (défaut Starter côté tenant, Free côté subscription si tenant.plan Free)
    plan = _tenant_plan(db, tenant_id)
    sub = models.Subscription(
        tenant_id=tenant_id, plan=plan, status="active",
        ia_calls_used=0, ia_calls_reset_at=datetime.utcnow() + timedelta(days=30),
        current_period_end=datetime.utcnow() + timedelta(days=30),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _tenant_plan(db: Session, tenant_id: str) -> str:
    t = db.query(models.Tenant).filter_by(id=tenant_id).first()
    plan = t.plan if t else "Free"
    return plan if plan in PLANS else "Free"


def check_and_increment_ia_quota(db: Session, tenant_id: str) -> tuple[bool, int, int | None]:
    """Guard quota IA plan-based. Appelé par gateway.generate() AVANT l'appel provider.
    Retourne (allowed, used, limit). limit=None = illimité (Enterprise).
    - Reset mensuel si ia_calls_reset_at dépassé.
    - Si used < limit (ou limit None) → incrémente used + retourne (True, used_after, limit).
    - Sinon → (False, used, limit) — le gateway fallback SimulatedProvider + flag quota_exceeded.
    """
    sub = get_or_create_subscription(db, tenant_id)
    now = datetime.utcnow()
    # Reset mensuel
    if sub.ia_calls_reset_at and sub.ia_calls_reset_at < now:
        sub.ia_calls_used = 0
        sub.ia_calls_reset_at = now + timedelta(days=30)
    plan_cfg = PLANS.get(sub.plan, PLANS["Free"])
    limit = plan_cfg["ia_calls_month"]  # None = illimité
    if limit is None:
        sub.ia_calls_used += 1
        db.commit()
        return (True, sub.ia_calls_used, None)
    if sub.ia_calls_used >= limit:
        db.commit()  # persiste le reset éventuel
        return (False, sub.ia_calls_used, limit)
    sub.ia_calls_used += 1
    db.commit()
    return (True, sub.ia_calls_used, limit)


# ─── Stripe checkout (real ou mock) ─────────────────────────────────────────
def create_checkout_session(db: Session, tenant_id: str, target_plan: str, success_url: str, cancel_url: str) -> dict:
    """Crée une Stripe Checkout Session pour upgrader vers target_plan.
    Si settings.stripe_secret_key configuré → vraie session Stripe (stripe lib).
    Sinon → mock : URL de test + simulated=True (anti-fake — JAMAIS présenté comme paiement réel).
    Retourne {checkout_url, session_id, simulated}. Lève ValueError si plan invalide.
    """
    if target_plan not in PLANS:
        raise ValueError(f"Plan invalide : {target_plan}")
    plan_cfg = PLANS[target_plan]
    sub = get_or_create_subscription(db, tenant_id)
    tenant = db.query(models.Tenant).filter_by(id=tenant_id).first()
    currency = validate_currency(tenant.currency) if tenant else "EUR"
    # GNF non supporté par Stripe → fallback EUR pour le paiement (affichage reste GNF)
    stripe_currency = CURRENCIES[currency]["stripe_code"] or "eur"
    price_eur = plan_cfg["price_eur"]
    if price_eur == 0:
        # Plan Free — pas de checkout, upgrade direct
        sub.plan = "Free"
        if tenant:
            tenant.plan = "Free"
        sub.ia_calls_used = 0
        sub.ia_calls_reset_at = datetime.utcnow() + timedelta(days=30)
        db.commit()
        return {"checkout_url": None, "session_id": None, "simulated": True, "upgraded_directly": True,
                "plan": "Free", "message": "Plan Free activé (sans paiement)."}
    # Stripe réel si configuré
    if settings.stripe_secret_key and not settings.is_production:
        try:
            import stripe  # type: ignore
            stripe.api_key = settings.stripe_secret_key
            # Prix en centimes (Stripe), convertir EUR → devise Stripe
            amount_cents = round(price_eur * CURRENCIES[currency]["rate_from_eur"] * 100)
            session = stripe.checkout.Session.create(
                mode="subscription",
                customer_email=sub.stripe_customer_id or None,
                line_items=[{"price_data": {
                    "currency": stripe_currency,
                    "product_data": {"name": f"RevPulse {target_plan} (mensuel)"},
                    "unit_amount": amount_cents,
                    "recurring": {"interval": "month"},
                }, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"tenant_id": tenant_id, "target_plan": target_plan},
            )
            return {"checkout_url": session.url, "session_id": session.id, "simulated": False}
        except Exception as e:
            # Fallback mock si Stripe échoue (anti-blocage CI/test)
            return _mock_checkout(tenant_id, target_plan, price_eur, currency, error=str(e))
    # Mock (pas de clé Stripe — dev/CI/test)
    return _mock_checkout(tenant_id, target_plan, price_eur, currency)


def _mock_checkout(tenant_id: str, target_plan: str, price_eur: float, currency: str, error: str | None = None) -> dict:
    """Mode mock : URL de test factice + simulated=True. En test/CI, auto-upgrade direct possible via
    webhook mock. Anti-fake : simulated=True JAMAIS présenté comme un paiement réel."""
    mock_url = f"https://mock.stripe.com/checkout/revpulse_{target_plan.lower()}_{tenant_id[:8]}"
    msg = "Mode mock (STRIPE_SECRET_KEY non configuré)."
    if error:
        msg += f" Stripe réel a échoué ({error}) — fallback mock."
    return {"checkout_url": mock_url, "session_id": f"mock_{tenant_id[:8]}_{target_plan}",
            "simulated": True, "message": msg, "plan": target_plan,
            "price_display": format_price(price_eur, currency)}


def handle_webhook_event(db: Session, event: dict) -> dict:
    """Traite un event Stripe webhook (checkout.session.completed ou customer.subscription.updated).
    - En mode mock (pas de stripe_webhook_secret) : accepte les events de test sans vérif signature.
    - En mode réel : la vérif signature est faite par le router avant d'appeler cette fonction.
    Met à jour Subscription.plan + status + stripe IDs + current_period_end.
    """
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    result = {"processed": False, "type": event_type}
    if event_type == "checkout.session.completed":
        tenant_id = data.get("metadata", {}).get("tenant_id")
        target_plan = data.get("metadata", {}).get("target_plan")
        if not tenant_id or not target_plan:
            return {**result, "error": "metadata tenant_id/target_plan manquants"}
        sub = get_or_create_subscription(db, tenant_id)
        sub.plan = target_plan
        sub.status = "active"
        sub.stripe_customer_id = data.get("customer") or sub.stripe_customer_id
        sub.stripe_subscription_id = data.get("subscription") or sub.stripe_subscription_id
        sub.current_period_end = datetime.utcnow() + timedelta(days=30)
        sub.ia_calls_used = 0  # reset quota on upgrade
        sub.ia_calls_reset_at = datetime.utcnow() + timedelta(days=30)
        tenant = db.query(models.Tenant).filter_by(id=tenant_id).first()
        if tenant:
            tenant.plan = target_plan
        db.commit()
        result.update({"processed": True, "tenant_id": tenant_id, "plan": target_plan})
    elif event_type == "customer.subscription.deleted":
        cust = data.get("customer")
        sub = db.query(models.Subscription).filter_by(stripe_customer_id=cust).first() if cust else None
        if sub:
            sub.status = "canceled"
            sub.plan = "Free"
            tenant = db.query(models.Tenant).filter_by(id=sub.tenant_id).first()
            if tenant:
                tenant.plan = "Free"
            db.commit()
            result.update({"processed": True, "tenant_id": sub.tenant_id})
    return result
