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
from sqlalchemy import extract, func
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


@router.get("/tenants/{tid}/analytics/funnel", response_model=schemas.AnalyticsFunnel)
def funnel(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Funnel de conversion pipeline (P24) : chemin prospecting→qualified→proposal→negotiation→
    closed_won. cumulative[i] = opps à ce stage + stages ultérieurs du chemin gagnant (closed_lost
    exclu — branche de perte). conversion_from_prev_pct = cumulative[i]/cumulative[i-1].
    drop_off = cumulative précédent - cumulative actuel. Conversion globale = won/entered."""
    stage_counts = dict(db.query(models.Opportunity.stage, func.count(models.Opportunity.id))
                        .filter_by(tenant_id=tid).group_by(models.Opportunity.stage).all())
    funnel_stages = schemas._FUNNEL_STAGES
    stages_out = []
    cumulative_prev = None
    for i, st in enumerate(funnel_stages):
        # cumulative = somme des counts des stages à partir de i dans le chemin gagnant
        cumulative = sum(stage_counts.get(s, 0) for s in funnel_stages[i:])
        count = stage_counts.get(st, 0)
        if i == 0:
            conv = 100.0 if cumulative else 0.0
            drop = 0
        else:
            conv = round(cumulative / cumulative_prev * 100, 1) if cumulative_prev else 0.0
            drop = cumulative_prev - cumulative
        stages_out.append(schemas.FunnelStage(
            stage=st, order=i, count=count, cumulative=cumulative,
            conversion_from_prev_pct=conv, drop_off=drop))
        cumulative_prev = cumulative
    total_entered = stages_out[0].cumulative if stages_out else 0
    total_won = stages_out[-1].cumulative if stages_out else 0
    overall = round(total_won / total_entered * 100, 1) if total_entered else 0.0
    return schemas.AnalyticsFunnel(
        stages=stages_out, total_entered=total_entered, total_won=total_won,
        overall_conversion_pct=overall)


@router.get("/tenants/{tid}/analytics/cohorts", response_model=schemas.AnalyticsCohorts)
def cohorts(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Cohortes par mois d'acquisition (P24) : opportunités groupées par created_at tronqué au mois
    (YYYY-MM). Pour chaque cohorte : by_stage (répartition actuelle), closed_won_count, win_rate,
    revenue_won (somme values won), avg_time_to_close_days (jours moyens created_at→closed_at pour
    les won). Ordonné par mois asc."""
    rows = (db.query(extract("year", models.Opportunity.created_at).label("y"),
                     extract("month", models.Opportunity.created_at).label("m"),
                     models.Opportunity.stage, models.Opportunity.value,
                     models.Opportunity.closed_at, models.Opportunity.created_at)
            .filter_by(tenant_id=tid).all())
    cmap: dict[str, dict] = {}
    for y, m, stage, value, closed_at, created_at in rows:
        month = f"{int(y):04d}-{int(m):02d}"
        c = cmap.setdefault(month, {"total": 0, "by_stage": {}, "won": 0, "won_value": 0, "deltas": []})
        c["total"] += 1
        c["by_stage"][stage] = c["by_stage"].get(stage, 0) + 1
        if stage == "closed_won":
            c["won"] += 1
            c["won_value"] += value or 0
            if closed_at and created_at:
                delta = (closed_at - created_at).days
                if delta >= 0:
                    c["deltas"].append(delta)
    cohorts = []
    for month in sorted(cmap.keys()):
        c = cmap[month]
        win_rate = round(c["won"] / c["total"] * 100, 1) if c["total"] else 0.0
        avg_ttc = round(sum(c["deltas"]) / len(c["deltas"]), 1) if c["deltas"] else None
        cohorts.append(schemas.CohortRow(
            month=month, total_opps=c["total"], by_stage=c["by_stage"],
            closed_won_count=c["won"], win_rate_pct=win_rate,
            revenue_won=c["won_value"], avg_time_to_close_days=avg_ttc))
    total_opps = sum(c.total_opps for c in cohorts)
    total_won = sum(c.closed_won_count for c in cohorts)
    avg_win = round(total_won / total_opps * 100, 1) if total_opps else 0.0
    return schemas.AnalyticsCohorts(
        cohorts=cohorts, total_opps=total_opps, total_won=total_won, avg_win_rate_pct=avg_win)


@router.get("/tenants/{tid}/analytics/attribution", response_model=schemas.AnalyticsAttribution)
def attribution(tid: str, db: Session = Depends(get_db), _t: models.Tenant = Depends(get_tenant)):
    """Attribution revenue par source d'acquisition (P24) : jointure Opportunity→Contact.source.
    Pour chaque source : opps_count, closed_won_count, revenue_won (somme values won), win_rate,
    avg_deal_size (revenue_won/won). Tri par revenue_won desc. best_source = top revenue."""
    rows = (db.query(models.Contact.source, models.Opportunity.stage, models.Opportunity.value)
            .join(models.Opportunity, models.Opportunity.contact_id == models.Contact.id)
            .filter(models.Opportunity.tenant_id == tid).all())
    smap: dict[str, dict] = {}
    for src, stage, value in rows:
        s = smap.setdefault(src, {"opps": 0, "won": 0, "won_value": 0})
        s["opps"] += 1
        if stage == "closed_won":
            s["won"] += 1
            s["won_value"] += value or 0
    by_source = []
    for src, d in smap.items():
        win_rate = round(d["won"] / d["opps"] * 100, 1) if d["opps"] else 0.0
        avg_deal = round(d["won_value"] / d["won"], 1) if d["won"] else 0.0
        by_source.append(schemas.AttributionSource(
            source=src, opps_count=d["opps"], closed_won_count=d["won"],
            revenue_won=d["won_value"], win_rate_pct=win_rate, avg_deal_size=avg_deal))
    by_source.sort(key=lambda x: x.revenue_won, reverse=True)
    total_rev = sum(s.revenue_won for s in by_source)
    total_won = sum(s.closed_won_count for s in by_source)
    best = by_source[0].source if by_source else None
    return schemas.AnalyticsAttribution(
        by_source=by_source, total_revenue_won=total_rev, total_won=total_won, best_source=best)


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
