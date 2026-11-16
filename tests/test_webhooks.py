#!/usr/bin/env python3
"""test_webhooks.py — validation statique des webhooks outbound (P18).

Couvre (par lecture des sources + introspection, pas de serveur) :
  - models.py : Webhook (tenant_id, url, secret, events JSONType, active, created_by) +
    WebhookDelivery (webhook_id FK CASCADE, event, payload, status pending|success|failed,
    response_code, attempts, max_attempts, last_error, delivered_at).
  - schemas.py : WebhookCreate/Update/Out/DeliveryOut/TestResult (secret jamais exposé en Out).
  - services/webhooks.py : SUPPORTED_EVENTS whitelist (6 events), gen_secret (token_hex 32),
    _sign HMAC-SHA256, emit_event (filtre active + events, best-effort never raises, flush +
    deliver sync), deliver (urllib POST, headers X-RevPulse-Signature + X-RevPulse-Event,
    timeout 5s, retry attempts/max_attempts, pending→failed), deliver_pending (retry batch).
  - routers/webhooks.py : 8 routes (CRUD + test + deliveries + retry), RBAC workspace.manage
    sur toutes, audit log (webhook.create/update/delete/retry), _validate_events anti-injection.
  - main.py : webhooks.router enregistré dans le tuple.
  - Hooks emit_event : crm.py (contact.created, contact.scored, opportunity.created),
    sequences.py router (sequence.enrolled), services/sequences.py (sequence.step_completed x2
    process_due + process_sequence), notifications.py (email.sent conditionnel sur ok).
  - migration 0008_p18_webhooks : tables webhooks + webhook_deliveries + CASCADE + index status.
  - CI : test_webhooks.py dans static checks.
"""
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


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── models.py : Webhook + WebhookDelivery ─────────────────────────────────────
print("=== models.py : Webhook + WebhookDelivery ===")
models = read("backend/app/models.py")
ck("class Webhook(Base):" in models, True, "class Webhook")
ck('__tablename__ = "webhooks"' in models, True, "table webhooks")
ck("tenant_id = Column(String, ForeignKey(\"tenants.id\"), nullable=False, index=True)" in models, True, "Webhook.tenant_id FK + index")
ck("url = Column(String, nullable=False)" in models, True, "Webhook.url")
ck("secret = Column(String, nullable=False)" in models, True, "Webhook.secret (HMAC key)")
ck("events = Column(JSONType, nullable=False)" in models, True, "Webhook.events JSONType (portable JSONB/Text)")
ck("active = Column(Boolean, default=True, nullable=False)" in models, True, "Webhook.active (suspend toggle)")
ck("created_by = Column(String, ForeignKey(\"users.id\"), nullable=True)" in models, True, "Webhook.created_by FK users")

ck("class WebhookDelivery(Base):" in models, True, "class WebhookDelivery")
ck('__tablename__ = "webhook_deliveries"' in models, True, "table webhook_deliveries")
ck("webhook_id = Column(String, ForeignKey(\"webhooks.id\", ondelete=\"CASCADE\")" in models, True, "WebhookDelivery.webhook_id FK CASCADE")
ck("payload = Column(JSONType, nullable=False)" in models, True, "WebhookDelivery.payload JSONType")
ck("status = Column(String, default=\"pending\", nullable=False, index=True)" in models, True, "WebhookDelivery.status pending|success|failed + index")
ck("response_code = Column(Integer, nullable=True)" in models, True, "WebhookDelivery.response_code")
ck("attempts = Column(Integer, default=0, nullable=False)" in models, True, "WebhookDelivery.attempts")
ck("max_attempts = Column(Integer, default=3, nullable=False)" in models, True, "WebhookDelivery.max_attempts (retry cap)")
ck("last_error = Column(Text, nullable=True)" in models, True, "WebhookDelivery.last_error")
ck("delivered_at = Column(DateTime, nullable=True)" in models, True, "WebhookDelivery.delivered_at")

# ─── schemas.py : Webhook* schemas ─────────────────────────────────────────────
print("\n=== schemas.py : Webhook* schemas ===")
schemas = read("backend/app/schemas.py")
ck("class WebhookCreate(BaseModel):" in schemas, True, "WebhookCreate")
ck("class WebhookUpdate(BaseModel):" in schemas, True, "WebhookUpdate")
ck("class WebhookOut(BaseModel):" in schemas, True, "WebhookOut")
ck("class WebhookDeliveryOut(BaseModel):" in schemas, True, "WebhookDeliveryOut")
ck("class WebhookTestResult(BaseModel):" in schemas, True, "WebhookTestResult")
ck("secret: str | None = None" in schemas, True, "WebhookCreate.secret optional (auto-généré)")
ck("secret jamais exposé" in schemas, True, "secret masqué en WebhookOut (docstring)")

# ─── services/webhooks.py : emit + deliver + HMAC ──────────────────────────────
print("\n=== services/webhooks.py : emit + deliver + HMAC ===")
svc = read("backend/app/services/webhooks.py")
ck('"contact.created"' in svc and '"contact.scored"' in svc, True, "SUPPORTED_EVENTS: contact.created + contact.scored")
ck('"sequence.enrolled"' in svc and '"sequence.step_completed"' in svc, True, "SUPPORTED_EVENTS: sequence.enrolled + step_completed")
ck('"email.sent"' in svc and '"opportunity.created"' in svc, True, "SUPPORTED_EVENTS: email.sent + opportunity.created")
ck("def gen_secret()" in svc, True, "gen_secret (auto-gen key)")
ck("_secrets.token_hex(16)" in svc, True, "gen_secret 32 hex chars (128 bits)")
ck("def _sign(secret: str, body: bytes) -> str:" in svc, True, "_sign function")
ck("hmac.new(secret.encode(), body, hashlib.sha256)" in svc, True, "_sign HMAC-SHA256")
ck("def emit_event(db: Session, *, tenant_id: str, event: str, payload: dict) -> int:" in svc, True, "emit_event signature")
ck("models.Webhook.active.is_(True)" in svc, True, "emit_event filtre active=True")
ck("if event not in events:" in svc, True, "emit_event filtre par events souscrits")
ck("if event not in SUPPORTED_EVENTS:" in svc, True, "emit_event whitelist anti-injection")
ck("best-effort" in svc.lower(), True, "emit_event best-effort (ne lève jamais)")
ck("def deliver(db: Session, delivery: models.WebhookDelivery) -> bool:" in svc, True, "deliver signature")
ck("X-RevPulse-Signature" in svc, True, "deliver header X-RevPulse-Signature (HMAC)")
ck("X-RevPulse-Event" in svc, True, "deliver header X-RevPulse-Event")
ck("urllib.request.urlopen(req, timeout=DELIVERY_TIMEOUT)" in svc, True, "deliver urllib POST (stdlib, pas de dep requests)")
ck("delivery.attempts += 1" in svc, True, "deliver incrémente attempts")
ck("delivery.attempts >= delivery.max_attempts" in svc, True, "deliver retry policy (failed si épuisé)")
ck("def deliver_pending(db: Session, limit: int = 50) -> int:" in svc, True, "deliver_pending (retry batch)")

# ─── routers/webhooks.py : 8 routes + RBAC + audit ─────────────────────────────
print("\n=== routers/webhooks.py : 8 routes + RBAC + audit ===")
rtr = read("backend/app/routers/webhooks.py")
ck('prefix="/api/tenants/{tid}/webhooks"' in rtr, True, "router prefix /api/tenants/{tid}/webhooks")
ck('tags=["webhooks"]' in rtr, True, "router tag webhooks")
ck('@router.post("", response_model=schemas.WebhookOut, status_code=status.HTTP_201_CREATED)' in rtr, True, "POST / (create)")
ck('@router.get("", response_model=list[schemas.WebhookOut])' in rtr, True, "GET / (list)")
ck('@router.get("/{wid}", response_model=schemas.WebhookOut)' in rtr, True, "GET /{wid} (detail)")
ck('@router.patch("/{wid}", response_model=schemas.WebhookOut)' in rtr, True, "PATCH /{wid} (update)")
ck('@router.delete("/{wid}", status_code=status.HTTP_200_OK)' in rtr, True, "DELETE /{wid}")
ck('@router.post("/{wid}/test", response_model=schemas.WebhookTestResult)' in rtr, True, "POST /{wid}/test (ping)")
ck('@router.get("/{wid}/deliveries", response_model=list[schemas.WebhookDeliveryOut])' in rtr, True, "GET /{wid}/deliveries")
ck('@router.post("/{wid}/deliveries/{did}/retry", response_model=schemas.WebhookDeliveryOut)' in rtr, True, "POST /{wid}/deliveries/{did}/retry")
ck(rtr.count('require_permission("workspace.manage")'), 8, "RBAC workspace.manage sur les 8 routes (admin-only)")
ck('action="webhook.create"' in rtr, True, "audit webhook.create")
ck('action="webhook.update"' in rtr, True, "audit webhook.update")
ck('action="webhook.delete"' in rtr, True, "audit webhook.delete")
ck('action="webhook.retry"' in rtr, True, "audit webhook.retry")
ck("def _validate_events" not in rtr, True, "_validate_events importé du service (pas redéfini)")
ck("wh_service._validate_events" in rtr, True, "_validate_events appelé (anti-injection events)")
ck("d.attempts = 0" in rtr, True, "retry reset attempts à 0")

# ─── main.py : router enregistré ───────────────────────────────────────────────
print("\n=== main.py : router enregistré ===")
main = read("backend/app/main.py")
ck("    webhooks," in main, True, "import webhooks dans le tuple routers")
ck("webhooks.router" in main, True, "webhooks.router inclus dans app.include_router")

# ─── Hooks emit_event dans les routers/services ────────────────────────────────
print("\n=== Hooks emit_event (crm + sequences + notifications) ===")
crm = read("backend/app/routers/crm.py")
ck("from ..services.webhooks import emit_event" in crm, True, "crm.py import emit_event")
ck('event="contact.created"' in crm, True, "crm.py hook contact.created (create_contact)")
ck('event="contact.scored"' in crm, True, "crm.py hook contact.scored (recompute_score)")
ck('event="opportunity.created"' in crm, True, "crm.py hook opportunity.created (create_opportunity)")

seqr = read("backend/app/routers/sequences.py")
ck("from ..services.webhooks import emit_event" in seqr, True, "sequences.py router import emit_event")
ck('event="sequence.enrolled"' in seqr, True, "sequences.py router hook sequence.enrolled (enroll_contact)")

seqs = read("backend/app/services/sequences.py")
ck("from .webhooks import emit_event" in seqs, True, "services/sequences.py import emit_event")
ck(seqs.count('event="sequence.step_completed"'), 2, "services/sequences.py hook sequence.step_completed x2 (process_due + process_sequence)")

notif = read("backend/app/routers/notifications.py")
ck("from ..services.webhooks import emit_event" in notif, True, "notifications.py import emit_event")
ck('event="email.sent"' in notif, True, "notifications.py hook email.sent (send_email_to_contact)")
ck("if ok:  # email réellement envoyé" in notif, True, "email.sent conditionnel sur ok (pas sur skip/failed)")

# ─── migration 0008_p18_webhooks ───────────────────────────────────────────────
print("\n=== migration 0008_p18_webhooks ===")
mig = read("backend/alembic/versions/0008_p18_webhooks.py")
ck('revision = "0008_p18_webhooks"' in mig, True, "revision 0008_p18_webhooks")
ck('down_revision = "0007_p15_sequences"' in mig, True, "down_revision 0007_p15_sequences (chaînée)")
ck('op.create_table(\n        "webhooks"' in mig, True, "create_table webhooks")
ck('op.create_table(\n        "webhook_deliveries"' in mig, True, "create_table webhook_deliveries")
ck("ondelete=\"CASCADE\"" in mig, True, "CASCADE sur webhook_deliveries.webhook_id")
ck('server_default="pending"' in mig, True, "status default pending")
ck("def downgrade() -> None:" in mig, True, "downgrade (drop tables)")

# ─── Introspection runtime : routes enregistrées ───────────────────────────────
print("\n=== Introspection runtime : routes webhooks enregistrées ===")
try:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.routers import webhooks as wh_mod

    paths = {(r.path, ",".join(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in wh_mod.router.routes}
    expected = {
        ("/api/tenants/{tid}/webhooks", "GET"),
        ("/api/tenants/{tid}/webhooks", "POST"),
        ("/api/tenants/{tid}/webhooks/{wid}", "GET"),
        ("/api/tenants/{tid}/webhooks/{wid}", "DELETE"),
        ("/api/tenants/{tid}/webhooks/{wid}", "PATCH"),
        ("/api/tenants/{tid}/webhooks/{wid}/test", "POST"),
        ("/api/tenants/{tid}/webhooks/{wid}/deliveries", "GET"),
        ("/api/tenants/{tid}/webhooks/{wid}/deliveries/{did}/retry", "POST"),
    }
    for p, m in expected:
        ck((p, m) in paths, True, f"route {p} [{m}]")
    ck(len(paths), 8, "exactement 8 routes webhook (pas de doublon)")

    # SUPPORTED_EVENTS au runtime
    from app.services import webhooks as wh_svc

    ck(len(wh_svc.SUPPORTED_EVENTS), 6, "6 événements supportés dans SUPPORTED_EVENTS")
    ck("webhook.test" not in wh_svc.SUPPORTED_EVENTS, True, "webhook.test hors whitelist (event interne test ping)")
    ck(wh_svc._sign("secret", b'{"a":1}'), hmac_check := __import__("hmac").new(
        b"secret", b'{"a":1}', __import__("hashlib").sha256).hexdigest(), "signature HMAC déterministe")
except Exception as e:
    ck(False, True, f"introspection runtime (erreur: {type(e).__name__}: {e})")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
