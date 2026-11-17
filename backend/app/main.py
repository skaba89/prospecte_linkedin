"""Point d'entrée FastAPI : CORS, rate-limit, routers, seed au démarrage, static du frontend."""

import logging
import os

from fastapi import Depends, FastAPI
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
    analytics,
    approvals,
    audit,
    auth,
    billing,
    content,
    crm,
    dashboard,
    inbound_webhooks,
    linkedin,
    members,
    notifications,
    sequences,
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
    tenants.router,
    members.router,
    approvals.router,
    dashboard.router,
    billing.router,
    content.router,
    linkedin.router,
    audit.router,
    crm.router,
    notifications.router,
    sequences.router,
    analytics.router,
    webhooks.router,
    inbound_webhooks.router,        # P20 : endpoint public POST /api/inbound/webhook/{token}
    inbound_webhooks.router_admin,  # P20 : CRUD admin /api/tenants/{tid}/inbound-webhooks
):
    app.include_router(r)


@app.get("/metrics")
def metrics():
    """Exposition Prometheus (texte) — scrape par Prometheus/Grafana. Public (pas d'auth)."""
    return metrics_response()


# Frontend statique : sert le dashboard (index.html) si le dossier existe.
# En local : on monte ./frontend_public ; en docker : monté par le build.
_FE_DIR = os.environ.get("REVPULSE_FRONTEND_DIR", "./frontend_public")
if os.path.isdir(_FE_DIR):
    app.mount("/", StaticFiles(directory=_FE_DIR, html=True), name="frontend")
