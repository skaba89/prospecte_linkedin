#!/usr/bin/env python3
"""test_p56_webhook_config_ui.py — P56 Webhook config UI sur audit.html.

P55 a livré les endpoints webhook (GET/PUT config + POST notify) MAIS audit.html ne les exposait
pas dans l'UI → les owners devaient curl pour configurer/tester leur webhook. P56 comble : UI complète.

Coverage (TestClient, AUCUN réseau externe en CI) :
  A. Page audit.html se charge + expose les nouveaux éléments P56 :
    1. GET /audit.html → 200, content-type text/html.
    2. Contient la card "Webhook alerts" (P55).
    3. Contient #wh-url (input URL) + #wh-sev (select min_severity).
    4. Contient bouton "Sauvegarder" (saveWebhookConfig) + "Tester notify" (testNotify).
    5. Contient #notify-results (zone affichage résultat notify).
    6. Contient loadWebhookConfig + saveWebhookConfig + testNotify (fonctions).
    7. loadWebhookConfig() appelé dans init().
    8. api() étendu pour accepter un body optionnel (PUT webhook).
    9. Wiring aux 3 endpoints P55 : /audit/alerts/webhook (GET+PUT) + /audit/alerts/notify.
    10. Banner mentionne P55 webhook alerts (config + notify push + cron hebdo).
    11. Options severity : high/medium/low (3 options dans le select).
  B. Backend wiring (smoke — les 3 endpoints P55 répondent depuis l'UI) :
    12. GET /audit/alerts/webhook → 200 (Owner) avec {webhook_url, min_severity}.
    13. PUT /audit/alerts/webhook → 200 (Owner) + update tenant.
    14. POST /audit/alerts/notify → 200 (Owner) avec NotifyResult.
    15. Admin → 403 sur les 3.
  C. JS syntaxe valide :
    16. Balises script équilibrées.

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p56_test.db python tests/test_p56_webhook_config_ui.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p56_test.db")
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

    print("=== test_p56_webhook_config_ui.py — P56 Webhook config UI ===\n")

    owner = M.User(id="usr_oA", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_aA", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_cA", name="Corp P56", plan="Enterprise", owner_id="usr_oA",
                      sso_enabled=False, retention_days=2555)
    member_admin = M.Member(id="mem_aA", tenant_id="tnt_cA", user_id="usr_aA",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_oA')}"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_aA')}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Page audit.html — éléments P56
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Page audit.html (/audit.html) — éléments P56 ---")
    r = client.get("/audit.html")
    ck(r.status_code == 200, f"GET /audit.html → 200 (got {r.status_code})")
    html = r.text
    ck("Webhook alerts" in html, "card 'Webhook alerts' présente")
    ck('id="wh-url"' in html, "input #wh-url (URL webhook) présent")
    ck('id="wh-sev"' in html, "select #wh-sev (min_severity) présent")
    ck("Sauvegarder" in html and "saveWebhookConfig" in html, "bouton Sauvegarder + saveWebhookConfig")
    ck("Tester notify" in html and "testNotify" in html, "bouton Tester notify + testNotify")
    ck('id="notify-results"' in html, "zone #notify-results présente")
    ck("loadWebhookConfig" in html, "fonction loadWebhookConfig présente")
    ck("loadWebhookConfig()" in html, "loadWebhookConfig() appelé dans init()")
    ck("saveWebhookConfig" in html, "fonction saveWebhookConfig présente")
    ck("testNotify" in html, "fonction testNotify présente")
    # api() étendu pour body optionnel
    ck("async function api(method, path, body)" in html, "api() signature étendue (body optionnel)")
    ck("opts.body = JSON.stringify(body)" in html, "api() sérialise le body quand présent")
    # Wiring aux 3 endpoints P55
    ck("/audit/alerts/webhook" in html, "endpoint /audit/alerts/webhook référencé")
    ck("/audit/alerts/notify" in html, "endpoint /audit/alerts/notify référencé")
    # Banner P55
    ck("P55 webhook alerts" in html, "banner mentionne 'P55 webhook alerts'")
    ck("notify push" in html, "banner mentionne 'notify push'")
    ck("cron hebdo" in html, "banner mentionne 'cron hebdo'")
    # Options severity dans le select
    ck(html.count('value="high"') >= 1 and html.count('value="medium"') >= 1 and html.count('value="low"') >= 1,
       "3 options severity (high/medium/low) dans le select")
    ck('name="viewport"' in html and 'width=device-width' in html, "viewport meta responsive présent")

    # ══════════════════════════════════════════════════════════════════════
    # B. Backend wiring (smoke — 3 endpoints P55 répondent)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Backend wiring (3 endpoints P55 répondent) ---")
    r = client.get("/api/tenants/tnt_cA/audit/alerts/webhook", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /alerts/webhook → 200 (got {r.status_code})")
    ck("webhook_url" in r.json() and "min_severity" in r.json(), "structure {webhook_url, min_severity}")

    r = client.put("/api/tenants/tnt_cA/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": "https://hooks.slack.test/abc", "min_severity": "medium"})
    ck(r.status_code == 200, f"PUT /alerts/webhook → 200 (got {r.status_code})")
    ck(r.json()["min_severity"] == "medium", "PUT update min_severity=medium")

    r = client.post("/api/tenants/tnt_cA/audit/alerts/notify?window_hours=24", headers=H_OWNER)
    ck(r.status_code == 200, f"POST /alerts/notify → 200 (got {r.status_code})")
    nr = r.json()
    ck("evaluated" in nr and "notified" in nr and "results" in nr, "structure NotifyResult {evaluated, notified, results}")

    # Permission gate (Admin → 403)
    ck(client.get("/api/tenants/tnt_cA/audit/alerts/webhook", headers=H_ADMIN).status_code == 403, "Admin GET webhook → 403")
    ck(client.put("/api/tenants/tnt_cA/audit/alerts/webhook", headers=H_ADMIN, json={"min_severity": "low"}).status_code == 403, "Admin PUT webhook → 403")
    ck(client.post("/api/tenants/tnt_cA/audit/alerts/notify", headers=H_ADMIN).status_code == 403, "Admin notify → 403")

    # ══════════════════════════════════════════════════════════════════════
    # C. JS syntaxe (balises script équilibrées)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. JS syntaxe (balises script équilibrées) ---")
    open_s = html.count("<script")
    close_s = html.count("</script>")
    ck(open_s == close_s, f"<script> ouverts={open_s} fermés={close_s} (équilibrés)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
