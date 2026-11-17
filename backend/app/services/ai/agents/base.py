"""P25 — BaseAgent ABC + AgentResult dataclass.

BaseAgent.run(db, tenant_id, params, user_id) → AgentResult :
  1. fetch_context(db, tenant_id, params) → dict (CRM data : contact, opportunity, notifications).
  2. build_prompt(context) → str (template {{variables}} rendu via safety.render_prompt).
  3. gateway.generate(prompt_name=self.name, variables={"_prompt": prompt}) → dict (P24-B gateway,
     fallback SimulatedProvider si dry_run/aucun provider/tous échoués).
  4. persist AgentRun (tenant, agent, context, result, status, simulated, require_human_validation,
     generation_id).
  5. retourne AgentResult (text, simulated, require_human_validation, agent_run_id, generation_id, error).

Sécurité (P25) :
  - PII redacted par le gateway (redact_pii avant provider — P24-B).
  - require_human_validation propagé (injection détectée OU action externe proposée).
  - simulated propagé (jamais présenté comme réel — anti-fake P24-B).
  - L'agent ne déclenche JAMAIS d'action externe (envoi e-mail, publication LinkedIn) — il génère du
    texte ; l'action est faite par le router qui DOIT vérifier require_human_validation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy.orm import Session

from .... import models
from .. import gateway
from ..safety import render_prompt


@dataclass
class AgentResult:
    """Résultat d'un run d'agent (P25). text = sortie générée. simulated=True → provider simulé
    (jamais présenté comme réel). require_human_validation=True → injection OU action externe
    proposée (l'appelant DOIT bloquer l'action + demander validation humaine). agent_run_id +
    generation_id = audit trail (P24-B → P25)."""
    text: str
    agent: str
    simulated: bool
    require_human_validation: bool
    agent_run_id: str | None = None
    generation_id: str | None = None
    status: str = "success"
    error: str | None = None


class BaseAgent(ABC):
    """Interface agent IA spécialisé (P25). Sous-classes : name, description, prompt_template (avec
    {{variables}}) + fetch_context (CRM data). run() orchestre : fetch → prompt → gateway → persist."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    prompt_template: ClassVar[str] = ""

    def build_prompt(self, context: dict) -> str:
        """Rend le prompt_template avec les {{variables}} du contexte (via safety.render_prompt)."""
        return render_prompt(self.prompt_template, context)

    def fetch_context(self, db: Session, tenant_id: str, params: dict) -> dict:
        """Récupère le contexte CRM (contact, opportunity, notifications). Default : retourne params
        tel quel. Sous-classes surchargent pour enrichir (ex. qualification fetch le contact)."""
        return dict(params or {})

    @abstractmethod
    def run(self, db: Session, tenant_id: str, params: dict, user_id: str | None = None) -> AgentResult:
        """Exécute l'agent : fetch_context → build_prompt → gateway.generate → persist AgentRun."""
        ...

    def _execute(self, db: Session, tenant_id: str, params: dict, user_id: str | None) -> AgentResult:
        """Helper commun : fetch + prompt + gateway.generate + persist AgentRun. Les sous-classes
        appellent _execute dans leur run() après avoir fetch_context spécifiquement."""
        context = self.fetch_context(db, tenant_id, params)
        prompt = self.build_prompt(context)
        gen = gateway.generate(
            db,
            tenant_id=tenant_id,
            prompt_name=self.name,
            variables={"_prompt": prompt},
            max_tokens=1200,
            user_id=user_id,
        )
        ar = models.AgentRun(
            tenant_id=tenant_id,
            agent=self.name,
            context=context,
            result_text=(gen.get("text") or "")[:8000],
            status=gen.get("status", "failed"),
            simulated=gen.get("simulated", True),
            require_human_validation=gen.get("require_human_validation", False),
            generation_id=gen.get("generation_id"),
            error=gen.get("error"),
            created_by=user_id,
        )
        db.add(ar)
        db.flush()
        db.commit()
        return AgentResult(
            text=gen.get("text", ""),
            agent=self.name,
            simulated=gen.get("simulated", True),
            require_human_validation=gen.get("require_human_validation", False),
            agent_run_id=ar.id,
            generation_id=gen.get("generation_id"),
            status=gen.get("status", "failed"),
            error=gen.get("error"),
        )


def _contact_summary(db: Session, tenant_id: str, contact_id: str | None) -> str:
    """Helper : résumé textuel d'un contact (nom, title, company, email redacté, score). PII redacted
    par le gateway avant l'envoi au provider — ici on inclut l'email pour le contexte interne (le
    gateway le redactera)."""
    if not contact_id:
        return "(aucun contact spécifié)"
    c = db.query(models.Contact).filter_by(id=contact_id, tenant_id=tenant_id).first()
    if not c:
        return f"(contact {contact_id} introuvable)"
    company_name = ""
    if c.company_id:
        comp = db.query(models.Company).filter_by(id=c.company_id).first()
        company_name = comp.name if comp else ""
    parts = [f"Nom: {c.first_name} {c.last_name}".strip()]
    if c.title:
        parts.append(f"Titre: {c.title}")
    if company_name:
        parts.append(f"Entreprise: {company_name}")
    if c.email:
        parts.append(f"Email: {c.email}")
    if c.linkedin_url:
        parts.append(f"LinkedIn: {c.linkedin_url}")
    parts.append(f"Score: {c.score}")
    parts.append(f"Statut: {c.status}")
    return " | ".join(parts)


def _engagement_summary(db: Session, tenant_id: str, contact_id: str | None) -> str:
    """Helper : résumé des signaux d'engagement (emails envoyés, opportunités, last contact)."""
    if not contact_id:
        return "(aucun engagement — pas de contact)"
    emails_sent = (
        db.query(models.Notification)
        .filter_by(tenant_id=tenant_id, recipient_contact_id=contact_id, type="email")
        .filter(models.Notification.status == "sent")
        .count()
    )
    opps = (
        db.query(models.Opportunity)
        .filter_by(tenant_id=tenant_id, contact_id=contact_id)
        .all()
    )
    opps_str = ", ".join(f"{o.stage} ({o.amount}€)" for o in opps[:3]) if opps else "aucune"
    return f"Emails envoyés (réels): {emails_sent} | Opportunités: {opps_str}"


def _interactions_summary(db: Session, tenant_id: str, contact_id: str | None) -> str:
    """Helper : résumé des interactions (5 dernières notifications email du contact)."""
    if not contact_id:
        return "(aucune interaction — pas de contact)"
    notifs = (
        db.query(models.Notification)
        .filter_by(tenant_id=tenant_id, recipient_contact_id=contact_id, type="email")
        .order_by(models.Notification.created_at.desc())
        .limit(5)
        .all()
    )
    if not notifs:
        return "(aucune notification email)"
    return " | ".join(f"[{n.created_at.strftime('%Y-%m-%d')}] {n.status}: {n.subject[:40]}" for n in notifs)
