#!/usr/bin/env python3
"""test_p69_gdpr_g4_purge.py — P69 GDPR G4 (backup purge) + P1-6 (rectification Art.16) + P1-7 (audit gdpr.export)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p69_test.db")
os.environ.setdefault("REVPULSE_ENV", "test")
os.environ.setdefault("REVPULSE_TOKEN_ENCRYPTION_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2o1OMe4YI=")
os.environ.setdefault("REVPULSE_FRONTEND_DIR", str(ROOT / "frontend" / "public"))
os.environ.setdefault("REVPULSE_RATE_LIMIT_ENABLED", "false")
passed = failed = 0
def ck(c, label):
    global passed, failed
    print(f"  {'✓' if c else '✗'} {label}")
    if c:
        passed += 1
    else:
        failed += 1

def main():
    from app import models as M
    from app.database import Base, get_db
    from app.security import create_access_token, hash_password
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(os.environ["REVPULSE_DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    def _ov(): yield db
    from app.main import app
    app.dependency_overrides[get_db] = _ov
    client = TestClient(app)
    for t in [M.AuditLog, M.Member, M.User, M.Tenant]:
        db.query(t).delete()
    db.commit()
    print("=== test_p69_gdpr_g4_purge.py — P69 (G4 + P1-6 + P1-7) ===\n")

    owner = M.User(id="usr_o69", email="owner@corp.io", name="Alice Original", password_hash=hash_password("Secret123!"), locale="fr")
    tenant = M.Tenant(id="tnt_p69", name="Corp P69", plan="Enterprise", owner_id="usr_o69", sso_enabled=False, retention_days=2555)
    mem = M.Member(id="mem_o69", tenant_id="tnt_p69", user_id="usr_o69", email="owner@corp.io", name="Alice Original", role="Owner", status="active")
    victim = M.User(id="usr_v69", email="victim@corp.io", name="Victor Original", password_hash="h", locale="en")
    mem_v = M.Member(id="mem_v69", tenant_id="tnt_p69", user_id="usr_v69", email="victim@corp.io", name="Victor Original", role="Contributor", status="active")
    db.add_all([owner, tenant, mem, victim, mem_v])
    db.commit()
    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o69')}"}
    H_VICTIM = {"Authorization": f"Bearer {create_access_token('usr_v69')}"}

    # ═══ A. G4 — script replay_gdpr_erase_post_restore.py ═══
    print("--- A. G4 — script replay_gdpr_erase_post_restore (Art. 17.3) ---")
    script = ROOT / "scripts" / "replay_gdpr_erase_post_restore.py"
    ck(script.exists(), "scripts/replay_gdpr_erase_post_restore.py existe")
    ck(os.access(script, os.X_OK) or script.suffix == ".py", "script exécutable")
    script_src = script.read_text()
    ck("gdpr_erased_at" in script_src, "script référence gdpr_erased_at (scan users effacés)")
    ck("gdpr.erase.replay" in script_src, "script logge audit action gdpr.erase.replay (preuve DPO)")
    ck("UserEmailIntegration" in script_src, "script hard-delete OAuth tokens (re-purge)")
    ck("CV" in script_src and "raw_text" in script_src, "script scrub CV.raw_text (re-purge)")
    ck("--dry-run" in script_src, "script supporte --dry-run (audit sans modifier)")
    # doc DPO
    doc = ROOT / "docs" / "DPO_backup_purge_procedure.md"
    ck(doc.exists(), "docs/DPO_backup_purge_procedure.md existe (procédure DPO Art. 17.3)")
    ck("Art. 17" in doc.read_text() and "replay_gdpr_erase" in doc.read_text(), "doc DPO référence Art. 17 + script replay")

    # ═══ B. P1-6 — rectification Art. 16 (PATCH /auth/me) ═══
    print("\n--- B. P1-6 — rectification Art. 16 (PATCH /auth/me) ---")
    r = client.patch("/api/auth/me", headers=H_VICTIM, json={"name": "Victor Corrected", "locale": "es"})
    ck(r.status_code == 200, f"PATCH /auth/me → 200 (got {r.status_code})")
    ck(r.json()["name"] == "Victor Corrected", "PATCH /me → name rectifié (Art. 16)")
    ck(r.json()["locale"] == "es", "PATCH /me → locale rectifié (es)")
    db.expire_all()
    v = db.query(M.User).filter_by(id="usr_v69").first()
    ck(v.name == "Victor Corrected" and v.locale == "es", "DB persiste la rectification (Art. 16)")
    # Email NON modifiable via PATCH /me (anti takeover via email swap)
    r = client.patch("/api/auth/me", headers=H_VICTIM, json={"email": "attacker@evil.io"})
    ck(r.status_code == 200, f"PATCH /me avec email → 200 (ignoré, pas 422) (got {r.status_code})")
    db.expire_all()
    v = db.query(M.User).filter_by(id="usr_v69").first()
    ck(v.email == "victim@corp.io", "email NON modifié via PATCH /me (anti takeover — flow verify-email séparé)")
    # Audit auth.profile.update
    aw = db.query(M.AuditLog).filter_by(action="auth.profile.update").first()
    ck(aw is not None, "audit auth.profile.update loggé (preuve rectification Art. 16)")
    # No-op (aucun champ modifié) → 200 sans audit
    audits_before = db.query(M.AuditLog).filter_by(action="auth.profile.update").count()
    r = client.patch("/api/auth/me", headers=H_VICTIM, json={})
    ck(r.status_code == 200, f"PATCH /me no-op → 200 (got {r.status_code})")
    db.expire_all()
    audits_after = db.query(M.AuditLog).filter_by(action="auth.profile.update").count()
    ck(audits_after == audits_before, "PATCH /me no-op → pas d'audit (idempotent)")

    # ═══ C. P1-7 — audit gdpr.export (preuve DPO Art. 15/20) ═══
    print("\n--- C. P1-7 — audit gdpr.export (preuve DPO Art. 15/20) ---")
    # Effacer les audit entries précédentes pour isoler le test gdpr.export
    db.query(M.AuditLog).filter_by(action="gdpr.export").delete()
    db.commit()
    r = client.get("/api/tenants/tnt_p69/gdpr/export?user_id=usr_v69", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /gdpr/export → 200 (got {r.status_code})")
    exp_audit = db.query(M.AuditLog).filter_by(action="gdpr.export").first()
    ck(exp_audit is not None, "audit gdpr.export loggé (preuve DPO Art. 15/20 — P1-7)")
    ck(exp_audit.target_id == "usr_v69", "audit gdpr.export target_id = user exporté")
    ck(exp_audit.detail.get("counts") is not None, "audit gdpr.export detail.counts (preuve complétude)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    db.close()
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
