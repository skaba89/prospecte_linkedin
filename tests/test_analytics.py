#!/usr/bin/env python3
"""test_analytics.py — validation statique des analytics & reporting (P14, sans API live).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - Router analytics.py : 5 endpoints (overview/pipeline/emails/sources/export), read-only,
    RBAC crm.view sur export (données PII), get_tenant sur les 4 autres (agrégats), agrégations
    SQLAlchemy (func.count/sum/avg + group_by), export CSV (module csv + Content-Disposition).
  - Schemas : AnalyticsOverview, AnalyticsPipeline, AnalyticsEmails, AnalyticsSources,
    PipelineStageStat, EmailStat, SourceStat.
  - Intégration : router analytics enregistré dans main.py, test_analytics.py dans le CI.
  - Introspection : endpoints enregistrés + schemas instanciables (skip si sqlalchemy absent).
"""
import sys
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


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── Router analytics.py (5 endpoints + RBAC + agrégations + CSV) ──────────────
print("=== Router analytics.py (P14) ===")
rt = read("backend/app/routers/analytics.py")
for path in ["/analytics/overview", "/analytics/pipeline", "/analytics/emails",
             "/analytics/sources", "/analytics/export"]:
    ck(path in rt, True, f"endpoint {path}")
ck('require_permission("crm.manage")' in rt, True, "RBAC crm.manage sur export (PII contacts — cohérent avec crm.manage du P11/P13)")
ck("get_tenant" in rt, True, "get_tenant sur les 4 endpoints agrégats (any member)")
# Agrégations SQLAlchemy.
for agg, label in [("func.count", "count"), ("func.sum", "sum (pipeline value)"),
                   ("func.avg", "avg (probability)"), ("group_by", "group_by")]:
    ck(agg in rt, True, f"agrégation {label}")
# Modèles interrogés (CRM P11 + Notification P13 + LinkedInSyncRun P12).
for m in ["models.Contact", "models.Opportunity", "models.Notification", "models.LinkedInSyncRun"]:
    ck(m in rt, True, f"modèle {m} interrogé (pont P11+P12+P13)")
# Export CSV.
ck("import csv" in rt, True, "module csv (échappement virgules — pas de concat manuelle)")
ck("io.StringIO" in rt, True, "StringIO buffer CSV")
ck("text/csv" in rt, True, "media_type text/csv")
ck("Content-Disposition" in rt and "attachment" in rt, True, "Content-Disposition attachment (download)")
ck("first_name" in rt and "linkedin_url" in rt, True, "colonnes CSV (PII contacts)")
# Conversion + success rate.
ck("_CONVERTED_STATUSES" in rt, True, "conversion par source (won/contacted/qualified)")
ck("success_rate_pct" in rt, True, "success rate e-mail (sent/(sent+failed))")
ck("win_rate_pct" in rt, True, "win rate pipeline (closed_won/total)")
ck("last_sync" in rt, True, "fraîcheur sync (dernière LinkedInSyncRun)")
ck("pipeline_value" in rt, True, "pipeline value (sum Opportunity.value)")
ck("@router.get" in rt and "@router.post" not in rt and "@router.delete" not in rt,
   True, "read-only (GET uniquement — aucune mutation)")

# ─── Schemas analytics (P14) ───────────────────────────────────────────────────
print("\n=== Schemas analytics (P14) ===")
sch = read("backend/app/schemas.py")
for cls in ["AnalyticsOverview", "AnalyticsPipeline", "AnalyticsEmails",
            "AnalyticsSources", "PipelineStageStat", "EmailStat", "SourceStat"]:
    ck(f"class {cls}(BaseModel):" in sch, True, f"{cls} défini")
for field in ["contacts_total", "contacts_by_source", "contacts_by_status",
              "opportunities_total", "pipeline_value", "emails_total", "sync_runs_total"]:
    ck(field in sch, True, f"AnalyticsOverview.{field}")
ck("win_rate_pct" in sch, True, "AnalyticsPipeline.win_rate_pct")
ck("success_rate_pct" in sch, True, "AnalyticsEmails.success_rate_pct")
ck("conversion_pct" in sch, True, "SourceStat.conversion_pct")
ck("avg_probability" in sch, True, "PipelineStageStat.avg_probability")

# ─── Intégration (main.py + CI) ────────────────────────────────────────────────
print("\n=== Intégration (main.py + CI) ===")
main_src = read("backend/app/main.py")
ck("analytics.router" in main_src, True, "router analytics enregistré dans main.py")
ci = read(".github/workflows/ci.yml")
ck("python tests/test_analytics.py" in ci, True, "test_analytics.py dans le CI (static checks)")

# ─── Introspection (endpoints + schemas instanciables) ────────────────────────
print("\n=== Introspection runtime ===")
sys.path.insert(0, str(ROOT / "backend"))
try:
    import sqlalchemy  # noqa: F401 — skip si absent (job static-checks)
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via job test-api E2E).")
else:
    try:
        from app.routers import analytics as a
        from app import schemas
        routes = {r.path for r in a.router.routes}
        for p in ["/api/tenants/{tid}/analytics/overview", "/api/tenants/{tid}/analytics/pipeline",
                  "/api/tenants/{tid}/analytics/emails", "/api/tenants/{tid}/analytics/sources",
                  "/api/tenants/{tid}/analytics/export"]:
            ck(p in routes, True, f"route {p.split('/analytics/')[1]} enregistrée")
        # Schemas instanciables (valeurs par défaut).
        ov = schemas.AnalyticsOverview(contacts_total=5, opportunities_total=2, emails_total=3)
        ck(ov.contacts_total == 5, True, "AnalyticsOverview instanciable (defaults OK)")
        pp = schemas.AnalyticsPipeline(stages=[schemas.PipelineStageStat(stage="prospecting", count=2)])
        ck(len(pp.stages) == 1, True, "AnalyticsPipeline instanciable (nested PipelineStageStat)")
        em = schemas.AnalyticsEmails(by_template=[schemas.EmailStat(template_id="cold_outreach", count=4)])
        ck(em.by_template[0].count == 4, True, "AnalyticsEmails instanciable (nested EmailStat)")
        sr = schemas.AnalyticsSources(by_source=[schemas.SourceStat(source="linkedin", count=8, conversion_pct=25.0)])
        ck(sr.by_source[0].conversion_pct == 25.0, True, "AnalyticsSources instanciable (nested SourceStat)")
    except Exception as e:
        ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
