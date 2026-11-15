"""Point d'entrée FastAPI : CORS, routers, seed au démarrage, montage static du frontend."""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import SessionLocal, init_db
from .routers import approvals, auth, billing, content, dashboard, linkedin, members, tenants
from .seed import seed_all

app = FastAPI(title="RevPulse API", version="1.0.0",
              description="Backend multi-tenant du LinkedIn Command Center — auth JWT, RBAC, dashboard data.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


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
    return {"status": "ok", "service": "revpulse-api", "db": settings.database_url.split("///")[-1]}


# Routers métier
for r in (auth.router, tenants.router, members.router, approvals.router, dashboard.router, billing.router,
          content.router, linkedin.router):
    app.include_router(r)


# Frontend statique : sert le dashboard (index.html) si le dossier existe.
# En local : on monte ./frontend_public ; en docker : monté par le build.
_FE_DIR = os.environ.get("REVPULSE_FRONTEND_DIR", "./frontend_public")
if os.path.isdir(_FE_DIR):
    app.mount("/", StaticFiles(directory=_FE_DIR, html=True), name="frontend")
