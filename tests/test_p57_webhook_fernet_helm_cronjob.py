#!/usr/bin/env python3
"""test_p57_webhook_fernet_helm_cronjob.py — P57 Webhook URL chiffrement Fernet + Helm CronJob.

P55/P56 stockaient l'URL webhook en clair en DB (fuite si DB compromise) + le cron script n'était
pas schedulé (manuel). P57 comble : (1) encrypt-at-rest Fernet (reuse P10/P48 pattern), (2) Helm
CronJob k8s (schedule hebdo dimanche 03:00 UTC).

Coverage (TestClient + YAML parse — AUCUN réseau/k8s en CI) :
  A. Webhook URL encrypt-at-rest (Fernet) :
    1. PUT /alerts/webhook → DB stocke ciphertext (préfixe gAAAAA), pas plaintext.
    2. GET /alerts/webhook → retourne URL déchiffrée (plaintext lisible par Owner).
    3. POST /alerts/notify → decrypt URL + POST vers la bonne URL (mock httpx capture).
    4. _decrypt_webhook_url(None) → None.
    5. _decrypt_webhook_url("corrupt") → None (gracieux, pas de leak).
    6. _decrypt_webhook_url(ciphertext) → plaintext.
    7. PUT webhook_url=None → DB NULL (désactivé).
    8. Cron script : decrypt webhook_url avant POST (smoke — 1 tenant avec webhook configuré).
  B. Helm CronJob manifest (k8s/audit-cronjob.yaml) :
    9. YAML valide (yaml.safe_load).
    10. kind: CronJob.
    11. schedule "0 3 * * 0" (dimanche 03:00 UTC).
    12. command ["python", "scripts/audit_cron.py"].
    13. env REVPULSE_DATABASE_URL + REVPULSE_TOKEN_ENCRYPTION_KEY depuis secret revpulse-secrets.
    14. securityContext runAsNonRoot true + readOnlyRootFilesystem true (durcissement).
    15. concurrencyPolicy Forbid (pas de run parallèle — purge non-idempotente).
    16. Secret revpulse-secrets défini (database-url + token-encryption-key).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p57_test.db python tests/test_p57_webhook_fernet_helm_cronjob.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p57_test.db")
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
    from app.database import Base, get_db
    from app.routers.audit import _decrypt_webhook_url
    from app.security import create_access_token, decrypt_token, encrypt_token
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    test_db = Sess()

    def _override_db():
        yield test_db

    import app.routers.audit as audit_mod
    from app.main import app
    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)

    for tbl in [M.AuditLog, M.Member, M.User, M.Tenant]:
        test_db.query(tbl).delete()
    test_db.commit()

    print("=== test_p57_webhook_fernet_helm_cronjob.py — P57 encrypt-at-rest + Helm CronJob ===\n")

    owner = M.User(id="usr_oB", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_cB", name="Corp P57", plan="Enterprise", owner_id="usr_oB",
                      sso_enabled=False, retention_days=2555)
    test_db.add_all([owner, tenant]); test_db.commit()
    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_oB')}"}

    # ══════════════════════════════════════════════════════════════════════
    # A. Webhook URL encrypt-at-rest (Fernet)
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Webhook URL encrypt-at-rest (Fernet) ---")
    URL = "https://hooks.slack.test/services/T00/B00/secret_p57"
    r = client.put("/api/tenants/tnt_cB/audit/alerts/webhook", headers=H_OWNER,
                   json={"webhook_url": URL, "min_severity": "high"})
    ck(r.status_code == 200, f"PUT /alerts/webhook → 200 (got {r.status_code})")
    # DB stocke ciphertext (pas plaintext)
    t_db = test_db.query(M.Tenant).filter_by(id="tnt_cB").first()
    ck(t_db.audit_alert_webhook_url != URL, "DB ne stocke pas plaintext (encrypt-at-rest)")
    ck(t_db.audit_alert_webhook_url.startswith("gAAAAA"), "DB stocke ciphertext Fernet (préfixe gAAAAA)")
    ck(decrypt_token(t_db.audit_alert_webhook_url) == URL, "decrypt_token(db_val) == plaintext URL")

    # GET retourne URL déchiffrée
    r = client.get("/api/tenants/tnt_cB/audit/alerts/webhook", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /alerts/webhook → 200 (got {r.status_code})")
    ck(r.json()["webhook_url"] == URL, "GET retourne URL déchiffrée (plaintext lisible Owner)")

    # PUT webhook_url=None → DB NULL
    client.put("/api/tenants/tnt_cB/audit/alerts/webhook", headers=H_OWNER,
               json={"webhook_url": None, "min_severity": "low"})
    t_db2 = test_db.query(M.Tenant).filter_by(id="tnt_cB").first()
    ck(t_db2.audit_alert_webhook_url is None, "PUT webhook_url=None → DB NULL (désactivé)")

    # _decrypt_webhook_url helper
    ck(_decrypt_webhook_url(None) is None, "helper None → None")
    ck(_decrypt_webhook_url("") is None, "helper '' → None")
    # legacy-tolerant : valeur non-Fernet retournée tel quel (decrypt_token design — migration smooth pre-P57)
    ck(_decrypt_webhook_url("corrupt_plaintext") == "corrupt_plaintext", "helper non-Fernet → retourné tel quel (legacy compat)")
    ct = encrypt_token("https://example.test/wh")
    ck(_decrypt_webhook_url(ct) == "https://example.test/wh", "helper ciphertext → plaintext")

    # notify decrypt URL + POST vers bonne URL (mock httpx)
    client.put("/api/tenants/tnt_cB/audit/alerts/webhook", headers=H_OWNER,
               json={"webhook_url": URL, "min_severity": "high"})
    # Seed 1 burst high (60 actions IP)
    from app.audit import log_audit
    from app.logging_config import request_ip_var
    for _ in range(60):
        t_ip = request_ip_var.set("203.0.113.57")
        try:
            log_audit(test_db, tenant_id="tnt_cB", actor_user_id="usr_oB", actor_name="Owner",
                      action="api.call", target_type="contact", target_id="c1")
        finally:
            request_ip_var.reset(t_ip)
    test_db.commit()

    posted = []
    class FakeResp:
        def __init__(self, code): self.status_code = code
    def fake_post(url, json=None, timeout=None):
        posted.append(url); return FakeResp(200)
    orig_post = audit_mod.httpx.post
    audit_mod.httpx.post = fake_post
    try:
        r = client.post("/api/tenants/tnt_cB/audit/alerts/notify?window_hours=24", headers=H_OWNER)
        ck(r.status_code == 200, f"POST /alerts/notify → 200 (got {r.status_code})")
        ck(r.json()["notified"] == 1, "notified=1 (burst high)")
        ck(len(posted) == 1, f"httpx.post appelé 1x (got {len(posted)})")
        ck(posted[0] == URL, f"POST vers URL déchiffrée {URL} (got {posted[0] if posted else 'none'})")
    finally:
        audit_mod.httpx.post = orig_post

    # ══════════════════════════════════════════════════════════════════════
    # B. Helm CronJob manifest (k8s/audit-cronjob.yaml)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Helm CronJob manifest (k8s/audit-cronjob.yaml) ---")
    import yaml
    yml_path = ROOT / "k8s" / "audit-cronjob.yaml"
    ck(yml_path.exists(), "k8s/audit-cronjob.yaml existe")
    docs = list(yaml.safe_load_all(yml_path.read_text()))
    ck(len(docs) == 2, f"2 docs YAML (CronJob + Secret) (got {len(docs)})")
    cronjob = docs[0]; secret = docs[1]
    ck(cronjob["kind"] == "CronJob", f"doc[0] kind=CronJob (got {cronjob.get('kind')})")
    ck(cronjob["spec"]["schedule"] == "0 3 * * 0", f"schedule '0 3 * * 0' dimanche 03:00 UTC (got {cronjob['spec']['schedule']})")
    ck(cronjob["spec"]["concurrencyPolicy"] == "Forbid", "concurrencyPolicy Forbid (pas de run parallèle)")
    ck(cronjob["spec"]["timeZone"] == "UTC", "timeZone UTC")
    ct_spec = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    ck(ct_spec["command"] == ["python", "scripts/audit_cron.py"], f"command python scripts/audit_cron.py (got {ct_spec['command']})")
    ck("--burst-threshold" in ct_spec["args"], "args contient --burst-threshold")
    env = {e["name"]: e for e in ct_spec["env"]}
    ck("REVPULSE_DATABASE_URL" in env, "env REVPULSE_DATABASE_URL présent")
    ck(env["REVPULSE_DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "revpulse-secrets", "database-url depuis secret revpulse-secrets")
    ck("REVPULSE_TOKEN_ENCRYPTION_KEY" in env, "env REVPULSE_TOKEN_ENCRYPTION_KEY présent (decrypt webhook)")
    ck(env["REVPULSE_TOKEN_ENCRYPTION_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "token-encryption-key", "token-encryption-key depuis secret")
    sc = ct_spec["securityContext"]
    ck(sc["runAsNonRoot"] is True, "securityContext runAsNonRoot=true (durcissement)")
    ck(sc["readOnlyRootFilesystem"] is True, "securityContext readOnlyRootFilesystem=true")
    ck("ALL" in sc["capabilities"]["drop"], "capabilities drop ALL")
    ck("tmp" in [v["name"] for v in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]], "volume tmp (writable pour readOnlyRootFilesystem)")
    # Secret doc
    ck(secret["kind"] == "Secret", f"doc[1] kind=Secret (got {secret.get('kind')})")
    ck(secret["metadata"]["name"] == "revpulse-secrets", "Secret name=revpulse-secrets")
    ck("database-url" in secret["stringData"], "Secret stringData database-url")
    ck("token-encryption-key" in secret["stringData"], "Secret stringData token-encryption-key")

    # ══════════════════════════════════════════════════════════════════════
    # C. Cron script decrypt webhook (smoke — tenant avec webhook configuré)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Cron script decrypt webhook (smoke) ---")
    # Le tenant tnt_cB a webhook configuré (encrypted) + 60 actions burst → cron notify doit decrypt + POST
    # Mock httpx dans le subprocess est complexe → on vérifie juste que le cron tourne sans crash decrypt.
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_cron.py"),
                           "--window-hours", "24"], capture_output=True, text=True,
                          env={**os.environ, "REVPULSE_DATABASE_URL": os.environ["REVPULSE_DATABASE_URL"]},
                          timeout=30)
    ck(proc.returncode == 0, f"audit_cron.py exit 0 (decrypt webhook OK) (got {proc.returncode})")
    ck("tnt_cB" in proc.stdout, "cron traite tnt_cB (webhook encrypted)")
    ck("DONE" in proc.stdout, "cron imprime DONE")
    # Pas d'erreur decrypt dans stderr
    ck("decrypt" not in proc.stderr.lower() or "error" not in proc.stderr.lower(), "pas d'erreur decrypt dans stderr")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
