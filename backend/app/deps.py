"""Dépendances FastAPI : auth JWT, résolution tenant, contrôle d'accès RBAC."""
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models, security
from .database import get_db
from .logging_config import tenant_id_var, user_id_var

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# Matrice de permissions par rôle (lecture seule — cohérente avec le frontend)
ROLE_PERMISSIONS = {
    "Owner": {"members.manage", "billing.manage", "workspace.manage", "approvals.manage", "content.publish", "crm.manage", "ai.use"},
    "Admin": {"members.manage", "approvals.manage", "content.publish", "crm.manage", "ai.use"},
    "Editor": {"approvals.manage", "content.publish", "crm.manage", "ai.use"},
    "Contributor": {"content.publish", "ai.use"},
    "Viewer": set(),
}


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié", headers={"WWW-Authenticate": "Bearer"})
    payload = security.decode_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalide ou expiré")
    # P10.7 : vérifie la révocation JIT (blacklist revoked_tokens). Les tokens sans jti
    # (legacy, antérieurs à P10) restent valides — seuls les nouveaux tokens sont révocables.
    jti = payload.get("jti")
    if jti and db.query(models.RevokedToken).filter_by(jti=jti).first():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token révoqué")
    user = db.query(models.User).filter_by(id=payload.get("sub")).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur introuvable")
    user_id_var.set(user.id)
    return user


def get_tenant(tid: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)) -> models.Tenant:
    """Résout un tenant et vérifie que l'utilisateur en est membre (ou owner)."""
    tenant = db.query(models.Tenant).filter_by(id=tid).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Espace introuvable")
    membership = db.query(models.Member).filter_by(tenant_id=tid, user_id=user.id).first()
    if not membership and tenant.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé à cet espace")
    tenant_id_var.set(tenant.id)
    return tenant


def get_membership(tenant: models.Tenant = Depends(get_tenant), user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> models.Member:
    """Retourne la ligne de membership de l'utilisateur courant sur le tenant."""
    m = db.query(models.Member).filter_by(tenant_id=tenant.id, user_id=user.id).first()
    if m:
        return m
    # owner sans ligne explicite
    return models.Member(id=f"mem-owner-{uuid.uuid4().hex[:6]}", tenant_id=tenant.id, user_id=user.id,
                         email=user.email, name=user.name, role="Owner", status="active")


def require_permission(perm: str):
    def _dep(membership: models.Member = Depends(get_membership)) -> models.Member:
        # P10.8 : un membre inactif (invitation en attente / expirée / suspendu) n'a aucune permission.
        if membership.status != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Membre inactif ou invitation en attente.")
        if perm not in ROLE_PERMISSIONS.get(membership.role, set()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Permission requise : {perm} (rôle {membership.role})")
        return membership
    return _dep
