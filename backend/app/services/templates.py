"""Templates d'e-mails de prospection (P13). Rendus avec un contexte (contact, tenant,
expéditeur, message personnalisé). Chaque template : (subject, body_text, body_html).

Templates :
  - cold_outreach : premier contact (froid) — accroche + valeur + CTA.
  - follow_up     : relance après silence — angle nouveau.
  - intro         : présentation suite à connexion LinkedIn (pont P12).
  - re_engagement : réveil d'un prospect dormant.

Sécurité : escape HTML de tout le contexte injecté (anti-XSS). Pas de balise inline
dangereuse, pas d'attribut style utilisateur. Le HTML est structuré (inline-safe pour
les clients mail).
"""
import html
from dataclasses import dataclass


@dataclass(frozen=True)
class _Template:
    id: str
    name: str
    description: str
    subject: str  # contient des {placeholders}


_TEMPLATES: dict[str, _Template] = {
    "cold_outreach": _Template(
        "cold_outreach", "Premier contact (froid)",
        "Premier e-mail de prospection — accroche contextuelle + valeur + appel.",
        "Idée pour {company} — {topic}"),
    "follow_up": _Template(
        "follow_up", "Relance (suivi)",
        "Relance après un premier contact sans réponse — angle nouveau + faible friction.",
        "Suite à notre échange — {contact_name}"),
    "intro": _Template(
        "intro", "Présentation (post-LinkedIn)",
        "Présentation suite à une connexion LinkedIn (P12) — cadre la relation hors réseau.",
        "Suite à notre connexion LinkedIn — {contact_name}"),
    "re_engagement": _Template(
        "re_engagement", "Ré-engagement (prospect dormant)",
        "Réveille un prospect dormant avec un angle/contenu récent — reconversation.",
        "Ressorti ce point pour {company}"),
}


def list_templates() -> list[dict]:
    """Liste les templates disponibles (id + name + description)."""
    return [{"id": t.id, "name": t.name, "description": t.description} for t in _TEMPLATES.values()]


def get_template(template_id: str) -> _Template | None:
    return _TEMPLATES.get(template_id)


def render(template_id: str, *, contact_name: str, company: str, tenant_name: str,
           sender_name: str, topic: str = "", custom_message: str = "") -> tuple[str, str, str]:
    """Rend un template → (subject, body_text, body_html). Échappe tout le contexte (anti-XSS).
    Lève KeyError si template_id inconnu (l'appelant gère → 400)."""
    t = _TEMPLATES[template_id]
    ctx = {
        "contact_name": html.escape(contact_name or ""),
        "company": html.escape(company or "votre entreprise"),
        "tenant_name": html.escape(tenant_name or ""),
        "sender_name": html.escape(sender_name or "l'équipe"),
        "topic": html.escape(topic or "votre croissance"),
        "custom_message": html.escape(custom_message or ""),
    }
    subject = t.subject.format(**ctx)
    custom_block = f"\n\n{ctx['custom_message']}\n" if custom_message else ""
    custom_block_html = (
        f"<p style='margin:16px 0;padding:12px;border-left:3px solid #0a66c2;"
        f"background:#f5f7fa;font-style:italic'>{ctx['custom_message']}</p>"
        if custom_message else "")
    body_text = (
        f"Bonjour {ctx['contact_name']},\n\n"
        f"{custom_block.lstrip()}"
        f"{t.description}.\n\n"
        f"— {ctx['sender_name']} ({ctx['tenant_name']})")
    body_html = (
        f"<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        f"max-width:560px;margin:auto;color:#1f2937;line-height:1.5'>"
        f"<p>Bonjour {ctx['contact_name']},</p>"
        f"{custom_block_html}"
        f"<p style='color:#374151'>{t.description}.</p>"
        f"<p style='margin-top:24px'>— <strong>{ctx['sender_name']}</strong> "
        f"<span style='color:#6b7280'>({ctx['tenant_name']})</span></p>"
        f"</div>")
    return subject, body_text, body_html
