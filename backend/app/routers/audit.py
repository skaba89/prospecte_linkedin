"""Audit log : lecture de la trace immuable des actions mutantes d'un tenant.

Endpoints (préfixe /api/tenants/{tid}/audit) :
  GET /          — liste paginée + filtrable (action prefix, actor_user_id, target_type,
                    since/until, ip). Plus récent d'abord. X-Total-Count reflète le total filtré.
  GET /stats     — total + top actions (group by action) + top acteurs (group by actor_name).
                    Résumé compliance du dashboard audit.
  GET /export    — export CSV des entrées filtrées (mêmes filtres que la liste, sans pagination).
                    Colonnes : created_at, actor, action, target, detail, request_id, ip, user_agent.
  GET /export-pdf — export PDF signé (P53) : entrées filtrées + footer HMAC-SHA256 (non-répudiation).
  GET /alerts    — alertes compliance (P53) : IPs avec burst d'actions (>N/heure) + actions sans acteur.
  POST /purge    — purge de rétention (P53) : supprime les entries > tenant.retention_days + log meta.

RBAC : workspace.manage (Owner/admin) — seul le propriétaire de l'espace voit l'historique complet
des actions (P3). La trace est immuable sauf POST /purge (rétention compliance, loggée elle-même).
"""
import csv
import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total

router = APIRouter(prefix="/api/tenants/{tid}/audit", tags=["audit"])


def _apply_filters(q, *, action: str | None, actor_user_id: str | None,
                   target_type: str | None, since: datetime | None, until: datetime | None,
                   ip: str | None = None):
    """Applique les filtres optionnels à une query AuditLog. action = prefix match (LIKE 'x%')
    pour filtrer une famille (ex. « crm.contact » → create/update/delete/import). Les autres
    sont exact match. since/until = bornes created_at incluses. ip = exact match (P52 SOC2)."""
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
    if ip:
        q = q.filter(models.AuditLog.ip == ip)
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
    ip: str | None = Query(None, description="Filtrer par IP source (P52 SOC2 — investigation « qui depuis où »)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Trace filtrée des actions mutantes du tenant. Plus récent d'abord. X-Total-Count = total
    filtré (pas le total absolu). Accès Owner (workspace.manage). Paginée."""
    q = _apply_filters(
        db.query(models.AuditLog).filter_by(tenant_id=tid),
        action=action, actor_user_id=actor_user_id, target_type=target_type,
        since=since, until=until, ip=ip,
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
    ip: str | None = Query(None, description="Filtrer par IP source (P52)"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Export CSV des entrées audit filtrées (mêmes filtres que la liste, sans pagination — export
    complet). Colonnes : created_at, actor_name, actor_user_id, action, target_type, target_id,
    detail (JSON), request_id, ip, user_agent (P52 SOC2/GDPR). Content-Disposition: attachment. RBAC workspace.manage."""
    q = _apply_filters(
        db.query(models.AuditLog).filter_by(tenant_id=tid),
        action=action, actor_user_id=actor_user_id, target_type=target_type,
        since=since, until=until, ip=ip,
    ).order_by(models.AuditLog.created_at.desc())
    rows = q.all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "actor_name", "actor_user_id", "action",
                "target_type", "target_id", "detail", "request_id", "ip", "user_agent"])
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
            r.ip or "",
            r.user_agent or "",
        ])
    csv_data = buf.getvalue()
    return Response(
        content=csv_data, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_{tid}.csv"',
                 "Content-Length": str(len(csv_data.encode("utf-8")))},
    )


# ════════════════════════════════════════════════════════════════════════════
# P53 — Compliance-grade : export PDF signé + alertes + purge de rétention
# ════════════════════════════════════════════════════════════════════════════

def _audit_hmac_key() -> bytes:
    """Clé HMAC dérivée du token_encryption_key du serveur (non-répudiation : signature vérifiable
    offline par quiconque possède la clé serveur). Fallback test si clé vide."""
    base = settings.token_encryption_key or "revpulse-audit-default-key-p53"
    return hashlib.sha256(("audit:" + base).encode()).digest()


def _audit_signature(entries: list[dict]) -> str:
    """HMAC-SHA256 sur le payload canonique (JSON trié des entries) → signature tamper-evident.
    Toute modification d'une entry change la signature (détection de falsification)."""
    payload = json.dumps(entries, sort_keys=True, default=str, ensure_ascii=False)
    return hmac.new(_audit_hmac_key(), payload.encode(), hashlib.sha256).hexdigest()


@router.get("/export-pdf")
def export_audit_pdf(
    tid: str,
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    target_type: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    ip: str | None = Query(None),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """GET /audit/export-pdf : export PDF signé (P53) — non-répudiation compliance.
    Génère un PDF avec les entries filtrées + footer HMAC-SHA256 (signature tamper-evident sur le
    payload canonique). Le vérifieur (auditeur SOC2) recomput l'HMAC sur le JSON des entries +
    compare au footer du PDF → détecte toute falsification. RBAC workspace.manage."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    q = _apply_filters(
        db.query(models.AuditLog).filter_by(tenant_id=tid),
        action=action, actor_user_id=actor_user_id, target_type=target_type,
        since=since, until=until, ip=ip,
    ).order_by(models.AuditLog.created_at.desc())
    rows = q.all()

    # Payload canonique pour la signature (entries simplifiées + triées).
    entries_canonical = [{
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "actor": r.actor_name or "", "actor_user_id": r.actor_user_id or "",
        "action": r.action, "target_type": r.target_type or "", "target_id": r.target_id or "",
        "ip": r.ip or "", "request_id": r.request_id or "",
    } for r in rows]
    signature = _audit_signature(entries_canonical)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0a66c2"))
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    elems = [
        Paragraph(f"Audit Log — {_t.name}", h1),
        Spacer(1, 4 * mm),
        Paragraph(f"Tenant: <b>{tid}</b> · Exporté le {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
                  f"{len(rows)} entrées · Filtres: action={action or '—'} actor={actor_user_id or '—'} "
                  f"target={target_type or '—'} ip={ip or '—'} since={since or '—'} until={until or '—'}", small),
        Spacer(1, 3 * mm),
    ]
    table_data = [["Date", "Acteur", "Action", "Cible", "IP"]] + [
        [(r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
         (r.actor_name or "—"), r.action, (r.target_type or "—"), (r.ip or "—")]
        for r in rows[:500]  # cap 500 rows dans le PDF (au-delà → CSV)
    ]
    tbl = Table(table_data, colWidths=[35 * mm, 30 * mm, 40 * mm, 30 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a66c2")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elems.append(tbl)
    if len(rows) > 500:
        elems.append(Spacer(1, 2 * mm))
        elems.append(Paragraph(f"(PDF limité à 500 entrées — {len(rows)} au total. Export CSV pour la trace complète.)", small))
    elems.append(Spacer(1, 6 * mm))
    elems.append(Paragraph(
        f"<b>Signature HMAC-SHA256 (non-répudiation) :</b> <font face='Courier'>{signature}</font><br/>"
        f"Vérification : recomputer HMAC-SHA256(clé serveur, JSON canonique trié des entries) + comparer. "
        f"Toute modification d'une entry invalide la signature (tamper-evident).", small))
    doc.build(elems)
    pdf_data = buf.getvalue()
    return Response(
        content=pdf_data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit_{tid}_signed.pdf"',
                 "Content-Length": str(len(pdf_data))},
    )


@router.get("/alerts", response_model=schemas.AuditAlertsOut)
def audit_alerts(
    tid: str,
    window_hours: int = Query(1, ge=1, le=168, description="Fenêtre d'analyse en heures (défaut 1h)"),
    burst_threshold: int = Query(10, ge=2, description="Seuil d'alerte : IP avec >N actions dans la fenêtre"),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """GET /audit/alerts : alertes compliance (P53) — détecte anomalies sur la fenêtre analysée.
    - ip_burst : IP avec >burst_threshold actions dans window_hours (potentiel brute-force / scan).
    - no_actor : actions sans actor_user_id (système/anonyme — vérifier légitimité).
    Sévérité : high (>5x seuil), medium (>seuil), low (actions système isolées). RBAC workspace.manage."""
    since = datetime.utcnow() - timedelta(hours=window_hours)
    base = db.query(models.AuditLog).filter_by(tenant_id=tid).filter(models.AuditLog.created_at >= since)

    # Burst par IP (IP non vide)
    ip_counts = (base.with_entities(models.AuditLog.ip, func.count(models.AuditLog.id).label("c"))
                 .filter(models.AuditLog.ip != "")
                 .group_by(models.AuditLog.ip).having(func.count(models.AuditLog.id) > burst_threshold)
                 .order_by(func.count(models.AuditLog.id).desc()).limit(20).all())
    # Actions sans acteur (système/anonyme)
    no_actor = (base.filter(models.AuditLog.actor_user_id.is_(None))
               .with_entities(models.AuditLog.action, func.count(models.AuditLog.id).label("c"))
               .group_by(models.AuditLog.action).order_by(func.count(models.AuditLog.id).desc()).limit(10).all())

    alerts = []
    for ip_addr, c in ip_counts:
        sev = "high" if c > burst_threshold * 5 else "medium"
        alerts.append(schemas.AuditAlertItem(kind="ip_burst", severity=sev, label=ip_addr, count=c,
                                             detail={"threshold": burst_threshold, "window_hours": window_hours}))
    for act, c in no_actor:
        alerts.append(schemas.AuditAlertItem(kind="no_actor", severity="low", label=act, count=c,
                                             detail={"window_hours": window_hours}))
    return schemas.AuditAlertsOut(window_hours=window_hours, total_alerts=len(alerts), alerts=alerts)


@router.post("/purge", response_model=schemas.PurgeResult)
def purge_audit(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """POST /audit/purge : purge de rétention (P53) — supprime les entries > tenant.retention_days.
    La purge elle-même est loggée comme une entry meta (action="audit.purge", detail={purged, before})
    AVANT suppression des entries anciennes (donc la meta survit). RBAC workspace.manage.
    Exception à l'immutabilité : la rétention compliance (SOC2/GDPR) nécessite une purge contrôlée + loggée."""
    from ..audit import log_audit
    cutoff = datetime.utcnow() - timedelta(days=_t.retention_days)
    old_q = db.query(models.AuditLog).filter_by(tenant_id=tid).filter(models.AuditLog.created_at < cutoff)
    purged = old_q.count()
    remaining_before = db.query(models.AuditLog).filter_by(tenant_id=tid).count()
    # Log meta de la purge (AVANT suppression — la meta entry est récente donc survit)
    log_audit(db, tenant_id=tid, actor_user_id=_perm.user_id, actor_name=_perm.name,
              action="audit.purge", target_type="audit_log", target_id=tid,
              detail={"purged": purged, "retention_days": _t.retention_days,
                      "purged_before": cutoff.isoformat(), "remaining_before": remaining_before})
    old_q.delete(synchronize_session=False)
    db.commit()
    remaining = db.query(models.AuditLog).filter_by(tenant_id=tid).count()
    return schemas.PurgeResult(purged=purged, retention_days=_t.retention_days,
                               purged_before=cutoff, remaining=remaining)
