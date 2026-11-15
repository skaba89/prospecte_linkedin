"""Seed démo idempotent.

- Charge les 34 modules de dashboard depuis seed_data.json (générés par GLM).
- Crée (ou répare) le compte démo + le workspace RevPulse Demo + Acme Growth,
  les membres (dont une invitation en attente) et la file d'approbation.
Appelé au démarrage ET par /api/auth/demo (garantie de disposer du compte).
"""
import json
import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import models, security
from .config import settings

_HERE = os.path.dirname(__file__)
_SEED_PATH = os.path.join(_HERE, "seed_data.json")

# File d'approbation seed (cohérente avec le module team_workspace du frontend)
_SEED_APPROVALS = [
    {"type": "post", "title": "Article sur l'automatisation des ventes", "submitter": "Sophie Dubois",
         "submitted_ago": "il y a 2h", "priority": "high", "risk_flags": "Aucun",
         "summary": "Post de thought-leadership (1 200 mots) sur l'automatisation du pipeline sales.",
         "type_label": "Post", "type_color": "#0a66c2", "priority_label": "Haute", "priority_color": "#ef4444",
         "status_label": "En attente", "status_color": "#f59e0b"},
    {"type": "dm", "title": "Séquence cold outreach comptes PME", "submitter": "Julien Leroy",
         "submitted_ago": "il y a 5h", "priority": "medium", "risk_flags": "Limite DM/heure proche",
         "summary": "Séquence de 3 messages vers 40 comptes PME identifiés par le filtre secteur.",
         "type_label": "DM", "type_color": "#635bff", "priority_label": "Normale", "priority_color": "#635bff",
         "status_label": "En attente", "status_color": "#f59e0b"},
    {"type": "comment", "title": "Réponses automatiques aux commentaires", "submitter": "Sophie Dubois",
         "submitted_ago": "il y a 1j", "priority": "low", "risk_flags": "Aucun",
         "summary": "8 réponses générées aux commentaires du dernier post viral.",
         "type_label": "Comment.", "type_color": "#10b981", "priority_label": "Basse", "priority_color": "#64748b",
         "status_label": "En attente", "status_color": "#f59e0b"},
    {"type": "post", "title": "Carrousel benchmark sectoriel Q4", "submitter": "Julien Leroy",
         "submitted_ago": "il y a 1j", "priority": "high", "risk_flags": "Chiffres concurrents cités",
         "summary": "Carrousel 10 slides reprenant le benchmark sectoriel (revshare, CAC, NPS).",
         "type_label": "Post", "type_color": "#0a66c2", "priority_label": "Haute", "priority_color": "#ef4444",
         "status_label": "En attente", "status_color": "#f59e0b"},
    {"type": "dm", "title": "Relance prospects salons virtuels", "submitter": "Sophie Dubois",
         "submitted_ago": "il y a 2j", "priority": "medium", "risk_flags": "Aucun",
         "summary": "Relance personnalisée de 22 prospects issus du salon B2B Forum.",
         "type_label": "DM", "type_color": "#635bff", "priority_label": "Normale", "priority_color": "#635bff",
         "status_label": "En attente", "status_color": "#f59e0b"},
    {"type": "post", "title": "Étude de cas client (anonymisée)", "submitter": "Julien Leroy",
         "submitted_ago": "il y a 3j", "priority": "medium", "risk_flags": "Anonymisation à vérifier",
         "summary": "Cas client SaaS B2B : +38% de leads qualifiés en 90 jours.",
         "type_label": "Post", "type_color": "#0a66c2", "priority_label": "Normale", "priority_color": "#635bff",
         "status_label": "En attente", "status_color": "#f59e0b"},
]


def _load_modules() -> dict:
    with open(_SEED_PATH, encoding="utf-8") as f:
        return json.load(f)


def _seed_modules(db: Session, tenant_id: str, modules: dict) -> None:
    for key, payload in modules.items():
        existing = db.query(models.DashboardModule).filter_by(tenant_id=tenant_id, module_key=key).first()
        if existing:
            existing.payload = payload
        else:
            db.add(models.DashboardModule(id=f"mod-{uuid.uuid4().hex[:10]}", tenant_id=tenant_id,
                                          module_key=key, payload=payload))


def _seed_approvals(db: Session, tenant_id: str) -> None:
    existing = db.query(models.Approval).filter_by(tenant_id=tenant_id).count()
    if existing:
        return
    for a in _SEED_APPROVALS:
        db.add(models.Approval(id=f"apr-{uuid.uuid4().hex[:10]}", tenant_id=tenant_id, **a))


def ensure_demo(db: Session) -> models.User:
    """Crée (ou répare) le compte démo + ses deux workspaces. Idempotent."""
    email = settings.demo_email.lower()
    user = db.query(models.User).filter_by(email=email).first()
    if not user:
        user = models.User(id="usr-demo", email=email, name="Alex Démo",
                           password_hash=security.hash_password(settings.demo_password),
                           created_at=datetime.utcnow())
        db.add(user)
        db.flush()

    # Workspace 1 : RevPulse Demo (profil LinkedIn, membres, approbations, modules)
    t1 = db.query(models.Tenant).filter_by(id="tnt-demo").first()
    if not t1:
        t1 = models.Tenant(id="tnt-demo", name="RevPulse Demo", plan="Pro", owner_id=user.id,
                           created_at=datetime.utcnow())
        db.add(t1)
        db.flush()
    if not db.query(models.Profile).filter_by(tenant_id=t1.id).first():
        db.add(models.Profile(id=f"prf-{uuid.uuid4().hex[:10]}", tenant_id=t1.id, type="linkedin",
                              name="Camille Laurent", headline="Founder @ RevPulse · Sales Engagement SaaS",
                              company="RevPulse", followers=18450, initials="CL"))
    if not db.query(models.Member).filter_by(tenant_id=t1.id).first():
        now = datetime.utcnow()
        db.add(models.Member(id="mem-1", tenant_id=t1.id, user_id=user.id, email=user.email, name="Alex Démo",
                             role="Owner", status="active", invited_by="self", added_at=now - timedelta(days=30)))
        db.add(models.Member(id="mem-2", tenant_id=t1.id, user_id=None, email="sophie.dubois@revpulse.io",
                             name="Sophie Dubois", role="Admin", status="active", invited_by=user.id,
                             added_at=now - timedelta(days=22)))
        db.add(models.Member(id="mem-3", tenant_id=t1.id, user_id=None, email="julien.leroy@revpulse.io",
                             name="Julien Leroy", role="Editor", status="active", invited_by=user.id,
                             added_at=now - timedelta(days=14)))
        db.add(models.Member(id="mem-4", tenant_id=t1.id, user_id=None, email="ines.bernard@revpulse.io",
                             name="Inès Bernard", role="Viewer", status="invited", invited_by=user.id,
                             token="a8k2m9qx4f", added_at=now - timedelta(days=2)))
    _seed_approvals(db, t1.id)
    _seed_modules(db, t1.id, _load_modules())

    # Workspace 2 : Acme Growth (multi-tenant, profil à configurer)
    t2 = db.query(models.Tenant).filter_by(id="tnt-acme").first()
    if not t2:
        t2 = models.Tenant(id="tnt-acme", name="Acme Growth", plan="Enterprise", owner_id=user.id,
                           created_at=datetime.utcnow())
        db.add(t2)
        db.flush()
    if not db.query(models.Member).filter_by(tenant_id=t2.id).first():
        db.add(models.Member(id="mem-a", tenant_id=t2.id, user_id=user.id, email=user.email, name="Alex Démo",
                             role="Owner", status="active", invited_by="self",
                             added_at=datetime.utcnow() - timedelta(days=10)))

    db.commit()
    return user


def seed_all(db: Session) -> None:
    """Appelé au démarrage : garantit le compte démo (n'écrase rien d'existant)."""
    ensure_demo(db)
