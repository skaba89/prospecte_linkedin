"""Point d'entrée FastAPI : CORS, rate-limit, routers, seed au démarrage, static du frontend."""

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, get_db, init_db
from .extensions import limiter
from .logging_config import RequestLoggingMiddleware, configure_logging
from .metrics import MetricsMiddleware, metrics_response
from .routers import (
    agents,
    ai,
    analytics,
    approvals,
    audit,
    auth,
    billing,
    career,
    content,
    content_studio,
    crm,
    dashboard,
    inbound_webhooks,
    linkedin,
    members,
    notifications,
    prospecting,
    sales_intelligence,
    sequences,
    tags,
    tenants,
    webhooks,
)
from .seed import seed_all

# Logging structuré (JSON en prod, texte en dev/test) — configuré avant l'app.
configure_logging(settings.log_level, settings.log_format)

app = FastAPI(
    title="RevPulse API",
    version="1.1.0",
    description="Backend multi-tenant du LinkedIn Command Center — auth JWT, RBAC, "
    "dashboard data, migrations Alembic, rate-limit, invitations e-mail SMTP.",
)

# Rate-limiting (slowapi) : le limiter est défini dans .extensions (pas d'import circulaire).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Request-ID"],
)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(MetricsMiddleware)
# Request-ID + logging structuré (outermost : logge tout, y compris 429 et preflight).
app.add_middleware(RequestLoggingMiddleware)


@app.on_event("startup")
def _startup():
    # P32 : durcissement production — WEBHOOK_SSRF_ALLOW_PRIVATE=True est une exception SSRF
    # légitime en dev/CI (test_api.py reçoit les webhooks sur localhost) mais une faille en prod
    # (autorise les webhooks à pointer vers des services internes/metadata). Fail-fast au boot.
    if settings.env == "production" and os.getenv("WEBHOOK_SSRF_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            "REVPULSE_ENV=production + WEBHOOK_SSRF_ALLOW_PRIVATE=True interdit en production "
            "(contourne la protection SSRF — retire cette variable en prod)."
        )
    init_db()
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()
    # P15 : scheduler de séquences (background thread daemon, stdlib threading — pas de dep).
    # Traite les enrollments dus toutes les SEQUENCE_SCHEDULER_INTERVAL_SECONDS (défaut 300s).
    from .services.sequences import start_scheduler

    start_scheduler()
    # P19 : scheduler de retry webhooks (background thread daemon). Retente les deliveries
    # pending toutes les WEBHOOK_SCHEDULER_INTERVAL_SECONDS (défaut 60s — time-sensitive).
    from .services.webhooks import start_scheduler as start_webhook_scheduler

    start_webhook_scheduler()


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """Healthcheck : ping DB réel (SELECT 1). 200/ok si DB up, 503/degraded sinon.

    P10.1 : n'expose NI la chaîne de connexion NI le détail d'exception — fuite d'info.
    Le détail est loggé côté serveur uniquement (structured logging).
    """
    db_ok = False
    try:
        db.execute(text("SELECT 1")).first()
        db_ok = True
    except Exception as e:
        logging.getLogger("revpulse.health").warning("DB healthcheck failed: %s: %s", type(e).__name__, str(e)[:200])
    return JSONResponse(
        {"status": "ok" if db_ok else "degraded", "service": "revpulse-api", "db_ok": db_ok, "env": settings.env},
        status_code=200 if db_ok else 503,
    )


# Routers métier
for r in (
    auth.router,
    career.router,             # P34 : Career (espace candidat isolé du Business — user-scoped, pas tenant)
    tenants.router,
    members.router,
    approvals.router,
    dashboard.router,
    billing.router,
    content.router,
    content_studio.router,          # P26 : LinkedIn Content Studio (posts + templates + carousel + calendar + stats + validation collaborative)
    linkedin.router,
    ai.router,                      # P24-B : AI Gateway (generate + providers + prompts + history)
    agents.router,                  # P25 : 10 agents IA + Copilot (agents run + history + copilot chat)
    audit.router,
    crm.router,
    prospecting.router,            # P27 : Prospection avancée (multicanal + A/B testing + intent signals + enrichment)
    sales_intelligence.router,     # P28 : Warmup inbox + Sales Intelligence (funnel + cohorts + forecast)
    notifications.router,
    sequences.router,
    analytics.router,
    webhooks.router,
    tags.router,                   # P22 : tags CRUD + assign/unassign sur contacts
    inbound_webhooks.router,        # P20 : endpoint public POST /api/inbound/webhook/{token}
    inbound_webhooks.router_admin,  # P20 : CRUD admin /api/tenants/{tid}/inbound-webhooks
):
    app.include_router(r)


@app.get("/metrics")
def metrics():
    """Exposition Prometheus (texte) — scrape par Prometheus/Grafana. Public (pas d'auth)."""
    return metrics_response()


@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook Stripe global (P30). Vérif signature si STRIPE_WEBHOOK_SECRET configuré ;
    sinon mode mock (accepte les events de test). Met à jour Subscription (plan/status/stripe IDs).
    Stripe attend 200 pour acquitter — on retourne toujours 200 (erreurs loggées)."""
    import json

    from .config import settings as cfg
    from .services import billing_service
    raw = await request.body()
    sig = request.headers.get("stripe-signature", "")
    payload = raw.decode("utf-8", errors="replace")
    # Vérif signature si secret configuré + stripe lib dispo
    if cfg.stripe_webhook_secret and cfg.stripe_secret_key:
        try:
            import stripe  # type: ignore
            stripe.api_key = cfg.stripe_secret_key
            event = stripe.Webhook.construct_event(payload, sig, cfg.stripe_webhook_secret)
            event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        except Exception as e:
            raise HTTPException(400, f"Signature Stripe invalide : {e}") from None
    else:
        # Mode mock (dev/CI/test) — parse JSON directement
        try:
            event_dict = json.loads(payload)
        except Exception:
            raise HTTPException(400, "Payload JSON invalide (mode mock — pas de STRIPE_WEBHOOK_SECRET).") from None
    return billing_service.handle_webhook_event(db, event_dict)


# Frontend statique : sert le dashboard (index.html) si le dossier existe.
# En local : on monte ./frontend_public ; en docker : monté par le build.
_FE_DIR = os.environ.get("REVPULSE_FRONTEND_DIR", "./frontend_public")
if os.path.isdir(_FE_DIR):
    app.mount("/", StaticFiles(directory=_FE_DIR, html=True), name="frontend")
