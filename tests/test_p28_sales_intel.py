#!/usr/bin/env python3
"""test_p28_sales_intel.py — validation statique + runtime de P28 Warmup inbox + Sales Intelligence.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : InboxWarmup (config warmup + rampe), WarmupMessage (tracking réputation).
  - migration 0018 : 2 nouvelles tables (inbox_warmups, warmup_messages) — pas de modif tables existantes.
  - services/sales_intelligence.py : create_warmup, advance_warmup_day (rampe linéaire), record_warmup_message,
    warmup_stats (open_rate/reply_rate), compute_funnel, compute_cohorts, compute_forecast (pipeline pondéré).
  - routers/sales_intelligence.py : 7 endpoints + RBAC (content.read Viewer+, crm.manage Editor+).
  - schemas.py : InboxWarmupRequest/Out, WarmupStatsOut, FunnelStage/Out, CohortOut, ForecastStage/Out.
  - main.py : router sales_intelligence inclus.
  - Runtime : warmup ramp (advance 3 jours → daily_volume croît), record messages + stats (open_rate),
    funnel (contacts par status), cohorts (enrollments par semaine ISO), forecast (value * prob/100).
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


print("=== test_p28_sales_intel.py — P28 Warmup inbox + Sales Intelligence ===\n")

# ─── Source : models.py (2 nouveaux modèles) ─────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : InboxWarmup + WarmupMessage ===")
ck("class InboxWarmup(Base):" in models_src, True, "InboxWarmup model")
ck('__tablename__ = "inbox_warmups"' in models_src, True, "table inbox_warmups")
ck("sender_email = Column(String, nullable=False)" in models_src, True, "InboxWarmup.sender_email")
ck("target_volume = Column(Integer, default=100" in models_src, True, "InboxWarmup.target_volume (default 100)")
ck("ramp_days = Column(Integer, default=30" in models_src, True, "InboxWarmup.ramp_days (default 30)")
ck("current_day = Column(Integer, default=0" in models_src, True, "InboxWarmup.current_day")
ck("daily_volume = Column(Integer, default=0" in models_src, True, "InboxWarmup.daily_volume")
ck('ramp_status = Column(String, default="pending"' in models_src, True, "ramp_status (pending|ramping|active|paused|completed)")
ck("class WarmupMessage(Base):" in models_src, True, "WarmupMessage model")
ck('__tablename__ = "warmup_messages"' in models_src, True, "table warmup_messages")
ck("opened = Column(Boolean, default=False" in models_src, True, "WarmupMessage.opened")
ck("replied = Column(Boolean, default=False" in models_src, True, "WarmupMessage.replied")

# ─── Source : migration 0018 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0018_p28_warmup_sales_intel.py").read_text(encoding="utf-8")
print("\n=== migration 0018_p28_warmup_sales_intel ===")
ck('revision = "0018_p28_warmup_sales_intel"' in mig, True, "revision 0018_p28_warmup_sales_intel")
ck('down_revision = "0017_p27_prospecting"' in mig, True, "down_revision 0017 (chaîne)")
ck('"inbox_warmups"' in mig and '"warmup_messages"' in mig, True, "2 nouvelles tables créées")
ck("ondelete=\"CASCADE\"" in mig, True, "CASCADE sur warmups (delete tenant) + messages (delete warmup)")
ck("ix_inbox_warmups_tenant_id" in mig, True, "index tenant_id sur inbox_warmups")
ck("ix_warmup_messages_warmup_id" in mig, True, "index warmup_id sur warmup_messages")

# ─── Source : services/sales_intelligence.py (7 fonctions) ───────────────────
svc_src = (ROOT / "backend" / "app" / "services" / "sales_intelligence.py").read_text(encoding="utf-8")
print("\n=== services/sales_intelligence.py : 7 fonctions ===")
ck("def create_warmup(" in svc_src, True, "create_warmup")
ck("def advance_warmup_day(" in svc_src, True, "advance_warmup_day (rampe linéaire)")
ck("def record_warmup_message(" in svc_src, True, "record_warmup_message")
ck("def warmup_stats(" in svc_src, True, "warmup_stats (open_rate/reply_rate)")
ck("def compute_funnel(" in svc_src, True, "compute_funnel (contacts par status)")
ck("def compute_cohorts(" in svc_src, True, "compute_cohorts (semaine ISO)")
ck("def compute_forecast(" in svc_src, True, "compute_forecast (pipeline pondéré)")
ck("max(1, round(warmup.target_volume * warmup.current_day / warmup.ramp_days))" in svc_src, True, "rampe linéaire (target * day / ramp_days)")
ck("max(10, min(1000, target_volume))" in svc_src, True, "target_volume clamp 10-1000")
ck("max(7, min(90, ramp_days))" in svc_src, True, "ramp_days clamp 7-90")
ck("opens / sent * 100" in svc_src, True, "open_rate = opens/sent*100")

# ─── Source : routers/sales_intelligence.py (7 endpoints + RBAC) ─────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "sales_intelligence.py").read_text(encoding="utf-8")
print("\n=== routers/sales_intelligence.py : 7 endpoints + RBAC + audit ===")
ck('prefix="/api/tenants/{tid}/sales-intel"' in rtr_src, True, "router prefix /sales-intel (distinct du router analytics.py P10)")
ck('tags=["sales-intelligence"]' in rtr_src, True, "router tag sales-intelligence")
ck('@router.post("/warmup"' in rtr_src, True, "POST /warmup (create)")
ck('@router.get("/warmup"' in rtr_src, True, "GET /warmup (list)")
ck('@router.post("/warmup/{wid}/advance"' in rtr_src, True, "POST /warmup/{wid}/advance")
ck('@router.get("/warmup/{wid}/stats"' in rtr_src, True, "GET /warmup/{wid}/stats")
ck('@router.get("/funnel"' in rtr_src, True, "GET /funnel")
ck('@router.get("/cohorts"' in rtr_src, True, "GET /cohorts")
ck('@router.get("/forecast"' in rtr_src, True, "GET /forecast")
ck('require_permission("content.read")' in rtr_src, True, "RBAC content.read (Viewer+ — lecture)")
ck('require_permission("crm.manage")' in rtr_src, True, "RBAC crm.manage (Editor+ — write)")
ck("analytics.warmup.create" in rtr_src, True, "audit analytics.warmup.create")
ck("analytics.warmup.advance" in rtr_src, True, "audit analytics.warmup.advance")

# ─── Source : schemas.py (P28 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P28 schemas ===")
ck("class InboxWarmupRequest" in sch_src, True, "InboxWarmupRequest")
ck("class InboxWarmupOut" in sch_src, True, "InboxWarmupOut")
ck("class WarmupStatsOut" in sch_src, True, "WarmupStatsOut")
ck("class ContactFunnelStage" in sch_src, True, "ContactFunnelStage (renamed — évite collision avec FunnelStage P24)")
ck("class FunnelOut" in sch_src, True, "FunnelOut (contacts-funnel P28)")
ck("class CohortOut" in sch_src, True, "CohortOut")
ck("class ForecastStage" in sch_src, True, "ForecastStage")
ck("class ForecastOut" in sch_src, True, "ForecastOut")

# ─── Source : main.py (router sales_intelligence inclus) ─────────────────────
main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : router sales_intelligence inclus ===")
ck("    sales_intelligence," in main_src, True, "main.py import sales_intelligence")
ck("sales_intelligence.router" in main_src, True, "main.py include sales_intelligence.router")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : warmup ramp + funnel + cohorts + forecast ===")
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
    from app.services import sales_intelligence as si

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p28", email="p28@test.com", name="P28", password_hash="h"))
    s.add(M.Tenant(id="tnt_p28", name="P28", plan="Starter", owner_id="usr_p28"))
    s.add(M.Member(id="mem_p28", tenant_id="tnt_p28", user_id="usr_p28", email="p28@test.com",
                   name="P28", role="Owner", status="active"))
    # EmailSequence pour les cohorts.
    s.add(M.EmailSequence(id="seq_p28", tenant_id="tnt_p28", name="Seq P28", status="draft"))
    s.commit()

    # ── Warmup : create + advance 3 jours + record messages + stats ──
    print("--- Warmup : create + advance 3 jours (rampe linéaire) + stats ---")
    warmup = si.create_warmup(s, "tnt_p28", "sender@p28.com", target_volume=100, ramp_days=10, user_id="usr_p28")
    ck(warmup.ramp_status, "pending", "warmup créé → status=pending")
    ck(warmup.current_day, 0, "warmup créé → current_day=0")
    ck(warmup.daily_volume, 0, "warmup créé → daily_volume=0")
    # Advance day 1 → daily_volume = round(100*1/10) = 10, status=ramping.
    warmup = si.advance_warmup_day(s, warmup)
    ck(warmup.current_day, 1, "advance day 1 → current_day=1")
    ck(warmup.daily_volume, 10, f"advance day 1 → daily_volume={warmup.daily_volume} (10)")
    ck(warmup.ramp_status, "ramping", "advance day 1 → status=ramping")
    # Advance day 5 → daily_volume = round(100*5/10) = 50.
    for _ in range(4):
        warmup = si.advance_warmup_day(s, warmup)
    ck(warmup.current_day, 5, f"advance to day 5 → current_day={warmup.current_day}")
    ck(warmup.daily_volume, 50, f"advance day 5 → daily_volume={warmup.daily_volume} (50)")
    # Record 10 messages : 6 opened, 2 replied.
    for i in range(10):
        si.record_warmup_message(s, warmup.id, opened=(i < 6), replied=(i < 2))
    stats = si.warmup_stats(s, warmup)
    ck(stats["sent_count"], 10, f"stats sent_count={stats['sent_count']} (10)")
    ck(stats["open_count"], 6, f"stats open_count={stats['open_count']} (6)")
    ck(stats["reply_count"], 2, f"stats reply_count={stats['reply_count']} (2)")
    ck(stats["open_rate"], 60.0, f"stats open_rate={stats['open_rate']} (60.0)")
    ck(stats["reply_rate"], 20.0, f"stats reply_rate={stats['reply_rate']} (20.0)")
    ck(stats["progress_pct"], 50.0, f"stats progress_pct={stats['progress_pct']} (50.0 — day 5/10)")

    # ── Advance to completion (day 10) → status=active, daily_volume=target ──
    print("--- Warmup : advance to completion (day 10) → status=active ---")
    for _ in range(5):
        warmup = si.advance_warmup_day(s, warmup)
    ck(warmup.current_day, 10, f"advance to day 10 → current_day={warmup.current_day}")
    ck(warmup.ramp_status, "active", f"advance day 10 → status={warmup.ramp_status} (active)")
    ck(warmup.daily_volume, 100, f"advance day 10 → daily_volume={warmup.daily_volume} (100 = target)")

    # ── Funnel : contacts par status ──
    print("--- Funnel : contacts par status + conversion_rate ---")
    statuses = ["new", "new", "qualified", "contacted", "nurture", "won", "won", "lost"]
    for i, st in enumerate(statuses):
        s.add(M.Contact(id=f"ctc_p28_{i}", tenant_id="tnt_p28", email=f"c{i}@p28.com",
                       first_name="C", last_name=str(i), status=st))
    s.commit()
    funnel = si.compute_funnel(s, "tnt_p28")
    ck(funnel["total"], 8, f"funnel total={funnel['total']} (8 contacts)")
    new_stage = next(st for st in funnel["stages"] if st["status"] == "new")
    ck(new_stage["count"], 2, f"funnel new={new_stage['count']} (2)")
    won_stage = next(st for st in funnel["stages"] if st["status"] == "won")
    ck(won_stage["count"], 2, f"funnel won={won_stage['count']} (2)")
    ck(funnel["lost"], 1, f"funnel lost={funnel['lost']} (1)")
    ck(funnel["conversion_rate"], 25.0, f"funnel conversion_rate={funnel['conversion_rate']} (2/8=25.0)")

    # ── Cohorts : enrollments par semaine ISO ──
    print("--- Cohorts : enrollments par semaine ISO + completion_rate ---")
    # 2 cohortes : cette semaine (3 enrollments : 1 completed, 1 active, 1 paused) + semaine passée (2 : 1 completed, 1 failed).
    now = datetime.utcnow()
    last_week = now - timedelta(days=8)
    for i, (day, st) in enumerate([(now, "completed"), (now, "active"), (now, "paused"),
                                    (last_week, "completed"), (last_week, "failed")]):
        enr = M.SequenceEnrollment(
            id=f"enr_p28_{i}", sequence_id="seq_p28", contact_id=f"ctc_p28_{i}",
            current_step_order=1, status=st, enrolled_at=day, next_step_at=day)
        s.add(enr)
    s.commit()
    cohorts = si.compute_cohorts(s, "tnt_p28", weeks=4)
    ck(len(cohorts) >= 1, True, f"{len(cohorts)} cohortes (>=1)")
    # Cohorte la plus récente (cette semaine) → 3 enrollments, 1 completed → completion_rate=33.3.
    recent = cohorts[0]
    ck(recent["total"], 3, f"cohorte récente total={recent['total']} (3)")
    ck(recent["completed"], 1, f"cohorte récente completed={recent['completed']} (1)")
    ck(recent["completion_rate"], 33.3, f"cohorte récente completion_rate={recent['completion_rate']} (33.3)")

    # ── Forecast : pipeline pondéré (value * prob/100) ──
    print("--- Forecast : pipeline pondéré (value * prob/100) ---")
    # 3 opportunities ouvertes : (value=10000, prob=50 → weighted=5000), (value=20000, prob=25 → 5000),
    # (value=5000, prob=80 → 4000). raw_total=35000, weighted_total=14000.
    for i, (val, prob, stage) in enumerate([(10000, 50, "qualified"), (20000, 25, "proposal"), (5000, 80, "negotiation")]):
        s.add(M.Opportunity(id=f"opp_p28_{i}", tenant_id="tnt_p28", contact_id=f"ctc_p28_{i}",
                           title=f"Opp {i}", value=val, probability=prob, stage=stage))
    s.commit()
    forecast = si.compute_forecast(s, "tnt_p28")
    ck(forecast["total_opportunities"], 3, f"forecast total_opportunities={forecast['total_opportunities']} (3)")
    ck(forecast["raw_pipeline"], 35000, f"forecast raw_pipeline={forecast['raw_pipeline']} (35000)")
    ck(forecast["weighted_forecast"], 14000, f"forecast weighted_forecast={forecast['weighted_forecast']} (14000 — 5000+5000+4000)")
    ck(len(forecast["stages"]), 3, f"forecast stages={len(forecast['stages'])} (3 : qualified, proposal, negotiation)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
