#!/usr/bin/env python3
"""audit_cron.py — P55 Cron hebdo : purge rétention + notification webhook alerts (tous tenants).

Usage (system cron / k8s CronJob, e.g. chaque dimanche 03h00) :
    python scripts/audit_cron.py
    # ou avec fenêtre/limite custom :
    python scripts/audit_cron.py --window-hours 24 --burst-threshold 10

Pour chaque tenant :
  1. Purge : supprime les entries audit > tenant.retention_days + logge meta audit.purge (actor system).
  2. Notify : évalue les alerts sur window_hours + POST celles >= min_severity vers le webhook configuré.

Aucune dépendance externe au-delà du backend (httpx pour POST webhook). Sortie : résumé par tenant.
Exit 0 même si certains webhooks échouent (best-effort). Exit 1 si erreur DB fatale.
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from app import models as M
from app.audit import log_audit
from app.database import SessionLocal
from app.routers.audit import _SEV_ORDER, _build_slack_payload, _compute_alerts


def purge_tenant(db, tenant) -> dict:
    """Purge les entries > retention_days + logge meta audit.purge (actor system). Retourne {purged, remaining}."""
    cutoff = datetime.utcnow() - timedelta(days=tenant.retention_days)
    old_q = db.query(M.AuditLog).filter_by(tenant_id=tenant.id).filter(M.AuditLog.created_at < cutoff)
    purged = old_q.count()
    remaining_before = db.query(M.AuditLog).filter_by(tenant_id=tenant.id).count()
    if purged > 0:
        log_audit(db, tenant_id=tenant.id, actor_user_id="system", actor_name="audit.cron",
                  action="audit.purge", target_type="audit_log", target_id=tenant.id,
                  detail={"purged": purged, "retention_days": tenant.retention_days,
                          "purged_before": cutoff.isoformat(), "remaining_before": remaining_before,
                          "source": "cron"})
        old_q.delete(synchronize_session=False)
        db.commit()
    remaining = db.query(M.AuditLog).filter_by(tenant_id=tenant.id).count()
    return {"purged": purged, "remaining": remaining, "retention_days": tenant.retention_days}


def notify_tenant(db, tenant, window_hours: int, burst_threshold: int) -> dict:
    """Évalue alerts + POST celles >= min_severity vers webhook. Retourne {evaluated, notified, failed}."""
    alerts = _compute_alerts(db, tenant.id, window_hours, burst_threshold)
    min_rank = _SEV_ORDER.get(tenant.audit_alert_min_severity or "high", 3)
    webhook_url = tenant.audit_alert_webhook_url
    notified = failed = 0
    if not webhook_url:
        return {"evaluated": len(alerts), "notified": 0, "failed": 0, "webhook": None}
    for a in alerts:
        if _SEV_ORDER.get(a.severity, 0) < min_rank:
            continue
        payload = _build_slack_payload(tenant.name, tenant.id, a)
        try:
            resp = httpx.post(webhook_url, json=payload, timeout=10.0)
            if 200 <= resp.status_code < 300:
                notified += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"evaluated": len(alerts), "notified": notified, "failed": failed, "webhook": webhook_url}


def main():
    ap = argparse.ArgumentParser(description="P55 audit cron : purge rétention + notify webhook (tous tenants)")
    ap.add_argument("--window-hours", type=int, default=24, help="Fenêtre analyse alerts (défaut 24h)")
    ap.add_argument("--burst-threshold", type=int, default=10, help="Seuil burst IP (défaut 10)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenants = db.query(M.Tenant).all()
        print(f"[audit_cron] {datetime.utcnow().isoformat()} — {len(tenants)} tenant(s) — "
              f"window={args.window_hours}h threshold={args.burst_threshold}")
        total_purged = total_notified = total_failed = 0
        for t in tenants:
            p = purge_tenant(db, t)
            n = notify_tenant(db, t, args.window_hours, args.burst_threshold)
            total_purged += p["purged"]
            total_notified += n["notified"]
            total_failed += n["failed"]
            print(f"  tenant {t.id} ({t.name}): purged={p['purged']} remaining={p['remaining']} "
                  f"| alerts eval={n['evaluated']} notified={n['notified']} failed={n['failed']} "
                  f"webhook={'on' if n['webhook'] else 'off'}")
        print(f"[audit_cron] DONE — total purged={total_purged} notified={total_notified} failed={total_failed}")
    except Exception as e:
        print(f"[audit_cron] FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
