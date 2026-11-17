#!/usr/bin/env python3
"""test_analytics_v2.py — validation statique + runtime de P24 Analytics v2 (funnel, cohorts, attribution).

Étend test_analytics.py (P14/P19 : overview/pipeline/emails/sources/export) avec 3 nouveaux endpoints
analytiques + le champ Opportunity.closed_at (time-to-close). Ne régresse pas le P19 — fichier séparé.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : Opportunity.closed_at (DateTime nullable, auto-set sur stage→closed_won).
  - schemas.py : FunnelStage, AnalyticsFunnel, CohortRow, AnalyticsCohorts, AttributionSource,
    AnalyticsAttribution + _FUNNEL_STAGES + OpportunityOut.closed_at.
  - routers/analytics.py : 3 endpoints funnel (cumulative + conversion + drop_off), cohorts
    (par mois d'acquisition + by_stage + win_rate + revenue_won + avg_time_to_close), attribution
    (join Contact.source → opps/won/revenue/win_rate/avg_deal_size + best_source).
  - routers/crm.py : update_opportunity + move_opportunity auto-set closed_at sur stage→closed_won.
  - migration 0012 : ALTER TABLE opportunities ADD COLUMN closed_at.
  - frontend analytics.html : sections funnel + cohorts + attribution.
  - Runtime : seed 6 opps (2 sources, 3 stages, 2 mois, 2 won avec closed_at) → funnel cumulative/
    conversion, cohorts par mois + time-to-close, attribution source→revenue + best_source,
    auto closed_at sur update_opportunity + move_opportunity.
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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


print("=== test_analytics_v2.py — Analytics v2 (P24) ===\n")

# ─── Source : models.py ────────────────────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : Opportunity.closed_at ===")
ck('closed_at = Column(DateTime, nullable=True)  # P24 : auto-set quand stage→closed_won' in models_src, True, "Opportunity.closed_at")

# ─── Source : schemas.py ───────────────────────────────────────────────────────
schemas_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : 6 nouveaux + OpportunityOut.closed_at ===")
ck("_FUNNEL_STAGES = [\"prospecting\", \"qualified\", \"proposal\", \"negotiation\", \"closed_won\"]" in schemas_src, True, "_FUNNEL_STAGES (chemin gagnant)")
ck("class FunnelStage(BaseModel):" in schemas_src, True, "FunnelStage")
ck("class AnalyticsFunnel(BaseModel):" in schemas_src, True, "AnalyticsFunnel")
ck("class CohortRow(BaseModel):" in schemas_src, True, "CohortRow")
ck("class AnalyticsCohorts(BaseModel):" in schemas_src, True, "AnalyticsCohorts")
ck("class AttributionSource(BaseModel):" in schemas_src, True, "AttributionSource")
ck("class AnalyticsAttribution(BaseModel):" in schemas_src, True, "AnalyticsAttribution")
ck("closed_at: datetime | None = None  # P24" in schemas_src, True, "OpportunityOut.closed_at")
ck("cumulative: int" in schemas_src, True, "FunnelStage.cumulative")
ck("conversion_from_prev_pct: float" in schemas_src, True, "FunnelStage.conversion_from_prev_pct")
ck("drop_off: int" in schemas_src, True, "FunnelStage.drop_off")
ck("avg_time_to_close_days" in schemas_src, True, "CohortRow.avg_time_to_close_days")
ck("revenue_won" in schemas_src, True, "AttributionSource.revenue_won")
ck("avg_deal_size" in schemas_src, True, "AttributionSource.avg_deal_size")
ck("best_source: str | None" in schemas_src, True, "AnalyticsAttribution.best_source")

# ─── Source : routers/analytics.py ─────────────────────────────────────────────
an_rtr = (ROOT / "backend" / "app" / "routers" / "analytics.py").read_text(encoding="utf-8")
print("\n=== routers/analytics.py : 3 endpoints v2 ===")
ck("@router.get(\"/tenants/{tid}/analytics/funnel\"" in an_rtr, True, "GET /analytics/funnel")
ck("@router.get(\"/tenants/{tid}/analytics/cohorts\"" in an_rtr, True, "GET /analytics/cohorts")
ck("@router.get(\"/tenants/{tid}/analytics/attribution\"" in an_rtr, True, "GET /analytics/attribution")
ck("def funnel(" in an_rtr, True, "funnel() défini")
ck("def cohorts(" in an_rtr, True, "cohorts() défini")
ck("def attribution(" in an_rtr, True, "attribution() défini")
ck("extract(\"year\", models.Opportunity.created_at)" in an_rtr, True, "cohorts : extract year de created_at")
ck("extract(\"month\", models.Opportunity.created_at)" in an_rtr, True, "cohorts : extract month de created_at")
ck("models.Opportunity.contact_id == models.Contact.id" in an_rtr, True, "attribution : join Opportunity→Contact")
ck("sum(stage_counts.get(s, 0) for s in funnel_stages[i:])" in an_rtr, True, "funnel : cumulative = somme stages >= i")
ck('stage == "closed_won"' in an_rtr, True, "cohorts/attribution : detection closed_won")
ck("by_source.sort(key=lambda x: x.revenue_won, reverse=True)" in an_rtr, True, "attribution : tri par revenue desc")
ck("from sqlalchemy import extract, func" in an_rtr, True, "import extract ajouté")

# ─── Source : routers/crm.py (auto closed_at) ──────────────────────────────────
crm_rtr = (ROOT / "backend" / "app" / "routers" / "crm.py").read_text(encoding="utf-8")
print("\n=== routers/crm.py : auto closed_at sur stage→closed_won ===")
ck('data.get("stage") == "closed_won" and o.stage != "closed_won"' in crm_rtr, True, "update_opportunity : auto-set closed_at sur stage→closed_won")
ck('o.closed_at = datetime.utcnow()' in crm_rtr, True, "update_opportunity : set closed_at")
ck('body.stage == "closed_won" and old != "closed_won"' in crm_rtr, True, "move_opportunity (Kanban) : auto-set closed_at")
ck('o.closed_at = None' in crm_rtr, True, "retour arrière → clear closed_at")

# ─── Migration 0012 ────────────────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0012_p24_analytics.py").read_text(encoding="utf-8")
print("\n=== migration 0012_p24_analytics ===")
ck('revision = "0012_p24_analytics"' in mig, True, "revision 0012_p24_analytics")
ck('down_revision = "0011_p23_tasks"' in mig, True, "down_revision 0011 (chaîne)")
ck('op.add_column("opportunities"' in mig, True, "ALTER TABLE opportunities ADD COLUMN")
ck('sa.Column("closed_at", sa.DateTime(), nullable=True)' in mig, True, "closed_at DateTime nullable")
ck("op.drop_column" in mig, True, "downgrade drop_column")

# ─── Frontend ──────────────────────────────────────────────────────────────────
an_html = (ROOT / "frontend" / "public" / "analytics.html").read_text(encoding="utf-8")
print("\n=== frontend analytics.html ===")
ck("loadFunnel" in an_html, True, "analytics.html loadFunnel")
ck("loadCohorts" in an_html, True, "analytics.html loadCohorts")
ck("loadAttribution" in an_html, True, "analytics.html loadAttribution")
ck("/analytics/funnel" in an_html, True, "analytics.html fetch /analytics/funnel")
ck("/analytics/cohorts" in an_html, True, "analytics.html fetch /analytics/cohorts")
ck("/analytics/attribution" in an_html, True, "analytics.html fetch /analytics/attribution")
ck("funnel-rows" in an_html, True, "analytics.html section funnel")
ck("cohorts-rows" in an_html, True, "analytics.html section cohorts")
ck("attribution-rows" in an_html, True, "analytics.html section attribution")
ck("avg_time_to_close_days" in an_html, True, "analytics.html affiche avg_time_to_close")
ck("best_source" in an_html, True, "analytics.html affiche best_source")

# ─── Introspection runtime (sqlite in-memory) ─────────────────────────────────
print("\n=== Introspection runtime : funnel + cohorts + attribution + auto closed_at ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M, schemas as S
    from app.routers import analytics as an, crm as crm_r

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_a", email="a@test.com", name="Alice", password_hash="x"))
    s.add(M.Tenant(id="tnt_a", name="A", plan="Starter", owner_id="usr_a"))
    s.flush()
    c1 = M.Contact(tenant_id="tnt_a", first_name="C1", last_name="X", email="c1@test.com", status="new", source="linkedin")
    c2 = M.Contact(tenant_id="tnt_a", first_name="C2", last_name="Y", email="c2@test.com", status="new", source="manual")
    s.add_all([c1, c2]); s.flush()
    mem = SimpleNamespace(user_id="usr_a", name="Alice", role="owner", tenant_id="tnt_a", status="active")

    opps = [
        M.Opportunity(tenant_id="tnt_a", contact_id=c1.id, title="o1", value=1000, stage="prospecting", created_at=datetime(2026, 1, 15)),
        M.Opportunity(tenant_id="tnt_a", contact_id=c1.id, title="o2", value=2000, stage="qualified", created_at=datetime(2026, 1, 20)),
        M.Opportunity(tenant_id="tnt_a", contact_id=c1.id, title="o3", value=5000, stage="closed_won", created_at=datetime(2026, 1, 10), closed_at=datetime(2026, 1, 25)),
        M.Opportunity(tenant_id="tnt_a", contact_id=c2.id, title="o4", value=3000, stage="proposal", created_at=datetime(2026, 2, 5)),
        M.Opportunity(tenant_id="tnt_a", contact_id=c2.id, title="o5", value=4000, stage="closed_won", created_at=datetime(2026, 2, 1), closed_at=datetime(2026, 2, 10)),
        M.Opportunity(tenant_id="tnt_a", contact_id=c2.id, title="o6", value=1000, stage="closed_lost", created_at=datetime(2026, 2, 15)),
    ]
    s.add_all(opps); s.commit()

    # ── Funnel ──
    f = an.funnel("tnt_a", db=s, _t=None)
    print("--- funnel ---")
    ck(f.total_entered, 5, "total_entered=5 (toutes sauf closed_lost)")
    ck(f.total_won, 2, "total_won=2 (o3+o5)")
    ck(f.overall_conversion_pct, 40.0, "overall_conversion=40.0% (2/5)")
    fmap = {fs.stage: fs for fs in f.stages}
    ck(fmap["prospecting"].cumulative, 5, "prospecting cumulative=5 (toutes non-lost)")
    ck(fmap["qualified"].cumulative, 4, "qualified cumulative=4")
    ck(fmap["proposal"].cumulative, 3, "proposal cumulative=3")
    ck(fmap["negotiation"].cumulative, 2, "negotiation cumulative=2 (won only)")
    ck(fmap["closed_won"].cumulative, 2, "closed_won cumulative=2")
    ck(fmap["qualified"].conversion_from_prev_pct, 80.0, "qualified conversion=80.0% (4/5)")
    ck(fmap["proposal"].conversion_from_prev_pct, 75.0, "proposal conversion=75.0% (3/4)")
    ck(fmap["negotiation"].conversion_from_prev_pct, 66.7, "negotiation conversion=66.7% (2/3)")
    ck(fmap["prospecting"].drop_off, 0, "prospecting drop_off=0 (entrée)")
    ck(fmap["qualified"].drop_off, 1, "qualified drop_off=1 (5-4)")

    # ── Cohorts ──
    co = an.cohorts("tnt_a", db=s, _t=None)
    print("--- cohorts ---")
    ck(co.total_opps, 6, "total_opps=6")
    ck(co.total_won, 2, "total_won=2")
    ck(co.avg_win_rate_pct, 33.3, "avg_win_rate=33.3% (2/6)")
    ck(len(co.cohorts), 2, "2 cohortes (2026-01 + 2026-02)")
    cmap = {c.month: c for c in co.cohorts}
    ck("2026-01" in cmap, True, "cohorte 2026-01 présente")
    ck("2026-02" in cmap, True, "cohorte 2026-02 présente")
    jan = cmap["2026-01"]
    ck(jan.total_opps, 3, "2026-01 : 3 opps (o1+o2+o3)")
    ck(jan.closed_won_count, 1, "2026-01 : 1 won (o3)")
    ck(jan.win_rate_pct, 33.3, "2026-01 win_rate=33.3%")
    ck(jan.revenue_won, 5000, "2026-01 revenue_won=5000")
    ck(jan.avg_time_to_close_days, 15.0, "2026-01 avg_time_to_close=15.0j (o3: 10→25 jan)")
    ck(jan.by_stage.get("closed_won"), 1, "2026-01 by_stage closed_won=1")
    feb = cmap["2026-02"]
    ck(feb.total_opps, 3, "2026-02 : 3 opps (o4+o5+o6)")
    ck(feb.closed_won_count, 1, "2026-02 : 1 won (o5)")
    ck(feb.revenue_won, 4000, "2026-02 revenue_won=4000")
    ck(feb.avg_time_to_close_days, 9.0, "2026-02 avg_time_to_close=9.0j (o5: 1→10 feb)")

    # ── Attribution ──
    at = an.attribution("tnt_a", db=s, _t=None)
    print("--- attribution ---")
    ck(at.total_revenue_won, 9000, "total_revenue_won=9000 (5000+4000)")
    ck(at.total_won, 2, "total_won=2")
    ck(at.best_source, "linkedin", "best_source=linkedin (5000 > 4000)")
    asrc = {a.source: a for a in at.by_source}
    ck(asrc["linkedin"].opps_count, 3, "linkedin : 3 opps (o1+o2+o3)")
    ck(asrc["linkedin"].closed_won_count, 1, "linkedin : 1 won")
    ck(asrc["linkedin"].revenue_won, 5000, "linkedin revenue_won=5000")
    ck(asrc["linkedin"].win_rate_pct, 33.3, "linkedin win_rate=33.3%")
    ck(asrc["linkedin"].avg_deal_size, 5000.0, "linkedin avg_deal_size=5000 (5000/1)")
    ck(asrc["manual"].opps_count, 3, "manual : 3 opps (o4+o5+o6)")
    ck(asrc["manual"].revenue_won, 4000, "manual revenue_won=4000")
    ck(at.by_source[0].source, "linkedin", "attribution trié par revenue desc → linkedin first")

    # ── Auto closed_at : update_opportunity ──
    print("--- auto closed_at (update_opportunity) ---")
    o7 = M.Opportunity(tenant_id="tnt_a", contact_id=c1.id, title="o7", value=2000, stage="negotiation")
    s.add(o7); s.commit()
    ck(o7.closed_at, None, "o7 closed_at=None avant (stage=negotiation)")
    o7_done = crm_r.update_opportunity("tnt_a", o7.id, S.OpportunityUpdate(stage="closed_won"), db=s, _t=None, membership=mem)
    ck(o7_done.stage, "closed_won", "update → stage=closed_won")
    ck(o7_done.closed_at is not None, True, "auto-set closed_at sur stage→closed_won")
    o7_back = crm_r.update_opportunity("tnt_a", o7.id, S.OpportunityUpdate(stage="negotiation"), db=s, _t=None, membership=mem)
    ck(o7_back.closed_at, None, "retour arrière closed_won→negotiation → clear closed_at")

    # ── Auto closed_at : move_opportunity (Kanban) ──
    print("--- auto closed_at (move_opportunity Kanban) ---")
    o8 = M.Opportunity(tenant_id="tnt_a", contact_id=c2.id, title="o8", value=3000, stage="proposal")
    s.add(o8); s.commit()
    o8_done = crm_r.move_opportunity("tnt_a", o8.id, S.StageUpdate(stage="closed_won"), db=s, _t=None, membership=mem)
    ck(o8_done.stage, "closed_won", "move → stage=closed_won")
    ck(o8_done.closed_at is not None, True, "move_opportunity auto-set closed_at (Kanban drag→won)")
    o8_back = crm_r.move_opportunity("tnt_a", o8.id, S.StageUpdate(stage="proposal"), db=s, _t=None, membership=mem)
    ck(o8_back.closed_at, None, "move_opportunity retour arrière → clear closed_at")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
