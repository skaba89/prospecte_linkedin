#!/usr/bin/env python3
"""test_audit_ui.py — validation statique de l'audit & compliance UI (P21).

Couvre (par lecture des sources + introspection runtime avec sqlite in-memory) :
  - schemas.py : AuditCountItem (label/count), AuditStatsOut (total/by_action/by_actor).
  - routers/audit.py : list_audit avec filtres query (action prefix LIKE, actor_user_id,
    target_type, since/until datetime), GET /stats (total + top 10 actions + top 10 acteurs,
    group_by + func.count), GET /export (CSV Response text/csv + Content-Disposition, mêmes
    filtres), _apply_filters helper partagé, RBAC workspace.manage sur les 3 endpoints.
  - frontend audit.html : filtres (action/actor/target/since/until), stats (total + top actions
    + top acteurs), table, export CSV, pagination.
  - main.py : audit.router enregistré (déjà là depuis P3, confirmé non-cassé).
  - CI : test_audit_ui.py dans static checks.
  - Runtime : _apply_filters applique action LIKE prefix + exact match actor/target +
    bornes since/until ; stats endpoint shape {total, by_action[], by_actor[]} avec count > 0.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


print("=== test_audit_ui.py — Audit & compliance UI (P21) ===\n")

# ─── Source : schemas.py ───────────────────────────────────────────────────────
schemas_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")

print("=== schemas.py : AuditStatsOut + AuditCountItem ===")
ck("class AuditCountItem" in schemas_src, True, "AuditCountItem définie")
ck("class AuditStatsOut" in schemas_src, True, "AuditStatsOut définie")
ck("total: int" in schemas_src, True, "AuditStatsOut.total")
ck("by_action: list[AuditCountItem]" in schemas_src, True, "by_action list[AuditCountItem]")
ck("by_actor: list[AuditCountItem]" in schemas_src, True, "by_actor list[AuditCountItem]")
ck("label: str" in schemas_src, True, "AuditCountItem.label")
ck("count: int" in schemas_src, True, "AuditCountItem.count")

# ─── Source : routers/audit.py ─────────────────────────────────────────────────
rtr = (ROOT / "backend" / "app" / "routers" / "audit.py").read_text(encoding="utf-8")

print("\n=== routers/audit.py : filtres + stats + export ===")
ck('prefix="/api/tenants/{tid}/audit"' in rtr, True, "prefix audit")
ck("def _apply_filters" in rtr, True, "_apply_filters helper (shared list/export)")
ck('.like(f"{action}%")' in rtr, True, "action = prefix match LIKE (famille d'actions)")
ck("actor_user_id ==" in rtr, True, "filtre actor_user_id exact")
ck("target_type ==" in rtr, True, "filtre target_type exact")
ck("created_at >=" in rtr, True, "borne since (created_at >= since)")
ck("created_at <=" in rtr, True, "borne until (created_at <= until)")
ck("from fastapi import APIRouter" in rtr and "Query" in rtr and "Response" in rtr, True, "imports Query + Response (P55 +ajoute HTTPException/status)")
ck("action: str | None = Query" in rtr, True, "param action via Query")
ck("since: datetime | None = Query" in rtr, True, "param since datetime via Query")
ck("until: datetime | None = Query" in rtr, True, "param until datetime via Query")
ck("def list_audit" in rtr, True, "list_audit (filtrée)")
ck("def audit_stats" in rtr, True, "audit_stats endpoint")
ck("def export_audit" in rtr, True, "export_audit endpoint")
ck("func.count" in rtr, True, "stats via func.count (group_by)")
ck("group_by(models.AuditLog.action)" in rtr, True, "group_by action (top actions)")
ck("group_by(models.AuditLog.actor_name)" in rtr, True, "group_by actor_name (top acteurs)")
ck(".limit(10)" in rtr, True, "top 10 (limit)")
ck("import csv" in rtr, True, "export CSV via module csv")
ck('media_type="text/csv"' in rtr, True, "export Response media_type text/csv")
ck("Content-Disposition" in rtr, True, "Content-Disposition attachment")
ck('attachment; filename="audit_' in rtr, True, "filename audit_{tid}.csv")
ck('require_permission("workspace.manage")' in rtr, True, "RBAC workspace.manage sur les endpoints")
# P53+P55 : 6 endpoints GET (list + stats + export CSV + export-pdf + alerts + alerts/webhook config)
#         + 1 PUT (alerts/webhook config) + 2 POST (purge rétention P53 + alerts/notify webhook P55)
ck(rtr.count("@router.get"), 6, "6 endpoints GET (list + stats + export + export-pdf P53 + alerts P53 + webhook P55)")
ck(rtr.count("@router.post"), 2, "2 endpoints POST (purge rétention P53 + alerts/notify webhook P55)")
ck(rtr.count("@router.put"), 1, "1 endpoint PUT (alerts/webhook config P55)")
ck("export-pdf" in rtr, True, "endpoint export-pdf signé HMAC (P53)")
ck("/alerts" in rtr, True, "endpoint alerts compliance (P53)")
ck("/alerts/notify" in rtr, True, "endpoint notify webhook (P55)")
ck("/alerts/webhook" in rtr, True, "endpoint config webhook (P55)")
ck("/purge" in rtr, True, "endpoint purge rétention (P53)")

# ─── Source : main.py (audit déjà enregistré depuis P3) ────────────────────────
main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : audit router enregistré ===")
ck("audit.router" in main, True, "audit.router dans le tuple (non-cassé par P21)")

# ─── Frontend : audit.html ─────────────────────────────────────────────────────
html = (ROOT / "frontend" / "public" / "audit.html").read_text(encoding="utf-8")
print("\n=== frontend audit.html ===")
ck(bool(html), True, "audit.html existe")
ck("f-action" in html, True, "filtre action")
ck("f-actor" in html, True, "filtre actor")
ck("f-target" in html, True, "filtre target_type")
ck("f-since" in html, True, "filtre since (datetime-local)")
ck("f-until" in html, True, "filtre until (datetime-local)")
ck("loadStats" in html, True, "chargement stats")
ck("loadAudit" in html, True, "chargement table audit")
ck("exportCsv" in html, True, "export CSV bouton")
ck("/audit/stats" in html, True, "appel endpoint /audit/stats")
ck("/audit/export" in html, True, "appel endpoint /audit/export")
ck("X-Total-Count" in html, True, "pagination via X-Total-Count")
ck("actions-list" in html, True, "datalist actions (suggestions préfixes)")

# ─── Introspection runtime (sqlite in-memory) ─────────────────────────────────
print("\n=== Introspection runtime : _apply_filters + stats shape ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M
    from app import schemas as S
    from app.routers import audit as aud_rtr

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    # seed user + tenant
    u = M.User(id="usr_a", email="a@test.com", name="Alice", password_hash="x")
    s.add(u); s.flush()
    t = M.Tenant(id="tnt_a", name="A", plan="Starter", owner_id="usr_a")
    s.add(t); s.flush()
    # seed audit logs variés
    now = datetime.utcnow()
    s.add_all([
        M.AuditLog(tenant_id="tnt_a", actor_user_id="usr_a", actor_name="Alice", action="crm.contact.create", target_type="contact", target_id="ctc_1", created_at=now - timedelta(hours=1)),
        M.AuditLog(tenant_id="tnt_a", actor_user_id="usr_a", actor_name="Alice", action="crm.contact.update", target_type="contact", target_id="ctc_1", created_at=now - timedelta(minutes=30)),
        M.AuditLog(tenant_id="tnt_a", actor_user_id="usr_a", actor_name="Alice", action="crm.contact.delete", target_type="contact", target_id="ctc_2", created_at=now - timedelta(minutes=10)),
        M.AuditLog(tenant_id="tnt_a", actor_user_id="usr_a", actor_name="Alice", action="webhook.create", target_type="webhook", target_id="whk_1", created_at=now - timedelta(days=2)),
        M.AuditLog(tenant_id="tnt_a", actor_user_id=None, actor_name="Inbound webhook: Form", action="inbound.contact_created", target_type="contact", target_id="ctc_3", created_at=now),
        M.AuditLog(tenant_id="tnt_a", actor_user_id="usr_a", actor_name="Alice", action="crm.opportunity.create", target_type="opportunity", target_id="opp_1", created_at=now - timedelta(days=5)),
    ])
    s.flush()

    # _apply_filters : action prefix crm.contact → 3 (create/update/delete)
    q = aud_rtr._apply_filters(s.query(M.AuditLog).filter_by(tenant_id="tnt_a"), action="crm.contact", actor_user_id=None, target_type=None, since=None, until=None)
    ck(q.count(), 3, "_apply_filters action=crm.contact → 3 (prefix match)")

    # _apply_filters : target_type=contact → 4 (3 crm.contact + 1 inbound)
    q2 = aud_rtr._apply_filters(s.query(M.AuditLog).filter_by(tenant_id="tnt_a"), action=None, actor_user_id=None, target_type="contact", since=None, until=None)
    ck(q2.count(), 4, "_apply_filters target_type=contact → 4")

    # _apply_filters : since (last 24h) → 4 (crm.contact.create -1h, update -30m, delete -10m, inbound now)
    q3 = aud_rtr._apply_filters(s.query(M.AuditLog).filter_by(tenant_id="tnt_a"), action=None, actor_user_id=None, target_type=None, since=now - timedelta(hours=24), until=None)
    ck(q3.count(), 4, "_apply_filters since=24h → 4 (last day)")

    # _apply_filters : actor_user_id=usr_a → 5 (les 6 sauf inbound qui a actor_user_id=None)
    q4 = aud_rtr._apply_filters(s.query(M.AuditLog).filter_by(tenant_id="tnt_a"), action=None, actor_user_id="usr_a", target_type=None, since=None, until=None)
    ck(q4.count(), 5, "_apply_filters actor=usr_a → 5 (exclut système inbound)")

    # stats shape : total=6, by_action contient crm.contact.create, by_actor contient Alice
    stats = aud_rtr.audit_stats(tid="tnt_a", db=s)
    ck(isinstance(stats, S.AuditStatsOut), True, "audit_stats retourne AuditStatsOut")
    ck(stats.total, 6, "stats.total = 6")
    actions_labels = [b.label for b in stats.by_action]
    ck("crm.contact.create" in actions_labels, True, "stats.by_action contient crm.contact.create")
    ck(stats.by_action[0].count >= 1, True, "stats.by_action[0].count >= 1 (trié décroissant)")
    actors_labels = [b.label for b in stats.by_actor]
    ck("Alice" in actors_labels, True, "stats.by_actor contient Alice")
    # by_actor exclut actor_name="" (système sans nom) — mais "Inbound webhook: Form" a un nom → inclus
    ck("Inbound webhook: Form" in actors_labels, True, "stats.by_actor contient 'Inbound webhook: Form' (actor_name non-vide)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
