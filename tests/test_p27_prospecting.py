#!/usr/bin/env python3
"""test_p27_prospecting.py — validation statique + runtime de P27 Prospection avancée.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : SequenceStep étendu (channel + channel_config + template_id nullable), Contact étendu
    (intent_score + enriched_at), SequenceStepVariant (A/B testing), IntentSignal (détection intention).
  - migration 0017 : batch alters (sequence_steps + contacts) + 2 nouvelles tables.
  - services/prospecting.py : create_multichannel_step, record_variant_metrics, add_intent_signal,
    compute_intent_score (fenêtre 90j, cap 100), trigger_enrichment (simulé v1), ab_test_results.
  - routers/prospecting.py : 9 endpoints + RBAC (content.read Viewer+, crm.manage Editor+).
  - schemas.py : MultichannelStepRequest, SequenceStepVariantRequest/Out, VariantMetricsRequest,
    ABTestResult, IntentSignalRequest/Out, IntentLeader, EnrichmentResult.
  - main.py : router prospecting inclus.
  - Runtime : multichannel step (email + linkedin + validation errors), A/B variants (create A+B +
    metrics + ab-results avec taux), intent signals (add + recompute score cap 100), enrichment (simulé).
"""
import os
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


print("=== test_p27_prospecting.py — P27 Prospection avancée ===\n")

# ─── Source : models.py (SequenceStep étendu + Contact étendu + 2 nouveaux) ───
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : SequenceStep multicanal + Contact intent + 2 nouveaux modèles ===")
ck("channel = Column(String, default=\"email\"" in models_src, True, "SequenceStep.channel (default email — backward compat P15)")
ck("channel_config = Column(JSONType" in models_src, True, "SequenceStep.channel_config JSON")
ck("template_id = Column(String, nullable=True)" in models_src, True, "SequenceStep.template_id nullable (steps non-email)")
ck("intent_score = Column(Integer, default=0" in models_src, True, "Contact.intent_score (P27)")
ck("enriched_at = Column(DateTime, nullable=True)" in models_src, True, "Contact.enriched_at (P27)")
ck("class SequenceStepVariant(Base):" in models_src, True, "SequenceStepVariant model (A/B testing)")
ck('__tablename__ = "sequence_step_variants"' in models_src, True, "table sequence_step_variants")
ck("variant = Column(String, nullable=False)" in models_src, True, "SequenceStepVariant.variant (A|B)")
ck("sent_count = Column(Integer" in models_src and "open_count = Column(Integer" in models_src, True, "variants métriques sent/open/click/reply")
ck("class IntentSignal(Base):" in models_src, True, "IntentSignal model (détection intention)")
ck('__tablename__ = "intent_signals"' in models_src, True, "table intent_signals")
ck("signal_type = Column(String, nullable=False" in models_src, True, "IntentSignal.signal_type (job_change|funding|...)")
ck("score = Column(Integer, default=10" in models_src, True, "IntentSignal.score 1-100")

# ─── Source : migration 0017 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0017_p27_prospecting.py").read_text(encoding="utf-8")
print("\n=== migration 0017_p27_prospecting ===")
ck('revision = "0017_p27_prospecting"' in mig, True, "revision 0017_p27_prospecting")
ck('down_revision = "0016_p26_content_studio"' in mig, True, "down_revision 0016 (chaîne)")
ck("batch_alter_table(\"sequence_steps\")" in mig, True, "batch alter sequence_steps (SQLite rebuild + PG alter)")
ck("batch_alter_table(\"contacts\")" in mig, True, "batch alter contacts")
ck('"sequence_step_variants"' in mig and '"intent_signals"' in mig, True, "2 nouvelles tables créées")
ck("server_default=\"email\"" in mig, True, "channel default 'email' (backward compat)")
ck("ondelete=\"CASCADE\"" in mig, True, "CASCADE sur variants (delete step) + signals (delete contact)")

# ─── Source : services/prospecting.py ────────────────────────────────────────
svc_src = (ROOT / "backend" / "app" / "services" / "prospecting.py").read_text(encoding="utf-8")
print("\n=== services/prospecting.py : 6 fonctions ===")
ck("def create_multichannel_step(" in svc_src, True, "create_multichannel_step")
ck("def record_variant_metrics(" in svc_src, True, "record_variant_metrics (sent|open|click|reply)")
ck("def compute_intent_score(" in svc_src, True, "compute_intent_score (fenêtre 90j, cap 100)")
ck("def add_intent_signal(" in svc_src, True, "add_intent_signal + recompute score")
ck("def trigger_enrichment(" in svc_src, True, "trigger_enrichment (simulé v1)")
ck("def ab_test_results(" in svc_src, True, "ab_test_results (taux par variante)")
ck("_INTENT_WINDOW_DAYS = 90" in svc_src, True, "fenêtre glissante 90j (anti-inflation)")
ck("min(100, sum" in svc_src, True, "intent_score cap 100")
ck("simulated" in svc_src and "True" in svc_src, True, "enrichment simulé v1 (anti-fake)")
ck("valid_channels = (\"email\", \"linkedin\", \"call\", \"sms\", \"manual\")" in svc_src, True, "5 canaux valides")

# ─── Source : routers/prospecting.py (9 endpoints + RBAC) ────────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "prospecting.py").read_text(encoding="utf-8")
print("\n=== routers/prospecting.py : 9 endpoints + RBAC + audit ===")
ck('prefix="/api/tenants/{tid}/prospecting"' in rtr_src, True, "router prefix /prospecting")
ck('tags=["prospecting"]' in rtr_src, True, "router tag prospecting")
ck('@router.post("/sequences/{sid}/steps"' in rtr_src, True, "POST /sequences/{sid}/steps (multichannel)")
ck('@router.get("/sequences/{sid}/steps/{step_id}/variants"' in rtr_src, True, "GET /variants (list)")
ck('@router.post("/sequences/{sid}/steps/{step_id}/variants"' in rtr_src, True, "POST /variants (create A/B)")
ck('@router.post("/sequences/{sid}/steps/{step_id}/variants/{vid}/metrics"' in rtr_src, True, "POST /variants/{vid}/metrics")
ck('@router.get("/sequences/{sid}/steps/{step_id}/ab-results"' in rtr_src, True, "GET /ab-results (taux par variante)")
ck('@router.get("/contacts/{cid}/intent-signals"' in rtr_src, True, "GET /contacts/{cid}/intent-signals")
ck('@router.post("/contacts/{cid}/intent-signals"' in rtr_src, True, "POST /contacts/{cid}/intent-signals")
ck('@router.get("/contacts/intent-leaders"' in rtr_src, True, "GET /contacts/intent-leaders (leaderboard)")
ck('@router.post("/contacts/{cid}/enrich"' in rtr_src, True, "POST /contacts/{cid}/enrich (simulé v1)")
ck('require_permission("content.read")' in rtr_src, True, "RBAC content.read (Viewer+ — lecture)")
ck('require_permission("crm.manage")' in rtr_src, True, "RBAC crm.manage (Editor+ — write)")
ck("prospecting.step.create" in rtr_src, True, "audit prospecting.step.create")
ck("prospecting.variant.create" in rtr_src, True, "audit prospecting.variant.create")
ck("prospecting.intent.add" in rtr_src, True, "audit prospecting.intent.add")
ck("prospecting.enrich" in rtr_src, True, "audit prospecting.enrich")
ck("from e" in rtr_src, True, "B904 fix : raise ... from e")

# ─── Source : schemas.py (P27 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P27 schemas ===")
ck("class MultichannelStepRequest" in sch_src, True, "MultichannelStepRequest")
ck("class SequenceStepVariantRequest" in sch_src, True, "SequenceStepVariantRequest")
ck("class SequenceStepVariantOut" in sch_src, True, "SequenceStepVariantOut")
ck("class VariantMetricsRequest" in sch_src, True, "VariantMetricsRequest")
ck("class ABTestResult" in sch_src, True, "ABTestResult (taux open/click/reply)")
ck("class IntentSignalRequest" in sch_src, True, "IntentSignalRequest")
ck("class IntentSignalOut" in sch_src, True, "IntentSignalOut")
ck("class IntentLeader" in sch_src, True, "IntentLeader (leaderboard)")
ck("class EnrichmentResult" in sch_src, True, "EnrichmentResult (simulé v1)")

# ─── Source : main.py (router prospecting inclus) ────────────────────────────
main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : router prospecting inclus ===")
ck("    prospecting," in main_src, True, "main.py import prospecting")
ck("prospecting.router" in main_src, True, "main.py include prospecting.router")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : multichannel + A/B + intent + enrich ===")
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
    from app.services import prospecting as pros

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p27", email="p27@test.com", name="P27", password_hash="h"))
    s.add(M.Tenant(id="tnt_p27", name="P27", plan="Starter", owner_id="usr_p27"))
    s.add(M.Member(id="mem_p27", tenant_id="tnt_p27", user_id="usr_p27", email="p27@test.com",
                   name="P27", role="Owner", status="active"))
    # EmailSequence + Contact pour les tests.
    s.add(M.EmailSequence(id="seq_p27", tenant_id="tnt_p27", name="Seq P27", status="draft"))
    s.add(M.Contact(id="ctc_p27", tenant_id="tnt_p27", email="prospect@test.com",
                   first_name="Jane", last_name="Doe", title="VP Sales"))
    s.commit()

    # ── create_multichannel_step : email (template_id requis) + linkedin (channel_config) ──
    print("--- create_multichannel_step : email + linkedin + validation errors ---")
    step_email = pros.create_multichannel_step(
        s, sequence_id="seq_p27", step_order=1, channel="email", delay_days=0,
        template_id="tpl_cold", channel_config=None, user_id="usr_p27")
    ck(step_email.channel, "email", "step email → channel=email")
    ck(step_email.template_id, "tpl_cold", "step email → template_id setté")
    step_li = pros.create_multichannel_step(
        s, sequence_id="seq_p27", step_order=2, channel="linkedin", delay_days=2,
        template_id=None, channel_config={"linkedin_action": "connection_request"}, user_id="usr_p27")
    ck(step_li.channel, "linkedin", "step linkedin → channel=linkedin")
    ck(step_li.template_id, None, "step linkedin → template_id=None")
    ck(step_li.channel_config.get("linkedin_action"), "connection_request", "channel_config.linkedin_action")
    # Validation errors.
    err = None
    try:
        pros.create_multichannel_step(s, "seq_p27", 3, "email", 0, None, None, "usr_p27")  # email sans template
    except ValueError as e:
        err = str(e)
    ck("template_id requis" in err, True, f"email sans template → ValueError ({err[:30]}...)")
    err2 = None
    try:
        pros.create_multichannel_step(s, "seq_p27", 3, "invalid_channel", 0, None, {}, "usr_p27")
    except ValueError as e:
        err2 = str(e)
    ck("channel invalide" in err2, True, f"channel invalide → ValueError ({err2[:30]}...)")
    err3 = None
    try:
        pros.create_multichannel_step(s, "seq_p27", 3, "linkedin", 0, None, None, "usr_p27")  # linkedin sans config
    except ValueError as e:
        err3 = str(e)
    ck("channel_config requis" in err3, True, f"linkedin sans config → ValueError ({err3[:30]}...)")

    # ── A/B variants : create A + B + metrics + ab-results ──
    print("--- A/B testing : variants A+B + metrics + ab-results ---")
    from app import models as M2
    var_a = M2.SequenceStepVariant(step_id=step_email.id, variant="A", label="Version longue",
                                   subject_override="Sujet A", body_override="Corps A")
    var_b = M2.SequenceStepVariant(step_id=step_email.id, variant="B", label="Version courte",
                                   subject_override="Sujet B", body_override="Corps B")
    s.add_all([var_a, var_b])
    s.commit()
    # Record metrics : A → sent=100, open=40, click=10, reply=5 ; B → sent=100, open=55, click=15, reply=8.
    for ev, cnt in [("sent", 100), ("open", 40), ("click", 10), ("reply", 5)]:
        pros.record_variant_metrics(s, var_a, ev, cnt)
    for ev, cnt in [("sent", 100), ("open", 55), ("click", 15), ("reply", 8)]:
        pros.record_variant_metrics(s, var_b, ev, cnt)
    ck(var_a.sent_count, 100, "var A sent_count=100")
    ck(var_a.open_count, 40, "var A open_count=40")
    ck(var_b.reply_count, 8, "var B reply_count=8")
    results = pros.ab_test_results(s, step_email.id)
    ck(len(results), 2, f"ab-results : {len(results)} variants (2)")
    res_a = next(r for r in results if r["variant"] == "A")
    res_b = next(r for r in results if r["variant"] == "B")
    ck(res_a["open_rate"], 40.0, f"var A open_rate={res_a['open_rate']} (40.0)")
    ck(res_b["open_rate"], 55.0, f"var B open_rate={res_b['open_rate']} (55.0 — gagnante)")
    ck(res_b["reply_rate"], 8.0, f"var B reply_rate={res_b['reply_rate']} (8.0)")
    ck(res_b["reply_rate"] > res_a["reply_rate"], True, "B gagne sur reply_rate (8% vs 5%)")

    # ── Intent signals : add + recompute score + cap 100 ──
    print("--- Intent signals : add + compute_intent_score (cap 100) ---")
    sig1 = pros.add_intent_signal(s, "tnt_p27", "ctc_p27", "job_change", "Jane promoted to VP Sales",
                                  score=30, source="webhook", user_id="usr_p27")
    ck(sig1.signal_type, "job_change", "signal type propagé")
    ck(sig1.source, "webhook", "source=webhook (untrusted data)")
    contact = s.query(M.Contact).filter_by(id="ctc_p27").first()
    ck(contact.intent_score, 30, f"intent_score après 1 signal (30) = {contact.intent_score}")
    sig2 = pros.add_intent_signal(s, "tnt_p27", "ctc_p27", "funding", "Series B $50M raised",
                                  score=40, source="enrichment", user_id="usr_p27")
    ck(contact.intent_score, 70, f"intent_score après 2 signaux (30+40=70) = {contact.intent_score}")
    sig3 = pros.add_intent_signal(s, "tnt_p27", "ctc_p27", "hire_spike", "Hiring 5 SDRs",
                                  score=50, source="manual", user_id="usr_p27")
    ck(contact.intent_score, 100, f"intent_score cap 100 (30+40+50=120 → 100) = {contact.intent_score}")
    # List signals.
    sigs = s.query(M.IntentSignal).filter_by(contact_id="ctc_p27").all()
    ck(len(sigs), 3, f"{len(sigs)} signaux en DB (3)")

    # ── Enrichment (simulated v1) ──
    print("--- Enrichment (simulé v1) ---")
    result = pros.trigger_enrichment(s, contact)
    ck(result["simulated"], True, "enrichment simulated=True (anti-fake)")
    ck(result["contact_id"], "ctc_p27", "enrichment contact_id propagé")
    ck(contact.enriched_at is not None, True, "contact.enriched_at setté")
    ck("simulé" in result["data"]["note"], True, "note marque enrichment simulé v1")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
