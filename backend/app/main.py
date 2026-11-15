"""Point d'entrée FastAPI : CORS, rate-limit, routers, seed au démarrage, static du frontend."""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .config import settings
from .database import SessionLocal, init_db
from .extensions import limiter
from .routers import approvals, auth, billing, content, dashboard, linkedin, members, tenants
from .seed import seed_all

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
)
app.add_middleware(SlowAPIMiddleware)


@app.on_event("startup")
def _startup():
    init_db()
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "revpulse-api", "db": settings.database_url.split("///")[-1],
            "env": settings.env}


# Routers métier
for r in (auth.router, tenants.router, members.router, approvals.router, dashboard.router, billing.router,
          content.router, linkedin.router):
    app.include_router(r)


# Frontend statique : sert le dashboard (index.html) si le dossier existe.
# En local : on monte ./frontend_public ; en docker : monté par le build.
_FE_DIR = os.environ.get("REVPULSE_FRONTEND_DIR", "./frontend_public")
if os.path.isdir(_FE_DIR):
    app.mount("/", StaticFiles(directory=_FE_DIR, html=True), name="frontend")
