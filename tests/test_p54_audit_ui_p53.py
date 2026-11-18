#!/usr/bin/env python3
"""test_p54_audit_ui_p53.py — P54 Audit UI P53 : panel alerts + bouton purge + export PDF signé.

P53 a ajouté 3 endpoints compliance (alerts + purge + export-pdf) MAIS audit.html ne les exposait
pas dans l'UI → les owners ne pouvaient les utiliser qu'en curl. P54 comble : UI complète.

Coverage (TestClient, AUCUN réseau externe en CI) :
  A. Page audit.html se charge + expose les nouveaux éléments P54 :
    1. GET /audit.html → 200, content-type text/html.
    2. Contient le bouton "Export PDF signé" (exportPdf function).
    3. Contient la card "Alertes compliance" + #alerts-panel.
    4. Contient le bouton "Purger rétention" (purgeRetention function).
    5. Contient #retention-hint (placeholder affichage retention_days).
    6. Contient loadAlerts (appelé dans init).
    7. Contient les 3 endpoints P53 référencés : /audit/alerts, /audit/purge, /audit/export-pdf.
    8. Contient le banner P53 (mention alertes + purge + PDF signé HMAC).
    9. viewport meta présent (responsive).
  B. Backend wiring (smoke — les 3 endpoints P53 répondent) :
    10. GET /audit/alerts → 200 (Owner) avec structure {window_hours, total_alerts, alerts}.
    11. POST /audit/purge → 200 (Owner) avec PurgeResult {purged, retention_days, purged_before, remaining}.
    12. GET /audit/export-pdf → 200 (Owner) + application/pdf + body %PDF.
    13. Admin → 403 sur les 3 endpoints (permission gate).
  C. JS syntaxe valide (pas d'erreur de parsing — page servie sans crash) :
    14. La page ne contient pas de balise <script> cassée (compte de balises ouvertes/fermées égal).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p54_test.db python tests/test_p54_audit_ui_p53.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p54_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))

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

    print("=== test_p54_audit_ui_p53.py — P54 Audit UI P53 (panel alerts + purge + PDF signé) ===\n")

    # Seed : owner + admin + tenant
    owner = M.User(id="usr_o8", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_a8", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_c8", name="Corp P54", plan="Enterprise", owner_id="usr_o8",
                      sso_enabled=False, retention_days=1)
    member_admin = M.Member(id="mem_a8", tenant_id="tnt_c8", user_id="usr_a8",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o8')}"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_a8')}"}

    # Seed 1 audit entry (pour que purge/alerts aient du contenu)
    from app.logging_config import request_ip_var
    t_ip = request_ip_var.set("203.0.113.42")
    try:
        log_audit(test_db, tenant_id="tnt_c8", actor_user_id="usr_o8", actor_name="Owner",
                  action="crm.contact.create", target_type="contact", target_id="c1", detail={"k": "v"})
    finally:
        request_ip_var.reset(t_ip)
    test_db.commit()

    # ══════════════════════════════════════════════════════════════════════
    # A. Page audit.html — éléments P54
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Page audit.html (/audit.html) — éléments P54 ---")
    r = client.get("/audit.html")
    ck(r.status_code == 200, f"GET /audit.html → 200 (got {r.status_code})")
    html = r.text
    ck("Export PDF signé" in html, "bouton 'Export PDF signé' présent")
    ck("exportPdf" in html, "fonction exportPdf présente")
    ck("Alertes compliance" in html, "card 'Alertes compliance' présente")
    ck('id="alerts-panel"' in html, "panel #alerts-panel présent")
    ck("Purger rétention" in html, "bouton 'Purger rétention' présent")
    ck("purgeRetention" in html, "fonction purgeRetention présente")
    ck('id="retention-hint"' in html, "placeholder #retention-hint présent")
    ck("loadAlerts" in html, "fonction loadAlerts présente")
    ck("loadAlerts()" in html, "loadAlerts() appelé dans init()")
    # Wiring aux 3 endpoints P53
    ck("/audit/alerts" in html, "endpoint /audit/alerts référencé dans la page")
    ck("/audit/purge" in html, "endpoint /audit/purge référencé dans la page")
    ck("/audit/export-pdf" in html, "endpoint /audit/export-pdf référencé dans la page")
    # Banner P53
    ck("P53 compliance-grade" in html, "banner mentionne 'P53 compliance-grade'")
    ck("burst IP" in html, "banner mentionne 'burst IP' (alertes)")
    ck("HMAC-SHA256" in html, "banner mentionne 'HMAC-SHA256' (PDF signé)")
    ck("audit.purge" in html, "banner mentionne meta 'audit.purge'")
    ck('name="viewport"' in html and 'width=device-width' in html, "viewport meta responsive présent")

    # ══════════════════════════════════════════════════════════════════════
    # B. Backend wiring (smoke — 3 endpoints P53 répondent)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Backend wiring (3 endpoints P53 répondent) ---")
    r = client.get("/api/tenants/tnt_c8/audit/alerts", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/alerts → 200 (got {r.status_code})")
    al = r.json()
    ck("window_hours" in al and "total_alerts" in al and "alerts" in al, "structure alerts {window_hours, total_alerts, alerts}")

    r = client.post("/api/tenants/tnt_c8/audit/purge", headers=H_OWNER)
    ck(r.status_code == 200, f"POST /audit/purge → 200 (got {r.status_code})")
    pr = r.json()
    ck("purged" in pr and "retention_days" in pr and "remaining" in pr, "structure PurgeResult {purged, retention_days, remaining}")

    r = client.get("/api/tenants/tnt_c8/audit/export-pdf", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/export-pdf → 200 (got {r.status_code})")
    ck("application/pdf" in r.headers.get("content-type", ""), "content-type application/pdf")
    ck(r.content[:4] == b"%PDF", "body commence par %PDF")

    # Permission gate (Admin → 403 sur les 3)
    ck(client.get("/api/tenants/tnt_c8/audit/alerts", headers=H_ADMIN).status_code == 403, "Admin /alerts → 403")
    ck(client.post("/api/tenants/tnt_c8/audit/purge", headers=H_ADMIN).status_code == 403, "Admin /purge → 403")
    ck(client.get("/api/tenants/tnt_c8/audit/export-pdf", headers=H_ADMIN).status_code == 403, "Admin /export-pdf → 403")

    # ══════════════════════════════════════════════════════════════════════
    # C. JS syntaxe (pas de balise cassée)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. JS syntaxe (balises script équilibrées) ---")
    open_scripts = html.count("<script")
    close_scripts = html.count("</script>")
    ck(open_scripts == close_scripts, f"<script> ouverts={open_scripts} fermés={close_scripts} (équilibrés)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
