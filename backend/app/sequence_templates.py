"""P86 — Bibliothèque de templates de séquences réutilisables.

Curated static library of pre-built sequence templates (no DB — ships immediately).
Chaque template est un dict avec id (slug), name, description, category, steps
(liste de {template_id, delay_days}). Les template_id sont validés contre
_VALID_TEMPLATES du router sequences (cold_outreach|follow_up|intro|re_engagement).

Endpoints (dans routers/sequences.py) :
  GET  /tenants/{tid}/crm/sequences/templates         → liste (read-only, any member)
  POST /tenants/{tid}/crm/sequences/templates/{id}/clone → clone en Sequence réelle (crm.manage)

Design choice : static library plutôt que table DB. Raisons :
  - ships immédiatement avec un set curé (pas de migration, pas de CRUD admin)
  - les templates sont du contenu produit (commun à tous les tenants)
  - si des templates custom par tenant sont requis plus tard → ajouter une table
    SequenceTemplate (P87+) sans casser cette API (le clone reste identique)
"""
from __future__ import annotations

# Catégories canoniques (pour filtrage UI).
CATEGORIES = ("prospection", "onboarding", "re-engagement", "closing")

# 8 templates curés couvrant 4 catégories. delay_days = délai avant envoi après
# le step précédent (0 = immédiat). Inspirés des patterns B2B éprouvés
# (cold outreach, drip onboarding, re-engagement, post-démo follow-up).
SEQUENCE_TEMPLATES: list[dict] = [
    {
        "id": "cold-outreach-3step",
        "name": "Cold outreach 3 étapes",
        "description": "Séquence d'approche à froid classique : premier contact, relance à J+3, "
                       "dernière chance à J+7. Idéale pour prospecter une nouvelle liste.",
        "category": "prospection",
        "steps": [
            {"template_id": "cold_outreach", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 3},
            {"template_id": "re_engagement", "delay_days": 7},
        ],
    },
    {
        "id": "intro-express",
        "name": "Intro express",
        "description": "Une seule étape d'introduction rapide — pour un premier contact léger "
                       "avant une séquence plus longue, ou pour un réseau chaud.",
        "category": "prospection",
        "steps": [
            {"template_id": "intro", "delay_days": 0},
        ],
    },
    {
        "id": "intro-followup-2step",
        "name": "Intro + follow-up rapide",
        "description": "Introduction suivie d'une relance à J+2. Bon compromis pour les prospects "
                       "tièdes : pas trop agressif, garde le momentum.",
        "category": "prospection",
        "steps": [
            {"template_id": "intro", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 2},
        ],
    },
    {
        "id": "drip-onboarding-5step",
        "name": "Drip onboarding 5 étapes",
        "description": "Séquence d'onboarding étalée sur 14 jours : intro, puis 3 follow-ups "
                       "progressifs, puis re-engagement. Pour nouveaux clients ou leads qualifiés.",
        "category": "onboarding",
        "steps": [
            {"template_id": "intro", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 1},
            {"template_id": "follow_up", "delay_days": 3},
            {"template_id": "follow_up", "delay_days": 7},
            {"template_id": "re_engagement", "delay_days": 14},
        ],
    },
    {
        "id": "long-sequence-7step",
        "name": "Séquence longue 7 étapes",
        "description": "Séquence persistante sur 30 jours pour prospects à fort ACV : cold, "
                       "follow-ups échelonnés, 2 re-engagement. Maximise le taux de réponse.",
        "category": "prospection",
        "steps": [
            {"template_id": "cold_outreach", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 2},
            {"template_id": "follow_up", "delay_days": 5},
            {"template_id": "re_engagement", "delay_days": 10},
            {"template_id": "follow_up", "delay_days": 15},
            {"template_id": "follow_up", "delay_days": 21},
            {"template_id": "re_engagement", "delay_days": 30},
        ],
    },
    {
        "id": "post-demo-followup",
        "name": "Follow-up post-démo",
        "description": "Séquence de closing après une démo produit : follow-up immédiat, "
                       "relance J+1, breakup email J+4. Conçue pour convertir les démos en deals.",
        "category": "closing",
        "steps": [
            {"template_id": "follow_up", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 1},
            {"template_id": "re_engagement", "delay_days": 4},
        ],
    },
    {
        "id": "soft-re-engagement",
        "name": "Soft re-engagement 2 étapes",
        "description": "Deux re-engagement espacés de 10 jours pour réveiller une liste dormante "
                       "sans spammer. Doux, respectueux du inbox.",
        "category": "re-engagement",
        "steps": [
            {"template_id": "re_engagement", "delay_days": 0},
            {"template_id": "re_engagement", "delay_days": 10},
        ],
    },
    {
        "id": "re-engagement-2step",
        "name": "Re-engagement + follow-up",
        "description": "Re-engagement immédiat puis follow-up à J+5. Plus direct que le soft "
                       "re-engagement — pour les prospects refroidis récemment.",
        "category": "re-engagement",
        "steps": [
            {"template_id": "re_engagement", "delay_days": 0},
            {"template_id": "follow_up", "delay_days": 5},
        ],
    },
]


def list_templates() -> list[dict]:
    """Retourne tous les templates (copie défensive pour éviter la mutation externe)."""
    return [dict(t) for t in SEQUENCE_TEMPLATES]


def get_template(tpl_id: str) -> dict | None:
    """Retourne un template par son id (slug), ou None si introuvable."""
    for t in SEQUENCE_TEMPLATES:
        if t["id"] == tpl_id:
            return dict(t)
    return None


def template_to_out(t: dict) -> dict:
    """Sérialise un template pour la réponse API (ajoute step_count, garde steps lisibles)."""
    return {
        "id": t["id"],
        "name": t["name"],
        "description": t["description"],
        "category": t["category"],
        "step_count": len(t["steps"]),
        "steps": [
            {"template_id": s["template_id"], "delay_days": s["delay_days"]} for s in t["steps"]
        ],
    }
