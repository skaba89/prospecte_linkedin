#!/usr/bin/env python3
"""test_p52_audit_log_ui.py — P52 Audit log UI frontend + filtre IP backend + CSV ip/user_agent.

Coverage (TestClient, AUCUN réseau externe en CI) :
  A. Page audit.html se charge + expose les nouveaux éléments P52 :
    1. GET /audit.html → 200, content-type text/html.
    2. Contient la colonne "Source (IP / UA)" dans le thead.
    3. Contient le filtre IP (input #f-ip + label "IP source").
    4. Contient la fonction filterByIp (clic IP → filtre).
    5. Contient les nouvelles actions P48 dans les datalists (sso.config, billing.plan, billing.currency).
    6. Contient sso_config dans le datalist targets.
    7. viewport meta présent (responsive).
  B. Lien "Journal d'audit" dans index.html :
    8. GET / → 200, contient href="audit.html" + titre "Journal d'audit".
  C. Backend filtre IP (P52 — _apply_filters + list + export) :
    9. GET /audit?ip=203.0.113.42 → ne retourne que les entries avec cette IP.
    10. GET /audit sans filtre ip → retourne tout.
    11. GET /audit?ip=inconnue → retourne [] (0 entries).
  D. CSV export inclut ip + user_agent (P52) :
    12. GET /audit/export → 200, header CSV contient "ip" + "user_agent".
    13. Lignes CSV contiennent les valeurs ip + user_agent.
  E. Permission gate (regression — workspace.manage) :
    14. Admin (pas workspace.manage) → GET /audit 403.
    15. Outsider → GET /audit 403 (get_tenant refuse).

Usage : REVPULSE_DATABASE_URL=sqlite:///./revpulse_p52_test.db python tests/test_p52_audit_log_ui.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("REVPULSE_DATABASE_URL", "sqlite:///./revpulse_p52_test.db")
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

    print("=== test_p52_audit_log_ui.py — P52 Audit log UI + filtre IP + CSV ip/user_agent ===\n")

    # Seed : owner + admin + outsider + tenant
    owner = M.User(id="usr_o5", email="owner@corp.io", name="Owner", password_hash="h", locale="fr")
    admin = M.User(id="usr_a5", email="admin@corp.io", name="Admin", password_hash="h", locale="fr")
    out = M.User(id="usr_x5", email="out@other.io", name="Outsider", password_hash="h", locale="fr")
    tenant = M.Tenant(id="tnt_c5", name="Corp Audit UI", plan="Enterprise", owner_id="usr_o5", sso_enabled=False)
    member_admin = M.Member(id="mem_a5", tenant_id="tnt_c5", user_id="usr_a5",
                            email="admin@corp.io", name="Admin", role="Admin", status="active")
    test_db.add_all([owner, admin, out, tenant, member_admin]); test_db.commit()

    H_OWNER = {"Authorization": f"Bearer {create_access_token('usr_o5')}"}
    H_ADMIN = {"Authorization": f"Bearer {create_access_token('usr_a5')}"}
    H_OUT = {"Authorization": f"Bearer {create_access_token('usr_x5')}"}

    # Seed audit entries avec IPs différentes (simule le contexte var manuellement)
    # log_audit lit ip depuis request_ip_var (set par middleware TestClient → on set manuellement ici)
    from app.logging_config import request_ip_var, request_ua_var
    for ip, ua, action in [
        ("203.0.113.42", "Mozilla/5.0 Chrome", "sso.config.upsert"),
        ("203.0.113.42", "Mozilla/5.0 Chrome", "billing.plan.change"),
        ("198.51.100.7", "curl/8.0", "member.invite"),
        ("198.51.100.7", "curl/8.0", "sso.config.delete"),
    ]:
        t_ip = request_ip_var.set(ip)
        t_ua = request_ua_var.set(ua)
        try:
            log_audit(test_db, tenant_id="tnt_c5", actor_user_id="usr_o5", actor_name="Owner",
                      action=action, target_type="tenant", target_id="tnt_c5", detail={"ip": ip})
        finally:
            request_ip_var.reset(t_ip)
            request_ua_var.reset(t_ua)
    test_db.commit()

    # ══════════════════════════════════════════════════════════════════════
    # A. Page audit.html se charge + expose les éléments P52
    # ══════════════════════════════════════════════════════════════════════
    print("--- A. Page audit.html (/audit.html) — éléments P52 ---")
    r = client.get("/audit.html")
    ck(r.status_code == 200, f"GET /audit.html → 200 (got {r.status_code})")
    html = r.text
    ck("Source (IP / UA)" in html, "colonne 'Source (IP / UA)' dans le thead")
    ck('id="f-ip"' in html, "filtre IP input #f-ip présent")
    ck("IP source" in html, "label 'IP source' présent")
    ck("filterByIp" in html, "fonction filterByIp présente (clic IP → filtre)")
    ck("sso.config" in html, "action sso.config dans datalist (P48 instrumenté)")
    ck("billing.plan" in html, "action billing.plan dans datalist (P48)")
    ck("billing.currency" in html, "action billing.currency dans datalist (P48)")
    ck("sso_config" in html, "target sso_config dans datalist (P48)")
    ck('name="viewport"' in html and 'width=device-width' in html, "viewport meta responsive présent")

    # ══════════════════════════════════════════════════════════════════════
    # B. Lien "Journal d'audit" dans index.html
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- B. Lien 'Journal d'audit' dans index.html (/) ---")
    r = client.get("/")
    ck(r.status_code == 200, f"GET / → 200 (got {r.status_code})")
    ck('href="audit.html"' in r.text, "lien href=\"audit.html\" présent dans index.html")
    ck("Journal d'audit" in r.text, "titre 'Journal d'audit' présent dans le lien nav")

    # ══════════════════════════════════════════════════════════════════════
    # C. Backend filtre IP (P52)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- C. Backend filtre IP (GET /audit?ip=) ---")
    r = client.get("/api/tenants/tnt_c5/audit", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit sans filtre → 200 (got {r.status_code})")
    all_entries = r.json()
    ck(len(all_entries) == 4, f"4 entries au total (got {len(all_entries)})")

    r = client.get("/api/tenants/tnt_c5/audit?ip=203.0.113.42", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit?ip=203.0.113.42 → 200 (got {r.status_code})")
    filtered = r.json()
    ck(len(filtered) == 2, f"2 entries pour ip=203.0.113.42 (got {len(filtered)})")
    ck(all(e.get("ip") == "203.0.113.42" for e in filtered), "toutes les entries ont ip=203.0.113.42")

    r = client.get("/api/tenants/tnt_c5/audit?ip=198.51.100.7", headers=H_OWNER)
    ck(len(r.json()) == 2, f"2 entries pour ip=198.51.100.7 (got {len(r.json())})")

    r = client.get("/api/tenants/tnt_c5/audit?ip=10.0.0.999", headers=H_OWNER)
    ck(len(r.json()) == 0, f"0 entries pour ip inconnue (got {len(r.json())})")

    # ip + user_agent présents dans la réponse (P48 fields exposés via AuditLogOut)
    ck(any(e.get("ip") for e in all_entries), "ip présent dans la réponse JSON")
    ck(any(e.get("user_agent") for e in all_entries), "user_agent présent dans la réponse JSON")

    # ══════════════════════════════════════════════════════════════════════
    # D. CSV export inclut ip + user_agent (P52)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- D. CSV export (ip + user_agent columns) ---")
    r = client.get("/api/tenants/tnt_c5/audit/export", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/export → 200 (got {r.status_code})")
    ck("text/csv" in r.headers.get("content-type", ""), "content-type text/csv")
    csv_text = r.text
    lines = csv_text.strip().split("\n")
    header = lines[0].strip()  # csv.writer ajoute \r\n → strip pour comparaison exacte
    header_cols = header.split(",")
    ck("ip" in header_cols, f"colonne 'ip' dans header CSV (got: {header[:80]})")
    ck("user_agent" in header_cols, f"colonne 'user_agent' dans header CSV (got: {header[:80]})")
    # Lignes contiennent les valeurs ip
    ck(any("203.0.113.42" in line for line in lines[1:]), "valeur ip=203.0.113.42 présente dans une ligne CSV")
    ck(any("Mozilla" in line for line in lines[1:]), "valeur user_agent (Mozilla) présente dans une ligne CSV")

    # Export avec filtre ip (même filtre que la liste)
    r = client.get("/api/tenants/tnt_c5/audit/export?ip=203.0.113.42", headers=H_OWNER)
    lines = r.text.strip().split("\n")
    ck(len(lines) == 3, f"export filtré ip=203.0.113.42 → header + 2 lignes (got {len(lines)})")

    # ══════════════════════════════════════════════════════════════════════
    # E. Permission gate (regression)
    # ══════════════════════════════════════════════════════════════════════
    print("\n--- E. Permission gate (workspace.manage) ---")
    r = client.get("/api/tenants/tnt_c5/audit", headers=H_ADMIN)
    ck(r.status_code == 403, f"Admin GET /audit → 403 (got {r.status_code})")
    r = client.get("/api/tenants/tnt_c5/audit", headers=H_OUT)
    ck(r.status_code == 403, f"Outsider GET /audit → 403 (got {r.status_code})")

    # Stats endpoint (regression — toujours fonctionne)
    r = client.get("/api/tenants/tnt_c5/audit/stats", headers=H_OWNER)
    ck(r.status_code == 200, f"GET /audit/stats → 200 (got {r.status_code})")
    ck(r.json()["total"] == 4, f"stats total=4 (got {r.json()['total']})")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    test_db.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
