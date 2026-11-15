"""Audit log : trace immuable des actions mutantes d'un tenant.

log_audit() est appelée par les routers après une mutation réussie (avant le
commit du router), dans la même session/transaction → atomique avec la mutation.
Si la mutation échoue (exception avant commit), aucune ligne audit n'est écrite
(cohérent : rien ne s'est passé). L'acteur est l'utilisateur courant (via le
membership) ; le request_id P3 est capturé pour corrélation logs ↔ audit.

audit_logs.tenant_id est un String indexé SANS FK étrangère : la trace doit
survivre à la suppression du tenant (une audit log outlive l'entité auditée).
"""
from typing import Any

from sqlalchemy.orm import Session

from . import models
from .logging_config import request_id_var


def log_audit(db: Session, *, tenant_id: str, actor_user_id: str | None, actor_name: str,
              action: str, target_type: str = "", target_id: str = "",
              detail: dict[str, Any] | list | None = None) -> models.AuditLog:
    """Crée une ligne AuditLog dans la session courante (flush, pas commit).
    Le router appelant commit (atomique avec la mutation)."""
    entry = models.AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, actor_name=actor_name,
        action=action, target_type=target_type, target_id=target_id,
        detail=detail, request_id=request_id_var.get(""),
    )
    db.add(entry)
    db.flush()
    return entry
