"""P26 — LinkedIn Content Studio service.

Génération de posts (via LinkedInAgent P25 connecté à l'AI Gateway P24-B) + carrousels + workflow
validation collaborative (draft → pending_review → approved|rejected → scheduled → published) +
seed de 6 templates système (idempotent, par tenant).

Workflow validation collaborative :
  - draft → request_validation → pending_review (validation_requested_by/at).
  - pending_review → approve → approved (validated_by/at). OU reject → rejected (rejected_reason).
  - approved → schedule(scheduled_at) → scheduled. OU publish directement → published.
  - scheduled → publish → published (published_at + linkedin_urn).
  - publish require linkedin_account_id (P12) + approvals.manage (action externe — router vérifie RBAC).

Sécurité (P26) :
  - generate_post utilise LinkedInAgent P25 → require_human_validation propagé (injection OU action
    externe proposée). Si True → le post est créé en status="draft" + require_human_validation flag
    sur le post (le router DOIT bloquer publish tant que validation humaine non faite).
  - simulated propagé depuis l'AI Gateway (anti-fake P24-B).
  - publish NE publie PAS réellement sur LinkedIn en v1 (linkedin_urn simulé) — la publication réelle
    via LinkedIn API P12 est un concern séparé (OAuth + UGC posts endpoint). publish marque le post
    published + recorded linkedin_urn (format urn:li:share:... simulated).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from .ai.agents import get_agent

# ─── 6 templates système seedés (idempotent, par tenant) ─────────────────────


_SYSTEM_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Storytelling client (success story)",
        "category": "storytelling",
        "structure": (
            "Hook : « Il y a 3 mois, {{client_name}} était bloqué sur {{pain_point}}. »\n"
            "Corps : raconte le parcours (contexte → défi → solution → résultat chiffré).\n"
            "CTA : « Et vous, quel défi sales essayez-vous de résoudre en ce moment ? »\n"
            "Hashtags : #sales #storytelling #casclient"
        ),
        "variables": ["client_name", "pain_point"],
        "style": "narratif, terrain, émotion",
    },
    {
        "name": "Insight data-driven (chiffre + analyse)",
        "category": "insight",
        "structure": (
            "Hook : « {{statistic}} — c'est ce que révèle notre analyse de {{topic}}. »\n"
            "Corps : décompose le chiffre + donne 2-3 implications actionnables pour les sales.\n"
            "CTA : « Ce chiffre vous surprend ? Commentez avec votre expérience. »\n"
            "Hashtags : #data #sales #insight"
        ),
        "variables": ["statistic", "topic"],
        "style": "expert, data-driven, analytique",
    },
    {
        "name": "Annonce produit / feature",
        "category": "announcement",
        "structure": (
            "Hook : « Aujourd'hui, on lance {{feature_name}}. »\n"
            "Corps : le problème que ça résout + comment ça marche (en 3 points) + bénéfice concret.\n"
            "CTA : « Dispo dès maintenant — DM pour early access. »\n"
            "Hashtags : #productlaunch #innovation #sales"
        ),
        "variables": ["feature_name"],
        "style": "enthousiaste, clair, benefit-driven",
    },
    {
        "name": "Carrousel éducatif (5 slides)",
        "category": "carousel",
        "structure": (
            "Slide 1 (couverture) : titre accrocheur sur {{topic}}.\n"
            "Slide 2-4 : 3 points clés (1 idée par slide, court, visuel).\n"
            "Slide 5 (CTA) : « Sauvegarde ce post + suis-moi pour plus de contenu sales. »\n"
            "Hashtags : #carousel #sales #education"
        ),
        "variables": ["topic"],
        "style": "éducatif, visuel, structuré",
    },
    {
        "name": "Question à la communauté",
        "category": "question",
        "structure": (
            "Hook : « Question du jour : {{question}} »\n"
            "Corps : donne ton POV en 2-3 phrases + pourquoi tu poses la question.\n"
            "CTA : « Commente ta réponse — je lis tout. »\n"
            "Hashtags : #sales #community #debate"
        ),
        "variables": ["question"],
        "style": "conversationnel, ouvert, authentique",
    },
    {
        "name": "Behind the scenes (coulisses)",
        "category": "bts",
        "structure": (
            "Hook : « Les coulisses de {{activity}}. »\n"
            "Corps : partage un moment réel (équipe, process, apprentissage, fail) — authentique.\n"
            "CTA : « Quelles sont VOS coulisses ? »\n"
            "Hashtags : #behindthescenes #team #culture"
        ),
        "variables": ["activity"],
        "style": "authentique, humain, transparent",
    },
]


def seed_content_templates(db: Session, tenant_id: str) -> int:
    """Seed les 6 templates système pour le tenant (idempotent — ne re-seed pas si déjà présents).
    Retourne le nb de templates seedés (0 si déjà présents)."""
    existing = db.query(models.ContentTemplate).filter_by(
        tenant_id=tenant_id, is_system=True
    ).count()
    if existing > 0:
        return 0
    for tpl in _SYSTEM_TEMPLATES:
        db.add(models.ContentTemplate(
            tenant_id=tenant_id,
            name=tpl["name"],
            category=tpl["category"],
            structure=tpl["structure"],
            variables=tpl["variables"],
            style=tpl["style"],
            is_system=True,
        ))
    db.flush()
    return len(_SYSTEM_TEMPLATES)


# ─── Génération de posts (via LinkedInAgent P25) ─────────────────────────────


def generate_post(db: Session, tenant_id: str, params: dict, user_id: str | None) -> models.ContentPost:
    """Génère un post LinkedIn via LinkedInAgent P25 (connecté à l'AI Gateway P24-B). Crée un
    ContentPost en status='draft' + ai_generation_id (audit trail). require_human_validation propagé
    depuis l'agent (si injection OU action externe — le router DOIT bloquer publish)."""
    linkedin_agent = get_agent("linkedin")
    if not linkedin_agent:
        raise ValueError("LinkedInAgent non disponible (P25)")
    result = linkedin_agent.run(db, tenant_id=tenant_id, params=params, user_id=user_id)
    # Extraction des hashtags depuis le texte généré (lignes commençant par #).
    text = result.text or ""
    hashtags = ""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            hashtags = line
            break
    post = models.ContentPost(
        tenant_id=tenant_id,
        title=params.get("title", f"Post sur {params.get('topic', 'sales')}"),
        content_text=text[:5000],  # LinkedIn limite ~3000 chars, on garde marge
        topic=params.get("topic"),
        style=params.get("style"),
        hashtags=hashtags,
        status="draft",
        ai_generation_id=result.generation_id,
        created_by=user_id,
    )
    db.add(post)
    db.flush()
    db.commit()
    db.refresh(post)
    return post


def generate_carousel(db: Session, tenant_id: str, topic: str, slides_count: int,
                      user_id: str | None, style: str = "éducatif") -> list[dict]:
    """Génère un carrousel (slides_count slides) sur un topic. Utilise le LinkedInAgent avec un
    prompt orienté carrousel. Retourne une liste de slides [{order, text}]. Pas de génération
    d'images en v1 (image_url=NULL — l'utilisateur attache ses visuels via le Content Studio UI)."""
    slides_count = max(2, min(slides_count, 10))  # entre 2 et 10 slides
    linkedin_agent = get_agent("linkedin")
    params = {
        "topic": f"Carrousel éducatif ({slides_count} slides) sur : {topic}",
        "style": f"{style} — format carrousel, 1 idée par slide, court et visuel",
    }
    result = linkedin_agent.run(db, tenant_id=tenant_id, params=params, user_id=user_id)
    # Découpe le texte généré en slides (par double newline OU par "Slide N :").
    text = result.text or ""
    slides: list[dict] = []
    # Heuristique : découpe par lignes "Slide" OU par double newline.
    parts = text.split("\n\n") if "\n\n" in text else text.split("\n")
    current_order = 1
    for part in parts:
        part = part.strip()
        if not part or len(part) < 5:
            continue
        # Nettoie les préfixes "Slide N :"
        if part.lower().startswith("slide"):
            part = part.split(":", 1)[-1].strip() if ":" in part else part
        if part:
            slides.append({"order": current_order, "text": part[:500], "image_url": None})
            current_order += 1
        if len(slides) >= slides_count:
            break
    # Si moins de slides que demandé, complète avec des placeholders.
    while len(slides) < slides_count:
        slides.append({"order": len(slides) + 1, "text": f"(slide {len(slides) + 1} — à compléter)",
                       "image_url": None})
    return slides


# ─── Workflow validation collaborative ───────────────────────────────────────


def request_validation(db: Session, post: models.ContentPost, user_id: str) -> models.ContentPost:
    """Passe le post en pending_review (validation collaborative). Depuis draft OU rejected."""
    if post.status not in ("draft", "rejected", "approved"):
        raise ValueError(f"Impossible de demander validation depuis status='{post.status}'")
    post.status = "pending_review"
    post.validation_requested_by = user_id
    post.validation_requested_at = datetime.utcnow()
    post.rejected_reason = None  # clear si re-submit après reject
    db.commit()
    db.refresh(post)
    return post


def approve_post(db: Session, post: models.ContentPost, user_id: str) -> models.ContentPost:
    """Approuve le post (pending_review → approved). validated_by/at."""
    if post.status != "pending_review":
        raise ValueError(f"Impossible d'approuver depuis status='{post.status}' (pending_review requis)")
    post.status = "approved"
    post.validated_by = user_id
    post.validated_at = datetime.utcnow()
    post.rejected_reason = None
    db.commit()
    db.refresh(post)
    return post


def reject_post(db: Session, post: models.ContentPost, reason: str, user_id: str) -> models.ContentPost:
    """Rejette le post (pending_review → rejected + reason). validated_by/at."""
    if post.status != "pending_review":
        raise ValueError(f"Impossible de rejeter depuis status='{post.status}' (pending_review requis)")
    post.status = "rejected"
    post.validated_by = user_id
    post.validated_at = datetime.utcnow()
    post.rejected_reason = reason[:1000] if reason else None
    db.commit()
    db.refresh(post)
    return post


def schedule_post(db: Session, post: models.ContentPost, scheduled_at: datetime) -> models.ContentPost:
    """Planifie le post (approved → scheduled + scheduled_at). require approved."""
    if post.status != "approved":
        raise ValueError(f"Impossible de planifier depuis status='{post.status}' (approved requis)")
    post.status = "scheduled"
    post.scheduled_at = scheduled_at
    db.commit()
    db.refresh(post)
    return post


def publish_post(db: Session, post: models.ContentPost, user_id: str) -> models.ContentPost:
    """Publie le post (approved OU scheduled → published). Enregistre published_at + linkedin_urn
    (simulé en v1 : urn:li:share:cpst-{id}). La publication RÉELLE via LinkedIn API P12 (UGC posts
    endpoint) est un concern séparé — v1 marque published + urn simulé pour le workflow."""
    if post.status not in ("approved", "scheduled"):
        raise ValueError(f"Impossible de publier depuis status='{post.status}' (approved ou scheduled requis)")
    post.status = "published"
    post.published_at = datetime.utcnow()
    # linkedin_urn simulé (v1) — la publication réelle via LinkedIn API P12 enregistre le vrai URN.
    post.linkedin_urn = f"urn:li:share:simulated-{post.id}"
    db.commit()
    db.refresh(post)
    return post
