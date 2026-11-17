"""P25 — Agents IA spécialisés + AI Copilot router.

Endpoints (préfixe /api/tenants/{tid}/ai) :
  GET    /agents              — lister les 10 agents (name + description) (member ai.use).
  POST   /agents/{agent}/run  — exécuter un agent avec params (member ai.use). Retourne AgentRunResponse
                                (simulated + require_human_validation — l'action externe est bloquée
                                jusqu'à validation humaine si require_human_validation=True).
  GET    /agents/runs         — historique des runs d'agents (paginé) (member ai.use).
  POST   /copilot/chat        — interface conversationnelle (member ai.use). Route le message vers
                                le bon agent (keyword matching) + retourne le résultat + suggestions.

RBAC : ai.use (member+) sur tous les endpoints (défini P24-B). Audit log sur run agent + copilot chat.

Sécurité (P25) :
  - require_human_validation propagé depuis l'agent (injection OU action externe) — l'appelant
    (frontend / intégration) DOIT bloquer l'action externe (envoi e-mail, publication LinkedIn) tant
    que require_human_validation=True + demander validation humaine explicite.
  - simulated=True → provider simulé (jamais présenté comme réel — anti-fake P24-B).
  - Les agents ne déclenchent JAMAIS d'action externe — ils génèrent du texte ; l'action est faite
    par le router/intégration qui DOIT vérifier require_human_validation.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services.ai.agents import AGENT_LIST, get_agent, route_copilot

router = APIRouter(prefix="/api/tenants/{tid}/ai", tags=["ai-agents"])


# ─── Liste des agents ────────────────────────────────────────────────────────


@router.get("/agents", response_model=list[schemas.AgentInfo])
def list_agents(
    tid: str,
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("ai.use")),
):
    """Liste les 10 agents IA spécialisés (name + description). Pour l'affichage dans le Copilot."""
    return AGENT_LIST


# ─── Exécution d'un agent ────────────────────────────────────────────────────


@router.post("/agents/{agent}/run", response_model=schemas.AgentRunResponse)
def run_agent(
    tid: str,
    agent: str,
    body: schemas.AgentRunRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("ai.use")),
):
    """Exécute un agent spécialisé (P25). params = inputs spécifiques (contact_id, opportunity_id,
    icp_description, topic, competitor...). Retourne AgentRunResponse : simulated (anti-fake) +
    require_human_validation (l'appelant DOIT bloquer l'action externe si True). Historisé dans
    AgentRun (audit trail). Audit ai.agent.run."""
    agent_inst = get_agent(agent)
    if not agent_inst:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Agent inconnu : {agent} (disponibles : {[a['name'] for a in AGENT_LIST]})",
        )
    result = agent_inst.run(db, tenant_id=tid, params=body.params, user_id=membership.user_id)
    log_audit(
        db,
        tenant_id=tid,
        actor_user_id=membership.user_id,
        actor_name=membership.name,
        action="ai.agent.run",
        target_type="agent_run",
        target_id=result.agent_run_id or "",
        detail={"agent": agent, "simulated": result.simulated,
                "require_human_validation": result.require_human_validation, "status": result.status},
    )
    db.commit()
    return result


# ─── Historique des runs ─────────────────────────────────────────────────────


@router.get("/agents/runs", response_model=list[schemas.AgentRunOut])
def list_agent_runs(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("ai.use")),
):
    """Historique des runs d'agents du tenant (plus récent d'abord, paginé)."""
    q = (
        db.query(models.AgentRun)
        .filter_by(tenant_id=tid)
        .order_by(models.AgentRun.created_at.desc())
    )
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


# ─── AI Copilot (chat conversationnel) ───────────────────────────────────────


@router.post("/copilot/chat", response_model=schemas.CopilotChatResponse)
def copilot_chat(
    tid: str,
    body: schemas.CopilotChatRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("ai.use")),
):
    """Interface conversationnelle AI Copilot (P25). Route le message utilisateur vers le bon agent
    (keyword matching : 'qualif' → qualification, 'score' → scoring, 'e-mail' → personalization, etc.).
    Si aucun match → réponse générique listant les agents disponibles. Retourne le résultat de
    l'agent + suggestions de prochaines actions (validation, exécution, autre agent).

    Sécurité : require_human_validation propagé — si l'agent propose une action externe OU détecte une
    injection, la réponse porte require_human_validation=True + l'appelant DOIT bloquer l'action."""
    routed = route_copilot(body.message)
    suggestions = []

    if not routed:
        # Aucun match → guide l'utilisateur vers les agents disponibles.
        agent_names = ", ".join(a["name"] for a in AGENT_LIST)
        text = (
            f"Je n'ai pas identifié d'agent spécifique dans votre message. "
            f"Agents disponibles : {agent_names}. Reformulez votre demande en mentionnant un de ces "
            f"thèmes (ex. 'qualifie ce prospect', 'rédige un post LinkedIn sur X', 'analyse mon pipeline')."
        )
        log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
                  action="ai.copilot.chat", target_type="copilot",
                  detail={"message": body.message[:200], "routed": None})
        db.commit()
        return schemas.CopilotChatResponse(
            routed_agent=None, text=text, simulated=True, require_human_validation=False,
            status="no_route", suggestions=["Précisez votre besoin (qualification, scoring, e-mail, LinkedIn, pipeline...)"],
        )

    agent_inst = get_agent(routed)
    params = dict(body.params)
    # Heuristique : si le user mentionne un contact_id dans le message, l'extraire (format uid ctc-...).
    # Sinon, params explicites (frontend peut passer contact_id dans params).
    result = agent_inst.run(db, tenant_id=tid, params=params, user_id=membership.user_id)

    # Suggestions contextuelles selon l'agent + require_human_validation.
    if result.require_human_validation:
        suggestions.append("⚠️ Validation humaine requise avant toute action externe (injection détectée).")
    if routed == "personalization":
        suggestions.append("Validez l'e-mail avant envoi (POST /crm/contacts/{cid}/email après validation).")
    elif routed == "linkedin":
        suggestions.append("Validez la publication avant publication LinkedIn (Content Studio P26).")
    elif routed == "recommendation":
        suggestions.append("Validez l'action recommandée avant exécution (relance, appel, démo).")
    elif routed == "qualification":
        suggestions.append("Mettez à jour le statut du contact selon l'action recommandée.")
    suggestions.append(f"Exécutez un autre agent (agents disponibles : {len(AGENT_LIST)}).")

    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="ai.copilot.chat", target_type="agent_run",
              target_id=result.agent_run_id or "",
              detail={"message": body.message[:200], "routed": routed,
                      "simulated": result.simulated, "require_human_validation": result.require_human_validation})
    db.commit()

    return schemas.CopilotChatResponse(
        routed_agent=routed,
        text=result.text,
        simulated=result.simulated,
        require_human_validation=result.require_human_validation,
        agent_run_id=result.agent_run_id,
        status=result.status,
        suggestions=suggestions,
        error=result.error,
    )
