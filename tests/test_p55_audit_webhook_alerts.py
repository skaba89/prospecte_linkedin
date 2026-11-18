#!/usr/bin/env python3
"""test_p55_audit_webhook_alerts.py — P55 Webhook alerts compliance + cron.

Coverage (TestClient, httpx POST mocké — AUCUN réseau externe en CI) :
  A. GET/PUT /audit/alerts/webhook (config webhook) :
    1. GET → default (webhook_url=None, min_severity="high").
    2. PUT config valide → 200 + update tenant + retourne nouvelle config.
    3. PUT min_severity invalide → 400.
    4. PUT logge audit.alert_webhook.update.
    5. Admin → 403 (GET + PUT).
  B. POST /audit/alerts/notify (notification webhook Slack-compatible) :
    6. Seed burst high (60 actions IP) + no_actor low → notify min_severity=high → notified=1 (high only).
    7. httpx.post appelé avec payload Slack (text + attachments[0].fields + color high=#dc2626).
    8. Filtre min_severity=low → notified=2 (high + low notifiés).
    9. No webhook configuré → notified=0, tous results status=0.
    10. Webhook retourne 500 → delivered=False sur cet item.
    11. Admin → 403.
  C. _build_slack_payload (pure function) :
    12. Retourne dict{text, attachments[1]} avec fields (Type/Sévérité/Cible/Occurrences/Tenant) + ts int.
    13. Severity high → color #dc2626, emoji 🚨.
  D. scripts/audit_cron.py (smoke) :
    14. Run sur DB avec 2 tenants seedés → exit 0 + résumé imprimé (purged/notified par tenant).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p55_test.db python tests/test_p55_audit_webhook_alerts.py
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p55_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")

passed = failed = 0


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def main():
    from app import models as M
    from app.audit import log_audit
    from app.database import Base, get_db
    from app.routers.audit import _build_slack_payload
    from app.security import create_access_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    test_db = Sess()

    def _override_db():
        yield test_db

    import app.routers.audit as audit_mod  # pour mocker httpx.post
    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    for tbl in [M.AuditLog, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p55_audit_webhook_alerts.py — P55 Webhook alerts + cron ===\n")

    owner = M.User(id="usr_o9", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_a9", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_c9", name="Corp P55", plan="Enterprise", owner_id="usr_o9",
                      sso_enabled=False, retention_days=2555)
    member_admin = M.Member(id="mem_a9", tenant_id="tnt_c9", user_id="usr_a9",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o9')}"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_a9')}"}

    from app.logging_config import request_ip_var
    def seed_audit(action, *, ip="1.2.3.4", actor="usr_o9", name="Owner"):
        t_ip = request_ip_var.set(ip)
        try:
            log_audit(test_db, tenant_id="tnt_c9", actor_user_id=actor, actor_name=name,
                      action=action, target_type="contact", target_id="c1")
        finally:
            request_ip_var.reset(t_ip)

    # ══════════════════════════════════════════════════════════════════════
    # A. GET/PUT /audit/alerts/webhook (config)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. GET/PUT /audit/alerts/webhook (config) ---")
    r = client.get("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /alerts/webhook → 200 (got {r.status_code})")
    cfg = r.json()
    ck(cfg["webhook_url"] is None, "default webhook_url=None (désactivé)")
    ck(cfg["min_severity"] == "high", "default min_severity=high")

    r = client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": "https://hooks.slack.com/services/T00/B00/XXX", "min_severity": "medium"})
    ck(r.status_code == 200, f"PUT /alerts/webhook valide → 200 (got {r.status_code})")
    cfg = r.json()
    ck(cfg["webhook_url"] == "https://hooks.slack.com/services/T00/B00/XXX", "webhook_url mis à jour")
    ck(cfg["min_severity"] == "medium", "min_severity=medium")
    # Tenant réellement persisté
    t_db = test_db.query(M.Tenant).filter_by(id="tnt_c9").first()
    ck(t_db.audit_alert_webhook_url == "https://hooks.slack.com/services/T00/B00/XXX", "tenant.persisted webhook_url")
    ck(t_db.audit_alert_min_severity == "medium", "tenant.persisted min_severity=medium")
    # Audit entry loggée
    cfg_log = test_db.query(M.AuditLog).filter_by(action="audit.alert_webhook.update").first()
    ck(cfg_log is not None, "audit.alert_webhook.update loggé")
    ck(cfg_log.detail.get("min_severity") == "medium", "audit.detail.min_severity=medium")

    r = client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": None, "min_severity": "invalid"})
    ck(r.status_code == 400, f"PUT min_severity invalide → 400 (got {r.status_code})")

    ck(client.get("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_ADMIN).status_code == 403, "Admin GET webhook → 403")
    ck(client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_ADMIN, json={"min_severity": "low"}).status_code == 403, "Admin PUT webhook → 403")

    # ══════════════════════════════════════════════════════════════════════
    # B. POST /audit/alerts/notify (notification webhook)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. POST /audit/alerts/notify (notification webhook Slack) ---")
    # Seed : 60 actions IP X (high >5x10) + 3 no_actor (low)
    for _ in range(60):
        seed_audit("api.call", ip="198.51.100.50")
    for _ in range(3):
        seed_audit("webhook.receive", ip="10.0.0.50", actor=None, name="")
    test_db.commit()

    # Mock httpx.post : capture payloads + retourne 200
    posted = []
    class FakeResp:
        def __init__(self, code): self.status_code = code
    def fake_post(url, json=None, timeout=None):
        posted.append({"url": url, "payload": json})
        return FakeResp(200)
    orig_post = audit_mod.httpx.post
    audit_mod.httpx.post = fake_post
    try:
        # Config min_severity=high → seul le burst high (198.51.100.50) notifié, no_actor low skipped
        client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": "https://hooks.slack.test/abc", "min_severity": "high"})
        r = client.post("/api/tenants/tnt_c9/audit/alerts/notify?window_hours=24&burst_threshold=10", headers=H_OWNER)
        ck(r.status_code == 200, f"POST /alerts/notify → 200 (got {r.status_code})")
        nr = r.json()
        ck(nr["evaluated"] == 2, f"evaluated=2 (1 ip_burst + 1 no_actor) (got {nr['evaluated']})")
        ck(nr["notified"] == 1, f"notified=1 (high only, low skipped) (got {nr['notified']})")
        ck(nr["min_severity"] == "high", "min_severity=high")
        ck(nr["webhook_url"] == "https://hooks.slack.test/abc", "webhook_url retourné")
        ck(len(nr["results"]) == 2, "2 results (1 par alerte)")
        high_item = next(it for it in nr["results"] if it["severity"] == "high")
        ck(high_item["delivered"] is True and high_item["status"] == 200, "high item delivered=True status=200")
        low_item = next(it for it in nr["results"] if it["severity"] == "low")
        ck(low_item["delivered"] is False, "low item not delivered (filtre min_severity=high)")
        # httpx.post appelé 1 fois (high only) avec payload Slack
        ck(len(posted) == 1, f"httpx.post appelé 1x (high only) (got {len(posted)})")
        p = posted[0]["payload"]
        ck("text" in p and "attachments" in p, "payload Slack {text, attachments}")
        ck("🚨" in p["text"] and "Corp P55" in p["text"], "payload text contient emoji 🚨 + tenant name")
        att = p["attachments"][0]
        ck(att["color"] == "#dc2626", f"attachment color high=#dc2626 (got {att['color']})")
        fields = {f["title"]: f["value"] for f in att["fields"]}
        ck("Type" in fields and "Sévérité" in fields and "Occurrences" in fields, "attachment fields (Type/Sévérité/Occurrences)")
        ck(fields["Sévérité"] == "HIGH", "field Sévérité=HIGH")
        ck("ts" in att and isinstance(att["ts"], int), "attachment ts int (timestamp)")
        posted.clear()

        # Filtre min_severity=low → high + low notifiés (2)
        client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": "https://hooks.slack.test/abc", "min_severity": "low"})
        r = client.post("/api/tenants/tnt_c9/audit/alerts/notify?window_hours=24&burst_threshold=10", headers=H_OWNER)
        ck(r.json()["notified"] == 2, f"min_severity=low → notified=2 (high + low) (got {r.json()['notified']})")
        ck(len(posted) == 2, f"httpx.post appelé 2x (high + low) (got {len(posted)})")
        posted.clear()

        # No webhook configuré → notified=0, status=0 partout
        client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": None, "min_severity": "low"})
        r = client.post("/api/tenants/tnt_c9/audit/alerts/notify?window_hours=24", headers=H_OWNER)
        ck(r.json()["notified"] == 0, "no webhook → notified=0")
        ck(all(it["status"] == 0 for it in r.json()["results"]), "no webhook → tous status=0")
        ck(len(posted) == 0, "no webhook → httpx.post jamais appelé")

        # Webhook retourne 500 → delivered=False
        def fake_post_500(url, json=None, timeout=None):
            posted.append({"url": url}); return FakeResp(500)
        audit_mod.httpx.post = fake_post_500
        client.put("/api/tenants/tnt_c9/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": "https://hooks.slack.test/abc", "min_severity": "high"})
        r = client.post("/api/tenants/tnt_c9/audit/alerts/notify?window_hours=24", headers=H_OWNER)
        high_item = next(it for it in r.json()["results"] if it["severity"] == "high")
        ck(high_item["delivered"] is False and high_item["status"] == 500, "webhook 500 → delivered=False status=500")
    finally:
        audit_mod.httpx.post = orig_post

    # Admin → 403
    ck(client.post("/api/tenants/tnt_c9/audit/alerts/notify", headers=H_ADMIN).status_code == 403, "Admin notify → 403")

    # ══════════════════════════════════════════════════════════════════════
    # C. _build_slack_payload (pure function)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. _build_slack_payload (pure function) ---")
    from app.schemas import AuditAlertItem
    alert = AuditAlertItem(kind="ip_burst", severity="high", label="198.51.100.50", count=60,
                           detail={"threshold": 10, "window_hours": 24})
    p = _build_slack_payload("Corp P55", "tnt_c9", alert)
    ck(isinstance(p, dict) and "text" in p and "attachments" in p, "payload dict{text, attachments}")
    ck(len(p["attachments"]) == 1, "1 attachment")
    att = p["attachments"][0]
    ck(att["color"] == "#dc2626", f"severity high → color #dc2626 (got {att['color']})")
    ck("🚨" in p["text"], "emoji 🚨 pour high")
    field_titles = [f["title"] for f in att["fields"]]
    ck(all(t in field_titles for t in ["Type", "Sévérité", "Cible", "Occurrences", "Tenant"]),
       "5 fields (Type/Sévérité/Cible/Occurrences/Tenant)")
    ck(isinstance(att["ts"], int), f"ts int timestamp (got {type(att['ts']).__name__})")
    # Severity medium → color différent
    p_med = _build_slack_payload("Corp", "t", AuditAlertItem(kind="ip_burst", severity="medium", label="1.1.1.1", count=15))
    ck(p_med["attachments"][0]["color"] == "#f59e0b", "severity medium → color #f59e0b")
    ck("⚠️" in p_med["text"], "emoji ⚠️ pour medium")

    # ══════════════════════════════════════════════════════════════════════
    # D. scripts/audit_cron.py (smoke)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. scripts/audit_cron.py (smoke) ---")
    # Seed un 2e tenant pour vérifier la boucle multi-tenant
    t2 = M.Tenant(id="tnt_d9", name="Corp 2", plan="Starter", owner_id="usr_o9", retention_days=1)
    test_db.add(t2); test_db.commit()
    # 1 entry ancienne sur tnt_d9 (>1 jour → purgée par cron)
    old_e = log_audit(test_db, tenant_id="tnt_d9", actor_user_id="usr_o9", actor_name="Owner",
                      action="crm.contact.create", target_type="contact", target_id="x")
    old_e.created_at = datetime.utcnow() - timedelta(days=2)
    test_db.commit()
    before_t2 = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_d9").count()
    ck(before_t2 == 1, "tnt_d9 a 1 entry (sera purgée)")

    import subprocess
    env = dict(os.environ)
    env["REVPULSE_DATABASE_URL"] = os.environ["REVPULSE_DATABASE_URL"]
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_cron.py"),
                           "--window-hours", "24", "--burst-threshold", "10"],
                          capture_output=True, text=True, env=env, timeout=30)
    ck(proc.returncode == 0, f"audit_cron.py exit 0 (got {proc.returncode})")
    ck("DONE" in proc.stdout, "cron imprime 'DONE' (résumé final)")
    ck("tnt_c9" in proc.stdout and "tnt_d9" in proc.stdout, "cron traite les 2 tenants")
    # tnt_d9 entry ancienne purgée
    after_t2 = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_d9").count()
    ck(after_t2 == 1, f"tnt_d9 : 1 entry après cron (l'ancienne purgée + 1 meta audit.purge) (got {after_t2})")
    # meta audit.purge créée par le cron sur tnt_d9
    cron_meta = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_d9", action="audit.purge").first()
    ck(cron_meta is not None and cron_meta.actor_name == "audit.cron", "meta audit.purge (actor=audit.cron) créée")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
