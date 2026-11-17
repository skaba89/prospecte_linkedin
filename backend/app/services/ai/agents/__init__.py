"""P25 — Agents IA spécialisés + AI Copilot.

10 agents connectés aux données CRM (via models) + à l'AI Gateway P24-B (gateway.generate avec
fallback SimulatedProvider). Chaque agent : fetch_context (CRM) → build_prompt (template {{variables}})
→ gateway.generate → persist AgentRun → retourne AgentResult.

AI Copilot : interface conversationnelle qui route le message utilisateur vers le bon agent
(keyword matching) + retourne le résultat dans un format chat.

Submodules :
  - base.py : BaseAgent ABC + AgentResult dataclass.
  - agents.py : 10 agents concrets + AGENTS registry + COPILOT_ROUTING (keyword → agent).
"""
from .agents import AGENT_LIST, AGENTS, COPILOT_ROUTING, get_agent, route_copilot

__all__ = ["AGENTS", "AGENT_LIST", "COPILOT_ROUTING", "get_agent", "route_copilot"]
