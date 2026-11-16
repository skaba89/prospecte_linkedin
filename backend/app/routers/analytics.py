"""Analytics & reporting (P14). KPIs CRM + e-mails + sync LinkedIn, read-only.

Routes (préfixe /api, scopées par tenant via get_tenant) :
  GET /tenants/{tid}/analytics/overview   → KPIs agrégés (contacts/opps/emails/sync)
  GET /tenants/{tid}/analytics/pipeline   → opportunités par stage (count+value+avg proba) + win rate
  GET /tenants/{tid}/analytics/emails     → e-mails par template + par statut + taux de succès
  GET /tenants/{tid}/analytics/sources    → contacts par source + conversion
  GET /tenants/{tid}/analytics/export     → export CSV des contacts (RBAC crm.view — données PII)

Agrégations SQLAlchemy (func.count/sum/avg + group_by). Read-only (aucune mutation, aucun audit —
les analytics ne modifient pas l'état). RGPD : export CSV = données contacts → RBAC crm.view ;
les 4 autres endpoints ne renvoient que des agrégats (pas de PII individuelle) → get_tenant suffit.

Capitalise sur P11 (CRM), P12 (sync), P13 (e-mails) : tourne la donnée en insight actionnable
(conversion par source, efficacité e-mail, valeur pipeline, fraîcheur sync).
"""
import csv
import io
import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_tenant, require_permission

router = APIRouter(prefix="/api", tags=["analytics"])
_log = logging.getLogger("revpulse.analytics")

# Statuts considérés « convertis » pour le taux de conversion par source.
_CONVERTED_STATUSES = {"won", "contacted", "qualified"}


@router.get("/tenants/{tid}/analytics/overview", response_model=schemas.AnalyticsOverview)
def overview(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Vue d'ensemble KPIs (P14) : contacts (total/source/status), opportunités (total/stage/value),
    e-mails (total/status), sync LinkedIn (dernière run + total runs). Agrégats seulement."""
    contacts_total = db.query(func.count(models.Contact.id)).filter_by(tenant_id=tid).scalar() or 0
    contacts_by_source = dict(db.query(models.Contact.source, func.count(models.Contact.id))
                              .filter_by(tenant_id=tid).group_by(models.Contact.source).all())
    contacts_by_status = dict(db.query(models.Contact.status, func.count(models.Contact.id))
                              .filter_by(tenant_id=tid).group_by(models.Contact.status).all())
    opps_total = db.query(func.count(models.Opportunity.id)).filter_by(tenant_id=tid).scalar() or 0
    opps_by_stage = dict(db.query(models.Opportunity.stage, func.count(models.Opportunity.id))
                         .filter_by(tenant_id=tid).group_by(models.Opportunity.stage).all())
    pipeline_value = db.query(func.sum(models.Opportunity.value)).filter_by(tenant_id=tid).scalar() or 0
    emails_total = (db.query(func.count(models.Notification.id))
                    .filter(models.Notification.tenant_id == tid,
                            models.Notification.type == "email").scalar() or 0)
    emails_by_status = dict(db.query(models.Notification.status, func.count(models.Notification.id))
                            .filter(models.Notification.tenant_id == tid,
                                    models.Notification.type == "email")
                            .group_by(models.Notification.status).all())
    last_sync = (db.query(models.LinkedInSyncRun).filter_by(tenant_id=tid)
                 .order_by(models.LinkedInSyncRun.started_at.desc()).first())
    sync_runs_total = (db.query(func.count(models.LinkedInSyncRun.id))
                       .filter_by(tenant_id=tid).scalar() or 0)
    return schemas.AnalyticsOverview(
        contacts_total=contacts_total, contacts_by_source=contacts_by_source,
        contacts_by_status=contacts_by_status, opportunities_total=opps_total,
        opportunities_by_stage=opps_by_stage, pipeline_value=pipeline_value or 0,
        emails_total=emails_total, emails_by_status=emails_by_status,
        last_sync_at=(last_sync.completed_at or last_sync.started_at) if last_sync else None,
        last_sync_status=last_sync.status if last_sync else None,
        last_sync_imported=last_sync.imported if last_sync else 0,
        last_sync_total=last_sync.total if last_sync else 0,
        sync_runs_total=sync_runs_total)


@router.get("/tenants/{tid}/analytics/pipeline", response_model=schemas.AnalyticsPipeline)
def pipeline(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Pipeline agrégé par stage (count + total_value + avg probability) + win rate (closed_won/total)."""
    rows = (db.query(models.Opportunity.stage,
                     func.count(models.Opportunity.id),
                     func.sum(models.Opportunity.value),
                     func.avg(models.Opportunity.probability))
            .filter_by(tenant_id=tid).group_by(models.Opportunity.stage).all())
    stages = [schemas.PipelineStageStat(
        stage=r[0], count=r[1] or 0, total_value=r[2] or 0,
        avg_probability=round(float(r[3] or 0), 1)) for r in rows]
    total = sum(s.count for s in stages)
    won = next((s.count for s in stages if s.stage == "closed_won"), 0)
    total_value = sum(s.total_value for s in stages)
    return schemas.AnalyticsPipeline(
        stages=stages, total_opportunities=total,
        win_rate_pct=round(won / total * 100, 1) if total else 0.0,
        total_value=total_value)


@router.get("/tenants/{tid}/analytics/emails", response_model=schemas.AnalyticsEmails)
def emails(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Stats e-mail (P14) : par template (count + breakdown status) + global by_status + success rate.
    Success = (sent + dry_run) / (sent + dry_run + failed) — dry_run = envoi simulé OK (smtp off)."""
    tpl_rows = (db.query(models.Notification.template_id, models.Notification.status,
                         func.count(models.Notification.id))
                .filter(models.Notification.tenant_id == tid,
                        models.Notification.type == "email")
                .group_by(models.Notification.template_id, models.Notification.status).all())
    by_template_map: dict[str | None, dict[str, int]] = {}
    for tpl, st, cnt in tpl_rows:
        by_template_map.setdefault(tpl, {})[st] = cnt
    by_template = [schemas.EmailStat(template_id=k, count=sum(v.values()), by_status=v)
                   for k, v in by_template_map.items()]
    by_status = dict(db.query(models.Notification.status, func.count(models.Notification.id))
                     .filter(models.Notification.tenant_id == tid,
                             models.Notification.type == "email")
                     .group_by(models.Notification.status).all())
    sent = by_status.get("sent", 0) + by_status.get("dry_run", 0)
    failed = by_status.get("failed", 0)
    total = sum(by_status.values())
    return schemas.AnalyticsEmails(
        by_template=by_template, by_status=by_status,
        success_rate_pct=round(sent / (sent + failed) * 100, 1) if (sent + failed) else 0.0,
        total=total)


@router.get("/tenants/{tid}/analytics/sources", response_model=schemas.AnalyticsSources)
def sources(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Contacts par source + taux de conversion (% won/contacted/qualified par source).
    Mesure l'efficacité de chaque canal (LinkedIn vs manuel vs import)."""
    rows = (db.query(models.Contact.source, models.Contact.status, func.count(models.Contact.id))
            .filter_by(tenant_id=tid).group_by(models.Contact.source, models.Contact.status).all())
    by_src: dict[str, dict[str, int]] = {}
    for src, st, cnt in rows:
        by_src.setdefault(src, {})[st] = cnt
    stats = []
    for src, status_counts in by_src.items():
        total_src = sum(status_counts.values())
        converted = sum(status_counts.get(s, 0) for s in _CONVERTED_STATUSES)
        stats.append(schemas.SourceStat(
            source=src, count=total_src,
            conversion_pct=round(converted / total_src * 100, 1) if total_src else 0.0))
    return schemas.AnalyticsSources(by_source=stats, total=sum(s.count for s in stats))


@router.get("/tenants/{tid}/analytics/export")
def export_contacts(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant),
                    membership=Depends(require_permission("crm.manage"))):
    """Export CSV des contacts du tenant (P14). RBAC crm.manage (données PII — emails).
    Colonnes : id, first_name, last_name, email, phone, title, status, source, score,
    linkedin_url, created_at. Échappe les virgules via le module csv (pas de concaténation manuelle)."""
    contacts = (db.query(models.Contact).filter_by(tenant_id=tid)
                .order_by(models.Contact.created_at.desc()).all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "first_name", "last_name", "email", "phone", "title",
                "status", "source", "score", "linkedin_url", "created_at"])
    for c in contacts:
        w.writerow([c.id, c.first_name, c.last_name, c.email, c.phone, c.title,
                    c.status, c.source, getattr(c, "score", 0) or 0,
                    getattr(c, "linkedin_url", "") or "",
                    c.created_at.isoformat() if c.created_at else ""])
    csv_data = buf.getvalue()
    return Response(
        content=csv_data, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="contacts_{tid}.csv"',
                 "Content-Length": str(len(csv_data.encode("utf-8")))})
