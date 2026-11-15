"""Modèles SQLAlchemy multi-tenant.

Toute table « métier » (Profile, Member, Approval, DashboardModule) est scopée
par `tenant_id` → isolation par espace de travail. Les membres portent aussi un
`user_id` (nullable : null pour une invitation en attente non encore rattachée).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from .database import Base


class JSONType(TypeDecorator):
    """JSON portable : JSONB sous Postgres, JSON sinon (SQLite)."""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        import json
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):
        import json
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: _uid("usr"))
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String, primary_key=True, default=lambda: _uid("tnt"))
    name = Column(String, nullable=False)
    plan = Column(String, default="Starter", nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Profile(Base):
    """Profil connecté à l'espace (LinkedIn simulé ou compte RevPulse natif). 1 par tenant."""
    __tablename__ = "profiles"
    id = Column(String, primary_key=True, default=lambda: _uid("prf"))
    tenant_id = Column(String, ForeignKey("tenants.id"), unique=True, nullable=False)
    type = Column(String, default="native", nullable=False)  # linkedin | native
    name = Column(String, default="")
    headline = Column(String, default="")
    company = Column(String, default="")
    followers = Column(Integer, default=0)
    initials = Column(String, default="")


class Member(Base):
    """Ligne d'appartenance tenant x utilisateur (ou invitation en attente si user_id est null)."""
    __tablename__ = "members"
    id = Column(String, primary_key=True, default=lambda: _uid("mem"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, default="Viewer", nullable=False)
    status = Column(String, default="invited", nullable=False)  # active | invited
    token = Column(String, nullable=True)
    invited_by = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # invitation : date d'expiration (null = sans limite)


class Approval(Base):
    """Contenu en file d'approbation, scopé par tenant."""
    __tablename__ = "approvals"
    id = Column(String, primary_key=True, default=lambda: _uid("apr"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    type = Column(String, default="")
    title = Column(String, default="")
    submitter = Column(String, default="")
    submitted_ago = Column(String, default="")
    priority = Column(String, default="medium")
    status = Column(String, default="pending")
    risk_flags = Column(String, default="Aucun")
    summary = Column(Text, default="")
    content_body = Column(Text, default="")  # texte complet du post/DM (pour publication LinkedIn)
    type_label = Column(String, default="")
    type_color = Column(String, default="#635bff")
    priority_label = Column(String, default="Normal")
    priority_color = Column(String, default="#635bff")
    status_label = Column(String, default="En attente")
    status_color = Column(String, default="#f59e0b")
    approved = Column(Boolean, default=False, nullable=False)


class DashboardModule(Base):
    """Un module de dashboard (clé → payload JSON), scopé par tenant."""
    __tablename__ = "dashboard_modules"
    id = Column(String, primary_key=True, default=lambda: _uid("mod"))
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, index=True)
    module_key = Column(String, nullable=False)
    payload = Column(JSONType, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "module_key", name="uq_tenant_module"),)


class LinkedInConnection(Base):
    """Connexion LinkedIn OAuth d'un tenant (1 par tenant).

    ⚠️ access_token stocké en clair pour la démo — à chiffrer (Fernet) en production
    et ne JAMAIS exposer dans une réponse API (status renvoie seulement member_name/id).
    """
    __tablename__ = "linkedin_connections"
    id = Column(String, primary_key=True, default=lambda: _uid("lk"))
    tenant_id = Column(String, ForeignKey("tenants.id"), unique=True, nullable=False)
    access_token = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    member_id = Column(String, default="")
    member_name = Column(String, default="")
    member_headline = Column(String, default="")
    scope = Column(String, default="")
    connected_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Trace immuable des actions mutantes d'un tenant (qui a fait quoi, quand).

    Scopée par tenant_id (String indexé SANS FK → survit à la suppression du
    tenant, une audit log devant outliver l'entité auditée). Alimentée par
    audit.log_audit() dans les routers mutantes. Le request_id corrélationne
    avec les logs structurés P3.
    """
    __tablename__ = "audit_logs"
    id = Column(String, primary_key=True, default=lambda: _uid("aud"))
    tenant_id = Column(String, nullable=False, index=True)  # pas de FK (survie)
    actor_user_id = Column(String, nullable=True)  # null pour actions système (OAuth callback)
    actor_name = Column(String, default="", nullable=False)
    action = Column(String, nullable=False)  # ex: "approval.approve", "member.invite"
    target_type = Column(String, default="", nullable=False)
    target_id = Column(String, default="", nullable=False)
    detail = Column(JSONType, nullable=True)
    request_id = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
