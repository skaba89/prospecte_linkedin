"""Point d'entrée FastAPI : CORS, rate-limit, routers, seed au démarrage, static du frontend."""
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
from .routers import approvals, auth, billing, content, dashboard, linkedin, members, tenants
from .seed import seed_all

# Logging structuré (JSON en prod, texte en dev/test) — configuré avant l'app.
configure_logging(settings.log_level, settings.log_format)

app = FastAPI(title="RevPulse API", version="1.1.0",
              description="Backend multi-tenant du LinkedIn Command Center — auth JWT, RBAC, "
                          "dashboard data, migrations Alembic, rate-limit, invitations e-mail SMTP.")

# Rate-limiting (slowapi) : le limiter est défini dans .extensions (pas d'import circulaire).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Request-ID"],
)
app.add_middleware(SlowAPIMiddleware)
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


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """Healthcheck approfondi : ping DB réel (SELECT 1). 200/ok si DB up, 503/degraded sinon."""
    db_ok, detail = False, ""
    try:
        db.execute(text("SELECT 1")).first()
        db_ok = True
    except Exception as e:
        detail = f"{type(e).__name__}: {str(e)[:180]}"
    status_str = "ok" if db_ok else "degraded"
    return JSONResponse(
        {"status": status_str, "service": "revpulse-api",
         "db": settings.database_url.split("///")[-1], "db_ok": db_ok, "detail": detail,
         "env": settings.env},
        status_code=200 if db_ok else 503,
    )


# Routers métier
for r in (auth.router, tenants.router, members.router, approvals.router, dashboard.router, billing.router,
          content.router, linkedin.router):
    app.include_router(r)


# Frontend statique : sert le dashboard (index.html) si le dossier existe.
# En local : on monte ./frontend_public ; en docker : monté par le build.
_FE_DIR = os.environ.get("REVPULSE_FRONTEND_DIR", "./frontend_public")
if os.path.isdir(_FE_DIR):
    app.mount("/", StaticFiles(directory=_FE_DIR, html=True), name="frontend")
