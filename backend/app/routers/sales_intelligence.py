"""P28 — Warmup inbox + Sales Intelligence router.

Endpoints (préfixe /api/tenants/{tid}/sales-intel — distinct du router analytics.py P10 qui gère
/analytics/funnel + /analytics/cohorts sur le pipeline opportunities ; le router P28 expose un
contacts-funnel + enrollment-cohorts + forecast pondéré + warmup inbox, sans collision) :
  POST   /warmup                  — créer une config warmup inbox (crm.manage).
  GET    /warmup                  — lister les configs warmup du tenant (content.read).
  POST   /warmup/{wid}/advance    — avancer le warmup d'un jour (crm.manage).
  GET    /warmup/{wid}/stats      — stats warmup (daily_volume, open_rate, reply_rate) (content.read).
  GET    /funnel                  — contacts funnel (new|qualified|contacted|nurture|won) (content.read).
  GET    /cohorts                 — enrollment cohorts par semaine ISO (content.read).
  GET    /forecast                — prévisions pipeline pondéré (content.read).

RBAC (P28) :
  - content.read (Viewer+) — lecture (warmup list, stats, funnel, cohorts, forecast).
  - crm.manage (Editor+) — write (créer warmup, advance day).

Sécurité (P28) :
  - Warmup v1 : enregistrement des messages via record_warmup_message (simulateur/webhook — pas
    d'envoi email réel ici). Le scheduler P15 process_due_enrollments reste responsable de l'envoi.
  - Forecast = projection statistique (pondérée par probability), pas une promesse de revenu.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..services import sales_intelligence as si

router = APIRouter(prefix="/api/tenants/{tid}/sales-intel", tags=["sales-intelligence"])


# ─── Warmup inbox ────────────────────────────────────────────────────────────


@router.post("/warmup", response_model=schemas.InboxWarmupOut, status_code=status.HTTP_201_CREATED)
def create_warmup(
    tid: str,
    body: schemas.InboxWarmupRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Crée une config warmup inbox (P28). Gradation du volume d'envoi email pour construire la
    réputation de l'expéditeur. status=pending, démarrage via POST /warmup/{wid}/advance."""
    warmup = si.create_warmup(db, tenant_id=tid, sender_email=body.sender_email,
                              target_volume=body.target_volume, ramp_days=body.ramp_days,
                              user_id=membership.user_id)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="analytics.warmup.create", target_type="inbox_warmup", target_id=warmup.id,
              detail={"sender_email": body.sender_email, "target_volume": body.target_volume})
    db.commit()
    db.refresh(warmup)
    return warmup


@router.get("/warmup", response_model=list[schemas.InboxWarmupOut])
def list_warmups(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Liste les configs warmup du tenant (plus récentes d'abord)."""
    return db.query(models.InboxWarmup).filter_by(tenant_id=tid).order_by(
        models.InboxWarmup.created_at.desc()).all()


@router.post("/warmup/{wid}/advance", response_model=schemas.InboxWarmupOut)
def advance_warmup(
    tid: str, wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("crm.manage")),
):
    """Avance le warmup d'un jour (P28). Incrémente current_day + recompute daily_volume (rampe
    linéaire) + status (ramping|active). No-op si déjà completed."""
    warmup = db.query(models.InboxWarmup).filter_by(id=wid, tenant_id=tid).first()
    if not warmup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Warmup introuvable")
    warmup = si.advance_warmup_day(db, warmup)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="analytics.warmup.advance", target_type="inbox_warmup", target_id=warmup.id,
              detail={"current_day": warmup.current_day, "daily_volume": warmup.daily_volume,
                      "ramp_status": warmup.ramp_status})
    db.commit()
    db.refresh(warmup)
    return warmup


@router.get("/warmup/{wid}/stats", response_model=schemas.WarmupStatsOut)
def warmup_stats(
    tid: str, wid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Stats warmup (P28). daily_volume + progress_pct + sent_count + open_rate + reply_rate."""
    warmup = db.query(models.InboxWarmup).filter_by(id=wid, tenant_id=tid).first()
    if not warmup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Warmup introuvable")
    return si.warmup_stats(db, warmup)


# ─── Sales Intelligence (funnel + cohorts + forecast) ────────────────────────


@router.get("/funnel", response_model=schemas.FunnelOut)
def funnel(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Funnel de conversion des contacts (P28). Stages ordonnés (new → qualified → contacted →
    nurture → won) + lost à part + conversion_rate (won/total*100)."""
    return si.compute_funnel(db, tid)


@router.get("/cohorts", response_model=list[schemas.CohortOut])
def cohorts(
    tid: str,
    weeks: int = 8,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Cohortes d'enrollment par semaine ISO (P28). completion_rate par cohorte (completed/total*100).
    Retourne les `weeks` dernières cohortes (plus récentes d'abord)."""
    return si.compute_cohorts(db, tid, weeks=weeks)


@router.get("/forecast", response_model=schemas.ForecastOut)
def forecast(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Prévisions pipeline pondéré (P28). weighted_forecast = sum(value * probability / 100) sur les
    opportunities ouvertes. Distribution par stage. Projection statistique, pas une promesse de revenu."""
    return si.compute_forecast(db, tid)
