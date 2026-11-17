"""Audit log : lecture de la trace immuable des actions mutantes d'un tenant.

Endpoints (préfixe /api/tenants/{tid}/audit) :
  GET /          — liste paginée + filtrable (action prefix, actor_user_id, target_type,
                    since/until). Plus récent d'abord. X-Total-Count reflète le total filtré.
  GET /stats     — total + top actions (group by action) + top acteurs (group by actor_name).
                    Résumé compliance du dashboard audit.
  GET /export    — export CSV des entrées filtrées (mêmes filtres que la liste, sans pagination).
                    Content-Disposition: attachment.

RBAC : workspace.manage (Owner/admin) — seul le propriétaire de l'espace voit l'historique complet
des actions (P3). La trace est immuable (aucun endpoint d'écriture ici — alimentée par audit.log_audit
dans les routers mutantes).
"""
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/audit", tags=["audit"])


def _apply_filters(q, *, action: str | None, actor_user_id: str | None,
                   target_type: str | None, since: datetime | None, until: datetime | None):
    """Applique les filtres optionnels à une query AuditLog. action = prefix match (LIKE 'x%')
    pour filtrer une famille (ex. « crm.contact » → create/update/delete/import). Les autres
    sont exact match. since/until = bornes created_at incluses."""
    if action:
        q = q.filter(models.AuditLog.action.like(f"{action}%"))
    if actor_user_id:
        q = q.filter(models.AuditLog.actor_user_id == actor_user_id)
    if target_type:
        q = q.filter(models.AuditLog.target_type == target_type)
    if since:
        q = q.filter(models.AuditLog.created_at >= since)
    if until:
        q = q.filter(models.AuditLog.created_at <= until)
    return q


@router.get("", response_model=list[schemas.AuditLogOut])
def list_audit(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    action: str | None = Query(None, description="Préfixe d'action (ex. crm.contact → tous les actions contact)"),
    actor_user_id: str | None = Query(None, description="Filtrer par acteur (user id)"),
    target_type: str | None = Query(None, description="Filtrer par type de cible (contact/opportunity/webhook…)"),
    since: datetime | None = Query(None, description="Borne inférieure created_at (ISO 8601)"),
    until: datetime | None = Query(None, description="Borne supérieure created_at (ISO 8601)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Trace filtrée des actions mutantes du tenant. Plus récent d'abord. X-Total-Count = total
    filtré (pas le total absolu). Accès Owner (workspace.manage). Paginée."""
    q = _apply_filters(
        db.query(models.AuditLog).filter_by(tenant_id=tid),
        action=action, actor_user_id=actor_user_id, target_type=target_type,
        since=since, until=until,
    ).order_by(models.AuditLog.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.get("/stats", response_model=schemas.AuditStatsOut)
def audit_stats(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Stats agrégées de l'audit : total des entrées + top 10 actions (group by action) + top 10
    acteurs (group by actor_name non-vide). Tri décroissant par count. Résumé compliance."""
    base = db.query(models.AuditLog).filter_by(tenant_id=tid)
    total = base.count()
    by_action = (
        base.with_entities(models.AuditLog.action, func.count(models.AuditLog.id).label("c"))
        .group_by(models.AuditLog.action)
        .order_by(func.count(models.AuditLog.id).desc())
        .limit(10)
        .all()
    )
    by_actor = (
        base.with_entities(models.AuditLog.actor_name, func.count(models.AuditLog.id).label("c"))
        .filter(models.AuditLog.actor_name != "")
        .group_by(models.AuditLog.actor_name)
        .order_by(func.count(models.AuditLog.id).desc())
        .limit(10)
        .all()
    )
    return schemas.AuditStatsOut(
        total=total,
        by_action=[schemas.AuditCountItem(label=a, count=c) for a, c in by_action],
        by_actor=[schemas.AuditCountItem(label=n, count=c) for n, c in by_actor],
    )


@router.get("/export")
def export_audit(
    tid: str,
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    target_type: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Export CSV des entrées audit filtrées (mêmes filtres que la liste, sans pagination — export
    complet). Colonnes : created_at, actor_name, actor_user_id, action, target_type, target_id,
    detail (JSON), request_id. Content-Disposition: attachment. RBAC workspace.manage."""
    q = _apply_filters(
        db.query(models.AuditLog).filter_by(tenant_id=tid),
        action=action, actor_user_id=actor_user_id, target_type=target_type,
        since=since, until=until,
    ).order_by(models.AuditLog.created_at.desc())
    rows = q.all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "actor_name", "actor_user_id", "action",
                "target_type", "target_id", "detail", "request_id"])
    for r in rows:
        w.writerow([
            r.created_at.isoformat() if r.created_at else "",
            r.actor_name or "",
            r.actor_user_id or "",
            r.action,
            r.target_type or "",
            r.target_id or "",
            json.dumps(r.detail, ensure_ascii=False) if r.detail is not None else "",
            r.request_id or "",
        ])
    csv_data = buf.getvalue()
    return Response(
        content=csv_data, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_{tid}.csv"',
                 "Content-Length": str(len(csv_data.encode("utf-8")))},
    )
