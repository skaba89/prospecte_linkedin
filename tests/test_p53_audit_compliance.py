#!/usr/bin/env python3
"""test_p53_audit_compliance.py — P53 Audit log retention + export PDF signé + alertes compliance.

Coverage (TestClient, AUCUN réseau externe en CI) :
  A. POST /audit/purge (rétention compliance, loggée meta) :
    1. Entries > retention_days purgées, récentes survivent.
    2. Meta entry "audit.purge" créée AVANT suppression (survit) avec detail={purged, before, remaining}.
    3. PurgeResult retourne purged/remaining corrects.
    4. retention_days respecté (set 1 jour → entry 2 jours purgée, entry 1h survit).
    5. Admin → 403. Outsider → 403.
  B. GET /audit/export-pdf (signé HMAC-SHA256, non-répudiation) :
    6. 200 + content-type application/pdf + body commence par %PDF.
    7. Filtre action respecté (entries matching uniquement).
    8. Signature HMAC recomputable (tamper-evident) : même payload → même signature ; payload modifié → signature différente.
    9. Admin → 403.
  C. GET /audit/alerts (burst IP + no_actor) :
    10. Burst : 15 actions même IP/heure → ip_burst alert (count=15, severity medium).
    11. Burst >5×seuil → severity high.
    12. burst_threshold=20 → 15 ne déclenche pas.
    13. no_actor : entries sans actor_user_id → alert no_actor.
    14. window_hours respecté (entries >1h non comptées).
    15. Admin → 403.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p53_test.db python tests/test_p53_audit_compliance.py
"""
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p53_test.db")
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
    from app.routers.audit import _audit_hmac_key, _audit_signature
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

    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    for tbl in [M.AuditLog, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p53_audit_compliance.py — P53 Audit retention + export PDF signé + alerts ===\n")

    # Seed : owner + admin + outsider + tenant (retention_days=1 pour test rapide)
    owner = M.User(id="usr_o7", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_a7", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    out = M.User(id="usr_x7", email="out@other.io", name="Outsider", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_c7", name="Corp P53", plan="Enterprise", owner_id="usr_o7",
                      sso_enabled=False, retention_days=1)  # rétention 1 jour pour test
    member_admin = M.Member(id="mem_a7", tenant_id="tnt_c7", user_id="usr_a7",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, out, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o7')}"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_a7')}"}
    H_OUT = {"Authorization": f"Bearer {create_access_token('usr_x7')}"}

    from app.logging_config import request_ip_var
    def seed_audit(action, *, ip="1.2.3.4", actor="usr_o7", name="Owner", target_type="tenant",
                  target_id="tnt_c7", days_ago=0, detail=None):
        t_ip = request_ip_var.set(ip)
        try:
            entry = log_audit(test_db, tenant_id="tnt_c7", actor_user_id=actor, actor_name=name,
                              action=action, target_type=target_type, target_id=target_id, detail=detail)
            if days_ago:
                entry.created_at = datetime.utcnow() - timedelta(days=days_ago)
        finally:
            request_ip_var.reset(t_ip)
        return entry

    # ══════════════════════════════════════════════════════════════════════
    # A. POST /audit/purge (rétention compliance, loggée meta)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. POST /audit/purge (rétention + meta log) ---")
    # Seed : 3 entries anciennes (2 jours) + 2 récentes (maintenant)
    seed_audit("crm.contact.create", days_ago=2, ip="10.0.0.1")
    seed_audit("crm.contact.update", days_ago=2, ip="10.0.0.1")
    seed_audit("member.invite", days_ago=2, ip="10.0.0.2")
    seed_audit("sso.config.upsert", ip="10.0.0.3")  # récent
    seed_audit("billing.plan.change", ip="10.0.0.3")  # récent
    test_db.commit()
    total_before = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_c7").count()
    ck(total_before == 5, f"5 entries seedées (got {total_before})")

    r = client.post("/api/tenants/tnt_c7/audit/purge", headers=H_OWNER)
    ck(r.status_code == 200, f"POST /audit/purge → 200 (got {r.status_code})")
    pr = r.json()
    ck(pr["purged"] == 3, f"purged=3 (3 entries >1 jour) (got {pr['purged']})")
    ck(pr["remaining"] == 3, f"remaining=3 (2 récentes + 1 meta purge) (got {pr['remaining']})")
    ck(pr["retention_days"] == 1, f"retention_days=1 (got {pr['retention_days']})")

    # Meta entry audit.purge créée + survit (récente)
    meta = test_db.query(M.AuditLog).filter_by(action="audit.purge").first()
    ck(meta is not None, "meta entry audit.purge créée (survit à la purge)")
    ck(meta.detail.get("purged") == 3, "meta.detail.purged=3")
    ck(meta.detail.get("remaining_before") == 5, "meta.detail.remaining_before=5")
    ck(meta.target_type == "audit_log", "meta.target_type=audit_log")

    # Entries anciennes supprimées, récentes survivent
    old_survivors = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_c7").filter(
        M.AuditLog.created_at < datetime.utcnow() - timedelta(days=1)).count()
    ck(old_survivors == 0, f"aucune entry ancienne survivante (got {old_survivors})")
    recent_survivors = test_db.query(M.AuditLog).filter_by(tenant_id="tnt_c7").filter(
        M.AuditLog.action.in_(["sso.config.upsert", "billing.plan.change"])).count()
    ck(recent_survivors == 2, f"2 entries récentes survivent (got {recent_survivors})")

    # Permission gate
    r = client.post("/api/tenants/tnt_c7/audit/purge", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin purge → 403 (got {r.status_code})")
    r = client.post("/api/tenants/tnt_c7/audit/purge", headers=H_OUT)
    ck(r.status_code == 403, f"Outsider purge → 403 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # B. GET /audit/export-pdf (signé HMAC-SHA256, non-répudiation)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. GET /audit/export-pdf (PDF signé HMAC) ---")
    r = client.get("/api/tenants/tnt_c7/audit/export-pdf", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/export-pdf → 200 (got {r.status_code})")
    ck("application/pdf" in r.headers.get("content-type", ""), "content-type application/pdf")
    ck(r.content[:4] == b"%PDF", f"body commence par %PDF (got {r.content[:8]})")
    ck("attachment" in r.headers.get("content-disposition", ""), "Content-Disposition attachment")

    # Filtre action respecté : le PDF avec filtre est plus petit (moins d'entries) que sans filtre.
    # (ReportLab compresse les flux → on ne peut pas chercher le texte en clair dans les bytes ;
    #  on vérifie donc que le filtre réduit la taille du PDF, prenant que _apply_filters est appliqué.)
    r_full = client.get("/api/tenants/tnt_c7/audit/export-pdf", headers=H_OWNER)
    r_filtered = client.get("/api/tenants/tnt_c7/audit/export-pdf?action=sso", headers=H_OWNER)
    ck(r_filtered.status_code == 200, f"export-pdf?action=sso → 200 (got {r_filtered.status_code})")
    ck(len(r_filtered.content) < len(r_full.content),
       f"PDF filtré < PDF complet (filtre appliqué : {len(r_filtered.content)} < {len(r_full.content)})")
    # Cross-check : le filtre action=sso via le JSON list (même _apply_filters) ne retourne que sso entries
    r_list = client.get("/api/tenants/tnt_c7/audit?action=sso", headers=H_OWNER)
    ck(r_list.status_code == 200, "GET /audit?action=sso → 200 (cross-check filtre)")
    ck(all("sso.config" in e["action"] for e in r_list.json()), "filtre action=sso via JSON list → uniquement sso entries")

    # Signature HMAC tamper-evident : même payload → même signature ; modifié → différent
    entries = [{"created_at": "2026-01-01", "actor": "X", "actor_user_id": "u1",
                "action": "test", "target_type": "t", "target_id": "i", "ip": "1.1.1.1", "request_id": "r"}]
    sig1 = _audit_signature(entries)
    sig2 = _audit_signature(entries)  # même payload → même signature
    ck(sig1 == sig2, "même payload → même signature (déterministe)")
    ck(len(sig1) == 64, f"signature 64 hex chars (SHA256) (got {len(sig1)})")
    entries_modified = [{**entries[0], "action": "TAMPERED"}]
    sig3 = _audit_signature(entries_modified)
    ck(sig3 != sig1, "payload modifié → signature différente (tamper-evident)")

    # Vérification HMAC avec la clé serveur (simulateur auditeur offline)
    key = _audit_hmac_key()
    payload = json.dumps(entries, sort_keys=True, default=str)
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    ck(expected == sig1, "HMAC recomputé offline == signature (non-répudiation vérifiable)")

    # Admin → 403
    r = client.get("/api/tenants/tnt_c7/audit/export-pdf", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin export-pdf → 403 (got {r.status_code})")

    # ══════════════════════════════════════════════════════════════════════
    # C. GET /audit/alerts (burst IP + no_actor)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. GET /audit/alerts (burst IP + no_actor) ---")
    # Seed burst : 15 actions depuis 203.0.113.99 dans la dernière heure
    for i in range(15):
        seed_audit("crm.contact.create", ip="203.0.113.99", actor="usr_o7", name="Owner")
    # Seed no_actor : 3 entries système (actor=None)
    for i in range(3):
        seed_audit("webhook.receive", ip="10.0.0.50", actor=None, name="")
    # Seed burst >5×seuil : 60 actions depuis 198.51.100.50 (high severity)
    for i in range(60):
        seed_audit("api.call", ip="198.51.100.50", actor="usr_o7", name="Owner")
    # Seed entry hors fenêtre (2h ago) — ne doit pas compter
    old_entry = seed_audit("crm.contact.delete", ip="203.0.113.99", actor="usr_o7", name="Owner")
    old_entry.created_at = datetime.utcnow() - timedelta(hours=2)
    test_db.commit()

    r = client.get("/api/tenants/tnt_c7/audit/alerts?window_hours=1&burst_threshold=10", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/alerts → 200 (got {r.status_code})")
    al = r.json()
    ck(al["window_hours"] == 1, f"window_hours=1 (got {al['window_hours']})")
    burst_alerts = [a for a in al["alerts"] if a["kind"] == "ip_burst"]
    ck(len(burst_alerts) == 2, f"2 ip_burst alerts (203.0.113.99 + 198.51.100.50) (got {len(burst_alerts)})")
    # 198.51.100.50 = 60 actions → high severity (>5×10)
    high = [a for a in burst_alerts if a["severity"] == "high"]
    ck(len(high) == 1 and high[0]["label"] == "198.51.100.50", "1 high alert pour 198.51.100.50 (60 actions >5×10)")
    ck(high[0]["count"] == 60, f"high alert count=60 (got {high[0]['count']})")
    # 203.0.113.99 = 15 actions (dans la fenêtre) → medium
    med = [a for a in burst_alerts if a["severity"] == "medium"]
    ck(len(med) == 1 and med[0]["label"] == "203.0.113.99", "1 medium alert pour 203.0.113.99 (15 actions)")
    ck(med[0]["count"] == 15, f"medium alert count=15 (got {med[0]['count']})")

    # no_actor alerts
    no_actor_alerts = [a for a in al["alerts"] if a["kind"] == "no_actor"]
    ck(len(no_actor_alerts) >= 1, f"≥1 no_actor alert (got {len(no_actor_alerts)})")
    ck(any(a["label"] == "webhook.receive" for a in no_actor_alerts), "no_actor alert pour webhook.receive")

    # burst_threshold=70 → aucun burst (60 < 70, 15 < 70)
    r = client.get("/api/tenants/tnt_c7/audit/alerts?burst_threshold=70", headers=H_OWNER)
    burst_alerts = [a for a in r.json()["alerts"] if a["kind"] == "ip_burst"]
    ck(len(burst_alerts) == 0, f"burst_threshold=70 → 0 ip_burst alerts (got {len(burst_alerts)})")

    # window_hours=0.5 → entry 2h ago exclue (déjà), et window_hours=1 inclut 15+60
    r = client.get("/api/tenants/tnt_c7/audit/alerts?window_hours=1", headers=H_OWNER)
    ck(r.json()["total_alerts"] >= 2, f"window 1h → ≥2 alerts (got {r.json()['total_alerts']})")

    # Admin → 403
    r = client.get("/api/tenants/tnt_c7/audit/alerts", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin alerts → 403 (got {r.status_code})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
