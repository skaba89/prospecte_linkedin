"""Content authoring : soumet un nouveau contenu dans la file d'approbation.

Boucle produit : un membre avec la permission `content.publish` compose un
post/DM/commentaire → ça crée une ligne `Approval` en status='pending' → un
approuveur (permission `approvals.manage`) l'approuve ou la refuse (voir
approvals.py) → une fois approuvée, elle peut être publiée sur LinkedIn
(voir linkedin.py).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api/tenants/{tid}/content", tags=["content"])

_TYPE_META = {"post": ("Post", "#0a66c2"), "dm": ("DM", "#635bff"), "comment": ("Comment.", "#10b981")}
_PRIO_META = {"high": ("Haute", "#ef4444"), "medium": ("Normale", "#635bff"), "low": ("Basse", "#64748b")}


@router.post("", response_model=schemas.ApprovalOut, status_code=status.HTTP_201_CREATED)
def create_content(tid: str, body: schemas.ContentCreate, db: Session = Depends(get_db),
                   _t: models.Tenant = Depends(get_tenant),
                   membership=Depends(require_permission("content.publish"))):
    """Soumet un nouveau contenu (post/DM/commentaire) dans la file d'approbation."""
    if body.type not in _TYPE_META:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Type invalide : {body.type} (post | dm | comment)")
    if body.priority not in _PRIO_META:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Priorité invalide : {body.priority} (high | medium | low)")
    tlabel, tcolor = _TYPE_META[body.type]
    plabel, pcolor = _PRIO_META[body.priority]
    a = models.Approval(
        tenant_id=tid, type=body.type, title=body.title,
        submitter=body.submitter or membership.name, submitted_ago="à l'instant",
        priority=body.priority, status="pending", risk_flags="Aucun",
        summary=body.summary or body.title[:140], content_body=body.content_body,
        type_label=tlabel, type_color=tcolor, priority_label=plabel, priority_color=pcolor,
        status_label="En attente", status_color="#f59e0b", approved=False,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a
