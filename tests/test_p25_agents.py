#!/usr/bin/env python3
"""test_p25_agents.py — validation statique + runtime de P25 Agents IA spécialisés + AI Copilot.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : AgentRun (historisation : tenant, agent, context JSON, result, status, simulated,
    require_human_validation, generation_id FK).
  - migration 0015 : table agent_runs (JSONB.with_variant pour context).
  - services/ai/agents/base.py : BaseAgent ABC + AgentResult + helpers (_contact_summary,
    _engagement_summary, _interactions_summary) + _execute (fetch → prompt → gateway → persist).
  - services/ai/agents/agents.py : 10 agents (qualification, scoring, research, personalization,
    linkedin, competitive, meeting_prep, performance, recommendation, synthesis) + AGENTS registry +
    AGENT_LIST + COPILOT_ROUTING (keyword → agent) + route_copilot.
  - routers/agents.py : 4 endpoints (GET /agents, POST /agents/{agent}/run, GET /agents/runs,
    POST /copilot/chat) + RBAC ai.use + audit ai.agent.run + ai.copilot.chat.
  - schemas.py : AgentInfo, AgentRunRequest/Response/Out, CopilotChatRequest/Response.
  - main.py : router agents inclus. deps.py : ai.use (hérité P24-B).
  - Runtime : route_copilot (keyword matching), AGENTS registry (10), agent run (simulated since no
    provider configured → simulated=True), AgentRun persisté, Copilot chat flow.
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


print("=== test_p25_agents.py — P25 Agents IA spécialisés + AI Copilot ===\n")

# ─── Source : models.py (AgentRun) ────────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : AgentRun ===")
ck("class AgentRun(Base):" in models_src, True, "AgentRun model")
ck('__tablename__ = "agent_runs"' in models_src, True, "table agent_runs")
ck("agent = Column(String, nullable=False" in models_src, True, "agent name column")
ck("context = Column(JSONType, nullable=True)" in models_src, True, "context JSON (inputs)")
ck("result_text = Column(Text" in models_src, True, "result_text column")
ck("require_human_validation = Column(Boolean" in models_src, True, "require_human_validation flag")
ck("generation_id = Column(String, ForeignKey" in models_src, True, "generation_id FK (audit trail P24-B→P25)")
ck("simulated = Column(Boolean" in models_src, True, "simulated flag propagé depuis gateway")

# ─── Source : migration 0015 ──────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0015_p25_ai_agents.py").read_text(encoding="utf-8")
print("\n=== migration 0015_p25_ai_agents ===")
ck('revision = "0015_p25_ai_agents"' in mig, True, "revision 0015_p25_ai_agents")
ck('down_revision = "0014_p24b_ai_gateway"' in mig, True, "down_revision 0014 (chaîne)")
ck('op.create_table' in mig and '"agent_runs"' in mig, True, "CREATE TABLE agent_runs")
ck("JSONB().with_variant(sa.Text()" in mig, True, "context JSONB PG / Text sqlite (pattern 0001/0004/0014)")

# ─── Source : services/ai/agents/base.py ─────────────────────────────────────
base_src = (ROOT / "backend" / "app" / "services" / "ai" / "agents" / "base.py").read_text(encoding="utf-8")
print("\n=== services/ai/agents/base.py : BaseAgent + AgentResult + helpers ===")
ck("class AgentResult" in base_src, True, "AgentResult dataclass")
ck("class BaseAgent(ABC):" in base_src, True, "BaseAgent ABC")
ck("def build_prompt(self" in base_src, True, "build_prompt (template {{variables}})")
ck("def fetch_context(self" in base_src, True, "fetch_context (CRM data)")
ck("def _execute(self" in base_src, True, "_execute (fetch → prompt → gateway → persist)")
ck("def _contact_summary(" in base_src, True, "helper _contact_summary")
ck("def _engagement_summary(" in base_src, True, "helper _engagement_summary")
ck("def _interactions_summary(" in base_src, True, "helper _interactions_summary")
ck("from .... import models" in base_src, True, "import models (relatif 4 dots — app)")
ck("from .. import gateway" in base_src, True, "import gateway (relatif 2 dots — app.services.ai)")
ck("models.AgentRun(" in base_src, True, "_execute persiste AgentRun")
ck("gateway.generate(" in base_src, True, "_execute appelle gateway.generate (P24-B)")

# ─── Source : services/ai/agents/agents.py (10 agents) ───────────────────────
ag_src = (ROOT / "backend" / "app" / "services" / "ai" / "agents" / "agents.py").read_text(encoding="utf-8")
print("\n=== services/ai/agents/agents.py : 10 agents + registry + Copilot routing ===")
ck("class QualificationAgent" in ag_src, True, "1. QualificationAgent")
ck("class ScoringAgent" in ag_src, True, "2. ScoringAgent")
ck("class ResearchAgent" in ag_src, True, "3. ResearchAgent")
ck("class PersonalizationAgent" in ag_src, True, "4. PersonalizationAgent")
ck("class LinkedInAgent" in ag_src, True, "5. LinkedInAgent")
ck("class CompetitiveAgent" in ag_src, True, "6. CompetitiveAgent")
ck("class MeetingPrepAgent" in ag_src, True, "7. MeetingPrepAgent")
ck("class PerformanceAgent" in ag_src, True, "8. PerformanceAgent")
ck("class RecommendationAgent" in ag_src, True, "9. RecommendationAgent")
ck("class SynthesisAgent" in ag_src, True, "10. SynthesisAgent")
ck("AGENTS: dict[str, BaseAgent]" in ag_src, True, "AGENTS registry")
ck("AGENT_LIST = [" in ag_src, True, "AGENT_LIST (pour affichage Copilot)")
ck("def get_agent(name: str)" in ag_src, True, "get_agent() factory")
ck("COPILOT_ROUTING" in ag_src, True, "COPILOT_ROUTING (keyword → agent)")
ck("def route_copilot(message: str)" in ag_src, True, "route_copilot() dispatcher")
ck("BANT" in ag_src, True, "qualification agent : framework BANT")
ck("prompt_template" in ag_src, True, "agents : prompt_template {{variables}}")
ck("def fetch_context" in ag_src, True, "agents : fetch_context (CRM data)")
ck("db.query(models.Contact)" in ag_src, True, "agents connectés au CRM (query Contact)")
ck("db.query(models.Opportunity)" in ag_src, True, "agents connectés au CRM (query Opportunity)")
ck("db.query(models.Notification)" in base_src, True, "agents connectés au CRM (query Notification via base.py helpers)")

# ─── Source : routers/agents.py (4 endpoints + RBAC) ─────────────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "agents.py").read_text(encoding="utf-8")
print("\n=== routers/agents.py : 4 endpoints + RBAC + audit ===")
ck('prefix="/api/tenants/{tid}/ai"' in rtr_src, True, "router prefix /api/tenants/{tid}/ai")
ck('tags=["ai-agents"]' in rtr_src, True, "router tag ai-agents")
ck('@router.get("/agents"' in rtr_src, True, "GET /agents (list)")
ck('@router.post("/agents/{agent}/run"' in rtr_src, True, "POST /agents/{agent}/run")
ck('@router.get("/agents/runs"' in rtr_src, True, "GET /agents/runs (history)")
ck('@router.post("/copilot/chat"' in rtr_src, True, "POST /copilot/chat (Copilot)")
ck('require_permission("ai.use")' in rtr_src, True, "RBAC ai.use (member)")
ck("ai.agent.run" in rtr_src, True, "audit ai.agent.run")
ck("ai.copilot.chat" in rtr_src, True, "audit ai.copilot.chat")
ck("get_agent(agent)" in rtr_src, True, "run_agent : get_agent by name")
ck("route_copilot(body.message)" in rtr_src, True, "copilot_chat : route_copilot dispatch")
ck("require_human_validation" in rtr_src, True, "copilot : require_human_validation propagé")
ck("suggestions" in rtr_src, True, "copilot : suggestions contextuelles")

# ─── Source : schemas.py (P25 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P25 schemas ===")
ck("class AgentInfo" in sch_src, True, "AgentInfo")
ck("class AgentRunRequest" in sch_src, True, "AgentRunRequest")
ck("class AgentRunResponse" in sch_src, True, "AgentRunResponse (simulated + require_human_validation)")
ck("class AgentRunOut" in sch_src, True, "AgentRunOut (history)")
ck("class CopilotChatRequest" in sch_src, True, "CopilotChatRequest")
ck("class CopilotChatResponse" in sch_src, True, "CopilotChatResponse (routed_agent + suggestions)")
ck("routed_agent: str | None" in sch_src, True, "CopilotChatResponse.routed_agent")

# ─── Source : main.py (router agents inclus) ─────────────────────────────────
main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : router agents inclus ===")
ck("    agents," in main_src, True, "main.py import agents")
ck("agents.router" in main_src, True, "main.py include agents.router")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : route_copilot + agents + agent run + Copilot ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))

    # ── route_copilot : keyword matching ──
    from app.services.ai.agents import AGENTS, AGENT_LIST, route_copilot
    print("--- route_copilot : keyword matching ---")
    ck(len(AGENTS), 10, f"AGENTS registry : {len(AGENTS)} agents (10 attendus)")
    ck(len(AGENT_LIST), 10, f"AGENT_LIST : {len(AGENT_LIST)} (pour affichage)")
    ck(route_copilot("qualifie ce prospect pour moi"), "qualification", "route : 'qualifie' → qualification")
    ck(route_copilot("explique pourquoi le score est bas"), "scoring", "route : 'score' → scoring")
    ck(route_copilot("génère un e-mail personnalisé"), "personalization", "route : 'e-mail' → personalization")
    ck(route_copilot("rédige un post linkedin sur le sales"), "linkedin", "route : 'linkedin' → linkedin")
    ck(route_copilot("analyse mon pipeline commercial"), "performance", "route : 'pipeline' → performance")
    ck(route_copilot("fais une veille concurrentielle"), "competitive", "route : 'veille' → competitive")
    ck(route_copilot("prépare mon rendez-vous de demain"), "meeting_prep", "route : 'rendez-vous' → meeting_prep")
    ck(route_copilot("recommande la prochaine action"), "recommendation", "route : 'recommande' → recommendation")
    ck(route_copilot("synthétise les interactions"), "synthesis", "route : 'synthétise' → synthesis")
    ck(route_copilot("bonjour comment ça va"), None, "route : pas de match → None (no_route)")

    # ── agent run (simulated since no provider configured) ──
    from app import database as db_mod
    from app import models as M
    from app.services.ai.agents import get_agent
    print("--- agent run : qualification (simulated, no provider) ---")
    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p25", email="p25@test.com", name="P25", password_hash="h"))
    s.add(M.Tenant(id="tnt_p25", name="P25", plan="Starter", owner_id="usr_p25"))
    s.add(M.Member(id="mem_p25", tenant_id="tnt_p25", user_id="usr_p25", email="p25@test.com", name="P25", role="Owner", status="active"))
    c = M.Contact(tenant_id="tnt_p25", first_name="Alice", last_name="Martin", email="alice@acme.com",
                  title="VP Sales", status="new", source="manual", score=65)
    s.add(c); s.commit()

    qualif = get_agent("qualification")
    ck(qualif is not None, True, "get_agent('qualification') retourne l'instance")
    res = qualif.run(s, tenant_id="tnt_p25", params={"contact_id": c.id}, user_id="usr_p25")
    ck(res.simulated, True, "qualification.run (no provider) → simulated=True (anti-fake)")
    ck(res.agent, "qualification", "result.agent = qualification")
    ck(res.agent_run_id is not None, True, f"AgentRun persisté (id={res.agent_run_id})")
    ck(len(res.text) > 0, True, f"result.text généré ({len(res.text)} chars)")

    # ── agent run : performance (uses tenant metrics, no contact_id needed) ──
    print("--- agent run : performance (tenant metrics) ---")
    perf = get_agent("performance")
    res_perf = perf.run(s, tenant_id="tnt_p25", params={}, user_id="usr_p25")
    ck(res_perf.simulated, True, "performance.run → simulated=True")
    ck(res_perf.agent, "performance", "result.agent = performance")
    ck(len(res_perf.text) > 0, True, "performance : text généré")

    # ── agent run : linkedin (topic param) ──
    print("--- agent run : linkedin (topic) ---")
    li = get_agent("linkedin")
    res_li = li.run(s, tenant_id="tnt_p25", params={"topic": "Sales B2B 2026", "style": "expert"}, user_id="usr_p25")
    ck(res_li.simulated, True, "linkedin.run → simulated=True")
    ck(res_li.agent, "linkedin", "result.agent = linkedin")

    # ── AgentRun historisation ──
    print("--- AgentRun historisation ---")
    runs = s.query(M.AgentRun).filter_by(tenant_id="tnt_p25").all()
    ck(len(runs) >= 3, True, f"{len(runs)} AgentRun persistés (>=3 : qualif + perf + linkedin)")
    agents_run = {r.agent for r in runs}
    ck("qualification" in agents_run, True, "historisation : qualification run persisté")
    ck("performance" in agents_run, True, "historisation : performance run persisté")
    ck("linkedin" in agents_run, True, "historisation : linkedin run persisté")
    sim_runs = [r for r in runs if r.simulated]
    ck(len(sim_runs) >= 3, True, "historisation : tous simulated=True (no provider configured)")

    # ── Copilot chat flow (via service, simulant le router) ──
    print("--- Copilot chat flow ---")
    # Simule le dispatch du router : route_copilot + agent.run.
    msg = "Qualifie ce prospect pour moi"
    routed = route_copilot(msg)
    ck(routed, "qualification", f"copilot dispatch '{msg}' → {routed}")
    copilot_result = get_agent(routed).run(s, tenant_id="tnt_p25", params={"contact_id": c.id}, user_id="usr_p25")
    ck(copilot_result.simulated, True, "copilot result : simulated=True (propagé depuis agent)")
    ck(copilot_result.agent, "qualification", "copilot result : agent=qualification (routé)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
