"""P18 — Webhooks outbound : emit_event (crée + livre les WebhookDelivery pour chaque
webhook actif souscrit à l'événement) + deliver (POST payload signé HMAC-SHA256, retry).

emit_event est appelé par les routers (crm/sequences/notifications) après une mutation clée
(contact.created, contact.scored, sequence.enrolled, sequence.step_completed, email.sent,
opportunity.created). La livraison est synchronous + best-effort (timeout court, jamais lève) :
une webhook lent/down ne bloque pas la mutation source (au pire 5s de timeout).

Sécurité : HMAC-SHA256(secret, body) → header `X-RevPulse-Signature` (le destinataire vérifie
l'authenticité). `X-RevPulse-Event` = nom de l'événement. Retry : attempts incrémenté à chaque
deliver ; status pending tant que attempts < max_attempts, failed sinon. deliver_pending retry
les pending (endpoint retry manuel OU cron P19 automatique via start_scheduler).

P19 — start_scheduler() : background thread daemon (stdlib threading, pas de dep). Tourne toutes
les WEBHOOK_SCHEDULER_INTERVAL_SECONDS (défaut 60s — plus fréquent que les séquences car les
webhooks sont time-sensitives : un endpoint down au moment de l'emit sera retenté dans la minute).
Désactivé si interval <= 0. Démarré depuis main._startup (à côté du sequences scheduler).
Multi-worker caveat : en prod multi-workers, chaque worker lance son scheduler → --workers 1 OU
un process scheduler dédié (noté dans le README). Pour cette app (single-worker uvicorn), OK.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from . import crypto
from .url_safety import safe_urlopen, validate_url

log = logging.getLogger("revpulse.webhooks")

# Whitelist des événements supportés (emit_event ignore les events inconnus — anti-injection
# de config arbitraire via l'API create).
SUPPORTED_EVENTS = {
    "contact.created",
    "contact.scored",
    "sequence.enrolled",
    "sequence.step_completed",
    "email.sent",
    "opportunity.created",
}

# Timeout court pour ne pas bloquer la requête HTTP entrante (un webhook down ne doit pas
# pendre la mutation source). Monkey-patchable en tests.
DELIVERY_TIMEOUT = 5.0


def gen_secret() -> str:
    """Génère un secret HMAC 32 chars hex (128 bits). Alias de crypto.new_secret (P24-A)."""
    return crypto.new_secret()


def _sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256(secret, body) → hex. Le destinataire recalcule avec son secret partagé."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _get_secret(wh: models.Webhook) -> str:
    """Récupère le secret de signature (P24-A — decrypt secret_enc). Lazy-migration : si secret_enc
    est NULL + secret plaintext non vide → encrypte vers secret_enc + clear secret (persisté au
    commit du router appelant). Lève RuntimeError si aucun secret (webhook corrompu)."""
    if wh.secret_enc:
        return crypto.decrypt(wh.secret_enc)
    if wh.secret:
        plaintext = wh.secret
        wh.secret_enc = crypto.encrypt(plaintext)
        wh.secret = ""  # clear plaintext (NOT NULL → empty string satisfait)
        return plaintext
    raise RuntimeError(f"Webhook {wh.id} sans secret (ni secret_enc ni secret)")


def _store_secret(wh: models.Webhook, plaintext: str) -> None:
    """Chiffre + stocke un secret dans secret_enc + clear le plaintext secret (P24-A). Utilisé à la
    création + rotation. Le plaintext n'est JAMAIS persisté en clair (secret="")."""
    wh.secret_enc = crypto.encrypt(plaintext)
    wh.secret = ""


def _validate_events(events: list[str]) -> list[str]:
    """Filtre les events non supportés (anti-injection). Lève ValueError si tous filtrés."""
    valid = [e for e in events if e in SUPPORTED_EVENTS]
    if not valid:
        raise ValueError(f"aucun event supporté parmi {events} (supportés: {sorted(SUPPORTED_EVENTS)})")
    return valid


def emit_event(db: Session, *, tenant_id: str, event: str, payload: dict) -> int:
    """Crée + livre une WebhookDelivery pour chaque webhook actif du tenant souscrit à `event`.
    Retourne le nombre de deliveries créées. Best-effort : ne lève jamais (log + skip).
    Ne commit pas — le router appelant commit (atomique avec la mutation source)."""
    if event not in SUPPORTED_EVENTS:
        log.warning("emit_event: event %r hors whitelist — ignoré", event)
        return 0
    try:
        whs = (
            db.query(models.Webhook)
            .filter(
                models.Webhook.tenant_id == tenant_id,
                models.Webhook.active.is_(True),
            )
            .all()
        )
        n = 0
        for wh in whs:
            events = wh.events or []
            if event not in events:
                continue
            d = models.WebhookDelivery(
                webhook_id=wh.id,
                event=event,
                payload=payload,
                status="pending",
                attempts=0,
            )
            db.add(d)
            db.flush()
            deliver(db, d)  # synchronous best-effort
            n += 1
        return n
    except Exception as e:  # best-effort : ne casse pas la mutation source
        log.warning("emit_event %r échoué (best-effort): %s: %s", event, type(e).__name__, str(e)[:200])
        return 0


def deliver(db: Session, delivery: models.WebhookDelivery) -> bool:
    """Tente une livraison : POST payload signé au webhook.url. Met à jour status/response_code/
    attempts/last_error/delivered_at. Retourne True si success (2xx). Ne lève jamais, ne commit pas.
    Retry policy : attempts += 1 ; si non-2xx/erreur + attempts >= max_attempts → failed, sinon pending."""
    wh = db.query(models.Webhook).filter_by(id=delivery.webhook_id).first()
    if not wh:
        delivery.status = "failed"
        delivery.last_error = "webhook not found (deleted)"
        db.flush()
        return False
    # P24-A : SSRF — valider l'URL avant tout POST (bloque private/loopback/metadata/file + DNS rebinding).
    ok, err, _ips = validate_url(wh.url)
    if not ok:
        delivery.attempts += 1
        delivery.status = "failed"
        delivery.last_error = f"SSRF bloqué : {err}"
        db.flush()
        return False
    body = json.dumps(delivery.payload, ensure_ascii=False).encode()
    secret = _get_secret(wh)  # decrypt secret_enc (lazy-migrate plaintext si besoin)
    sig = _sign(secret, body)
    req = urllib.request.Request(wh.url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-RevPulse-Signature", sig)
    req.add_header("X-RevPulse-Event", delivery.event)
    delivery.attempts += 1
    try:
        with safe_urlopen(req, DELIVERY_TIMEOUT) as r:
            delivery.response_code = r.status
            if 200 <= r.status < 300:
                delivery.status = "success"
                delivery.delivered_at = datetime.utcnow()
                delivery.last_error = None
                db.flush()
                return True
            delivery.last_error = f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        delivery.response_code = e.code
        delivery.last_error = f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"
    except Exception as e:
        delivery.last_error = f"{type(e).__name__}: {str(e)[:200]}"
    # échec : failed si attempts épuisé, sinon pending (retry par deliver_pending)
    if delivery.attempts >= delivery.max_attempts:
        delivery.status = "failed"
    # else reste pending (retry)
    db.flush()
    return False


def deliver_pending(db: Session, limit: int = 50) -> int:
    """Retry les deliveries pending (limit). Retourne le nombre traitées. Synchronous —
    appelé par l'endpoint retry manuel OU par le cron P19 (start_scheduler). Ne commit pas
    (le caller commit — le router pour l'endpoint, le scheduler pour le cron)."""
    pendings = (
        db.query(models.WebhookDelivery)
        .filter_by(status="pending")
        .limit(limit)
        .with_for_update(skip_locked=True)  # P24-A : lock anti double-delivery concurrent (PostgreSQL)
        .all()
    )
    for d in pendings:
        deliver(db, d)
    return len(pendings)


def start_scheduler() -> None:
    """Démarre le scheduler de retry webhooks (background thread daemon, stdlib threading — pas
    de nouvelle dep). Tourne toutes les WEBHOOK_SCHEDULER_INTERVAL_SECONDS (défaut 60s — plus
    fréquent que les séquences car les webhooks sont time-sensitives : un endpoint down au moment
    de l'emit sera retenté dans la minute). Désactivé si interval <= 0. Boucle infinie :
    deliver_pending(db) → commit → sleep(interval). Catch large volontaire (le scheduler ne doit
    jamais crasher l'app). Démarré depuis main._startup."""
    interval = int(os.getenv("WEBHOOK_SCHEDULER_INTERVAL_SECONDS", "60"))
    if interval <= 0:
        log.info("Webhook scheduler disabled (WEBHOOK_SCHEDULER_INTERVAL_SECONDS=%s).", interval)
        return

    def _loop():
        while True:
            db = SessionLocal()
            try:
                n = deliver_pending(db)
                if n:
                    log.info("Webhook scheduler: retried %d pending deliveries.", n)
                db.commit()
            except Exception as e:  # scheduler ne doit jamais crasher (catch large volontaire)
                log.exception("Webhook scheduler error: %s", e)
                db.rollback()
            finally:
                db.close()
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="revpulse-webhook-scheduler")
    t.start()
    log.info("Webhook scheduler started (interval=%ss).", interval)
