"""P25 — 10 agents IA spécialisés connectés au CRM (via models) + à l'AI Gateway P24-B.

Chaque agent : name + description + prompt_template ({{variables}}) + fetch_context (CRM data).
run() = _execute (fetch → prompt → gateway.generate → persist AgentRun). require_human_validation
propagé (injection OU action externe). simulated propagé (anti-fake). L'agent ne déclenche JAMAIS
d'action externe — il génère du texte ; l'action est faite par le router qui DOIT vérifier
require_human_validation.

Agents :
  1. QualificationAgent — BANT + score de qualification + action recommandée.
  2. ScoringAgent — explique POURQUOI le score + 3 actions pour l'améliorer.
  3. ResearchAgent — critères de recherche + comptes cibles + signaux d'achat (ICP).
  4. PersonalizationAgent — e-mail personnalisé (objet + corps) pour un prospect.
  5. LinkedInAgent — publication LinkedIn (hook + corps + CTA).
  6. CompetitiveAgent — framework d'analyse concurrentielle + signaux à surveiller.
  7. MeetingPrepAgent — brief de préparation RDV (objectifs, points, questions).
  8. PerformanceAgent — analyse des performances pipeline + 3 recommandations.
  9. RecommendationAgent — prochaine meilleure action (NBA) + justification.
  10. SynthesisAgent — synthèse de l'historique d'interactions d'un contact.

COPILOT_ROUTING : keyword (dans le message utilisateur) → agent name. Le Copilot matche le message
+ route vers le bon agent + retourne le résultat dans un format chat conversationnel.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .... import models
from .base import (
    BaseAgent,
    _contact_summary,
    _engagement_summary,
    _interactions_summary,
)

# ─── 1. Qualification des prospects ──────────────────────────────────────────


class QualificationAgent(BaseAgent):
    name = "qualification"
    description = "Qualifie un prospect selon BANT (Budget, Authority, Need, Timing) + score + action."
    prompt_template = (
        "Tu es un expert en qualification de prospects B2B. Analyse ce prospect et qualifie-le.\n\n"
        "Prospect : {{contact_summary}}\n\n"
        "Évalue son fit selon le framework BANT :\n"
        "- Budget : capacité d'achat estimée\n"
        "- Authority : niveau de décision (le contact est-il décideur ?)\n"
        "- Need : besoin identifié pour notre solution\n"
        "- Timing : urgence / fenêtre d'achat\n\n"
        "Retourne une analyse structurée :\n"
        "1. Score de qualification (0-100)\n"
        "2. Justification par critère BANT\n"
        "3. Action recommandée (nurturing / qualification approfondie / démo / transmission sales)\n"
        "4. Questions de qualification à poser au prochain échange"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        return {"contact_summary": _contact_summary(db, tenant_id, params.get("contact_id"))}

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 2. Scoring explicable ───────────────────────────────────────────────────


class ScoringAgent(BaseAgent):
    name = "scoring"
    description = "Explique POURQUOI le score d'un contact + suggère 3 actions pour l'améliorer."
    prompt_template = (
        "Tu es un expert en lead scoring explicable. Le contact {{contact_name}} a un score actuel "
        "de {{score}}/100.\n\n"
        "Signaux d'engagement : {{engagement_summary}}\n\n"
        "Explique POURQUOI ce score :\n"
        "1. Décomposition du score (profil + engagement + decay)\n"
        "2. Facteurs qui influencent positivement\n"
        "3. Facteurs qui influencent négativement (decay, manque d'engagement)\n"
        "4. 3 actions concrètes pour améliorer le score (priorisées par impact)"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        cid = params.get("contact_id")
        c = db.query(models.Contact).filter_by(id=cid, tenant_id=tenant_id).first() if cid else None
        return {
            "contact_name": f"{c.first_name} {c.last_name}".strip() if c else "inconnu",
            "score": c.score if c else 0,
            "engagement_summary": _engagement_summary(db, tenant_id, cid),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 3. Recherche commerciale ────────────────────────────────────────────────


class ResearchAgent(BaseAgent):
    name = "research"
    description = "Génère critères de recherche + comptes cibles + signaux d'achat pour un ICP."
    prompt_template = (
        "Tu es un expert en recherche commerciale B2B + Sales Intelligence.\n\n"
        "Profil Client Idéal (ICP) : {{icp_description}}\n\n"
        "Génère un plan de recherche commerciale :\n"
        "1. 5 critères de recherche ciblés (secteur, taille, géographie, signaux technologiques)\n"
        "2. 10 comptes cibles potentiels (noms de secteurs/types d'entreprises — pas de noms réels "
        "inventés, des archétypes)\n"
        "3. Signaux d'achat à surveiller (funding, hiring, tech stack changes, leadership changes)\n"
        "4. Sources autorisées pour la recherche (LinkedIn Sales Navigator, Apollo, Crunchbase)\n"
        "5. Message d'approche initial suggéré pour ce segment"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        return {"icp_description": params.get("icp_description", "SaaS B2B mid-market, 50-500 employés, Europe")}

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 4. Personnalisation des messages ────────────────────────────────────────


class PersonalizationAgent(BaseAgent):
    name = "personalization"
    description = "Rédige un e-mail personnalisé (objet + corps) pour un prospect donné."
    prompt_template = (
        "Tu es un expert en personnalisation d'e-mails de prospection B2B (cold outreach).\n\n"
        "Prospect : {{contact_summary}}\n\n"
        "Template de base : {{template}}\n\n"
        "Rédige un e-mail personnalisé :\n"
        "1. Objet (court, accrocheur, personnalisé — pas de clickbait)\n"
        "2. Corps (3-5 phrases) : montre que tu connais son contexte, propose de la valeur, CTA clair\n"
        "3. Respecte les RGPD (pas de données sensibles, opt-out)\n"
        "4. Ton professionnel mais humain (pas de jargon vide)\n\n"
        "IMPORTANT : cet e-mail NE sera PAS envoyé automatiquement — il est proposé pour validation "
        "humaine avant envoi."
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        return {
            "contact_summary": _contact_summary(db, tenant_id, params.get("contact_id")),
            "template": params.get("template", "Cold outreach standard — intro + value prop + CTA démo"),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 5. Rédaction LinkedIn ───────────────────────────────────────────────────


class LinkedInAgent(BaseAgent):
    name = "linkedin"
    description = "Rédige une publication LinkedIn (hook + corps + CTA) sur un sujet + style donnés."
    prompt_template = (
        "Tu es un expert en rédaction LinkedIn B2B (content marketing pour sales leaders).\n\n"
        "Sujet : {{topic}}\n"
        "Style : {{style}}\n\n"
        "Rédige une publication LinkedIn :\n"
        "1. Hook (1ère ligne accrocheuse — fait défiler + clique)\n"
        "2. Corps (3-5 paragraphes courts, aérés, storytelling ou insight)\n"
        "3. CTA (invite à commenter / DM / réagir)\n"
        "4. Hashtags pertinents (3-5)\n\n"
        "Respecte les bonnes pratiques LinkedIn : pas de clickbait trompeur, pas de contenu "
        "promotionnal pur, valeur d'abord. IMPORTANT : la publication NE sera PAS publiée "
        "automatiquement — elle est proposée pour validation humaine avant publication (Content Studio P26)."
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        return {
            "topic": params.get("topic", "Les défis du sales B2B en 2026"),
            "style": params.get("style", "expert, terrain, data-driven"),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 6. Veille concurrentielle ───────────────────────────────────────────────


class CompetitiveAgent(BaseAgent):
    name = "competitive"
    description = "Framework d'analyse concurrentielle + signaux récents à surveiller."
    prompt_template = (
        "Tu es un expert en veille concurrentielle + competitive intelligence B2B.\n\n"
        "Concurrent analysé : {{competitor}}\n"
        "Notre produit : {{our_product}}\n\n"
        "Génère un framework d'analyse concurrentielle :\n"
        "1. Positionnement (catégorie, ciblage, proposition de valeur)\n"
        "2. Produits + pricing (fourchettes, modèles de pricing)\n"
        "3. Forces (avantages compétitifs)\n"
        "4. Faiblesses (gaps, friction connue)\n"
        "5. Signaux récents à surveiller (funding, product launches, hiring, leadership changes)\n"
        "6. Angles de différenciation pour notre équipe sales\n\n"
        "IMPORTANT : ne invente PAS de faits spécifiques non vérifiables (chiffres, dates, deals) — "
        "donne un framework + des catégories de signaux, pas des assertions factuelles."
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        return {
            "competitor": params.get("competitor", "Waalaxy"),
            "our_product": params.get("our_product", "RevPulse (CRM + prospection + Sales Intelligence + IA)"),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 7. Préparation des rendez-vous ──────────────────────────────────────────


class MeetingPrepAgent(BaseAgent):
    name = "meeting_prep"
    description = "Brief de préparation RDV : objectifs, points de discussion, questions clés."
    prompt_template = (
        "Tu es un expert en préparation de rendez-vous commerciaux B2B (discovery / démo / negotiation).\n\n"
        "Prospect : {{contact_summary}}\n\n"
        "Contexte opportunité : {{opportunity_summary}}\n\n"
        "Génère un brief de préparation de RDV :\n"
        "1. Objectifs du RDV (primaire + secondaire)\n"
        "2. Points de discussion (3-5, priorisés)\n"
        "3. Questions clés à poser (découverte, qualification, objection handling)\n"
        "4. Contexte à connaître (historique, signaux récents)\n"
        "5. Anticipation des objections + réponses\n"
        "6. Next steps à proposer"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        cid = params.get("contact_id")
        oid = params.get("opportunity_id")
        opp_summary = "(aucune opportunité)"
        if oid:
            o = db.query(models.Opportunity).filter_by(id=oid, tenant_id=tenant_id).first()
            if o:
                opp_summary = f"Stage: {o.stage} | Montant: {o.value}€ | Titre: {o.title}"
        return {
            "contact_summary": _contact_summary(db, tenant_id, cid),
            "opportunity_summary": opp_summary,
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 8. Analyse des performances ─────────────────────────────────────────────


class PerformanceAgent(BaseAgent):
    name = "performance"
    description = "Analyse les performances du pipeline + 3 recommandations priorisées."
    prompt_template = (
        "Tu es un expert en analyse de performance commerciale (RevOps / sales analytics).\n\n"
        "Métriques du pipeline : {{metrics}}\n\n"
        "Analyse :\n"
        "1. Points forts (ce qui marche — stages avec bonne conversion)\n"
        "2. Points faibles (goulots, fuites, stages à faible conversion)\n"
        "3. Benchmarks / tendances (si identifiables)\n"
        "4. 3 recommandations priorisées (par impact x effort) avec estimation d'effet\n"
        "5. KPIs à surveiller en priorité le mois prochain"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        # Métriques réelles du tenant : nb contacts, opportunities par stage, revenue pipeline.
        n_contacts = db.query(models.Contact).filter_by(tenant_id=tenant_id).count()
        opps = db.query(models.Opportunity).filter_by(tenant_id=tenant_id).all()
        by_stage = {}
        revenue = 0.0
        for o in opps:
            by_stage[o.stage] = by_stage.get(o.stage, 0) + 1
            if o.stage == "closed_won":
                revenue += float(o.value or 0)
        pipeline = sum(float(o.value or 0) for o in opps if o.stage != "closed_won")
        metrics = (
            f"Contacts: {n_contacts} | Opportunités: {len(opps)} | Par stage: {by_stage} | "
            f"Pipeline (open): {pipeline}€ | Revenue (won): {revenue}€"
        )
        return {"metrics": metrics}

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 9. Recommandation commerciale (Next Best Action) ────────────────────────


class RecommendationAgent(BaseAgent):
    name = "recommendation"
    description = "Recommande la prochaine meilleure action (NBA) pour un prospect + justification."
    prompt_template = (
        "Tu es un expert en recommandation commerciale (Next Best Action) B2B.\n\n"
        "Prospect : {{contact_summary}}\n"
        "Engagement : {{engagement_summary}}\n\n"
        "Recommande la prochaine meilleure action :\n"
        "1. NBA prioritaire (1 action — ex. relance e-mail / appel / invitation démo / nurture)\n"
        "2. Justification (pourquoi cette action MAINTENANT, basée sur les signaux)\n"
        "3. Timing optimal (quand exécuter)\n"
        "4. Message/angle suggéré (court)\n"
        "5. Alternative si l'action principale n'est pas possible\n\n"
        "IMPORTANT : l'action NE sera PAS exécutée automatiquement — elle est proposée pour "
        "validation humaine."
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        cid = params.get("contact_id")
        return {
            "contact_summary": _contact_summary(db, tenant_id, cid),
            "engagement_summary": _engagement_summary(db, tenant_id, cid),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── 10. Synthèse des interactions ───────────────────────────────────────────


class SynthesisAgent(BaseAgent):
    name = "synthesis"
    description = "Synthétise l'historique d'interactions d'un contact + sentiment + prochaine étape."
    prompt_template = (
        "Tu es un expert en synthèse de relations clients B2B.\n\n"
        "Prospect : {{contact_summary}}\n\n"
        "Historique d'interactions (5 dernières) : {{interactions}}\n\n"
        "Synthétise :\n"
        "1. Résumé de la relation (en 2-3 phrases — où en est-on)\n"
        "2. Points clés discutés (thèmes récurrents)\n"
        "3. Sentiment estimé (engaged / tiède / à risque / désengaged)\n"
        "4. Prochaine étape recommandée\n"
        "5. Risques / points d'attention"
    )

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        cid = params.get("contact_id")
        return {
            "contact_summary": _contact_summary(db, tenant_id, cid),
            "interactions": _interactions_summary(db, tenant_id, cid),
        }

    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> object:
        return self._execute(db, tenant_id, params, user_id)


# ─── Registry + Copilot routing ──────────────────────────────────────────────


AGENTS: dict[str, BaseAgent] = {
    "qualification": QualificationAgent(),
    "scoring": ScoringAgent(),
    "research": ResearchAgent(),
    "personalization": PersonalizationAgent(),
    "linkedin": LinkedInAgent(),
    "competitive": CompetitiveAgent(),
    "meeting_prep": MeetingPrepAgent(),
    "performance": PerformanceAgent(),
    "recommendation": RecommendationAgent(),
    "synthesis": SynthesisAgent(),
}

AGENT_LIST = [
    {"name": a.name, "description": a.description}
    for a in AGENTS.values()
]


def get_agent(name: str) -> BaseAgent | None:
    """Retourne l'instance agent par nom. None si inconnu."""
    return AGENTS.get(name)


# COPILOT_ROUTING : keyword (lowercased, dans le message utilisateur) → agent name.
# Le Copilot matche le premier keyword trouvé + route vers l'agent correspondant.
COPILOT_ROUTING: list[tuple[str, str]] = [
    ("qualif", "qualification"),
    ("bant", "qualification"),
    ("score", "scoring"),
    ("pourquoi ce score", "scoring"),
    ("recherche", "research"),
    ("icp", "research"),
    ("cible", "research"),
    ("personnalis", "personalization"),
    ("e-mail", "personalization"),
    ("email", "personalization"),
    ("outreach", "personalization"),
    ("linkedin", "linkedin"),
    ("post linkedin", "linkedin"),
    ("publication", "linkedin"),
    ("concurren", "competitive"),
    ("veille", "competitive"),
    ("rendez-vous", "meeting_prep"),
    ("rdv", "meeting_prep"),
    ("meeting", "meeting_prep"),
    ("préparation", "meeting_prep"),
    ("performan", "performance"),
    ("pipeline", "performance"),
    ("analys", "performance"),
    ("recommand", "recommendation"),
    ("prochaine action", "recommendation"),
    ("nba", "recommendation"),
    ("synth", "synthesis"),  # matche synthétise (é) / synthèse (è) / synthese — accent-agnostic
    ("synthès", "synthesis"),
    ("synthese", "synthesis"),
    ("résumé", "synthesis"),
    ("historique", "synthesis"),
]


def route_copilot(message: str) -> str | None:
    """Route un message utilisateur vers un agent name (keyword matching). None si aucun match."""
    if not message:
        return None
    msg_lower = message.lower()
    for keyword, agent_name in COPILOT_ROUTING:
        if keyword in msg_lower:
            return agent_name
    return None
