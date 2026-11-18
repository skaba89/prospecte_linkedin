"""P65 — GDPR Art. 7.3 : consent gating effectif.

Avant P65, ConsentRecord.withdrawn_at était posé mais JAMAIS lu par aucun consommateur métier →
le retrait de consent "marketing" n'arrêtait AUCUN traitement (fausse conformité CNIL — Art. 7.3 violé).

Ce module expose has_active_consent() — le gate utilisé par send_email_to_contact (notifications.py)
+ le scheduler de séquences (sequences.py) pour skip l'envoi d'emails marketing si l'owner du contact
a retiré son consent marketing. Appelé aussi par gdpr_consent_withdraw pour pauser les séquences actives.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models


def has_active_consent(db: Session, tenant_id: str, user_id: str, purpose: str) -> bool:
    """Retourne True si l'user peut exercer le traitement lié au purpose (marketing/analytics/...).

    Sémantique opt-out avec retrait effectif (Art. 7.3 — le retrait doit arrêter le traitement) :
    - Aucun record (user jamais sollicité) → True (comportement par défaut préservé).
    - Record actif (withdrawn_at IS NULL) → True (consent explicite donné, ou re-grant après retrait).
    - Record retiré (withdrawn_at IS NOT NULL) ET AUCUN record actif → False (retrait effectif → ARRÊT).
    - Record retiré + record actif (re-grant après retrait) → True (le re-grant l'emporte).

    Ce gate est le point effectif d'arrêt du traitement sur retrait — send_email_to_contact l'appelle
    pour purpose="marketing" avant tout envoi de prospection. Avant P65, withdrawn_at était posé mais
    jamais lu → fausse conformité CNIL (Art. 7.3 violé)."""
    active = db.query(models.ConsentRecord).filter_by(
        tenant_id=tenant_id, user_id=user_id, purpose=purpose, withdrawn_at=None
    ).first()
    if active:
        return True  # consent actif (ou re-grant après retrait) → traitement autorisé
    withdrawn = db.query(models.ConsentRecord).filter_by(
        tenant_id=tenant_id, user_id=user_id, purpose=purpose
    ).filter(models.ConsentRecord.withdrawn_at.isnot(None)).first()
    return withdrawn is None  # True si aucun retrait (default allow), False si retrait sans re-grant (block)
