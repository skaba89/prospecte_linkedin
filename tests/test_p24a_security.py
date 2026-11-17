#!/usr/bin/env python3
"""test_p24a_security.py — validation statique + runtime de P24-A Sécurisation résiduelle.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - services/url_safety.py : validate_url (SSRF — block private/loopback/metadata/link-local,
    schemes http/https only, DNS resolve + re-check, safe_urlopen redirect re-validation).
  - services/crypto.py : Fernet encrypt/decrypt (encrypt-at-rest des secrets webhook).
  - models.py : Webhook.secret_enc + InboundEvent (UniqueConstraint tenant/webhook/event_id).
  - migration 0013 : ADD COLUMN secret_enc + CREATE TABLE inbound_events.
  - services/webhooks.py : deliver (validate_url + _get_secret + safe_urlopen), deliver_pending
    (with_for_update skip_locked), _get_secret/_store_secret (lazy-migration plaintext→encrypted).
  - services/sequences.py : process_due_enrollments/process_sequence (with_for_update skip_locked),
    start_scheduler (overlap guard _sched_lock).
  - services/inbound_webhooks.py : process_inbound event_id dedup (SAVEPOINT + IntegrityError).
  - routers/webhooks.py : validate_url create/update + rotate endpoint + _store_secret.
  - routers/tags.py : unassign_tag IDOR fix (guard tenant sur contact + tag).
  - routers/crm.py : _engagement_signals exclude dry_run (status == "sent" only).
  - routers/inbound_webhooks.py : receive_inbound header X-Webhook-Id/Webhook-Id.
  - SECURITY.md : table CVE mise à jour (P24-A pyasn1 bloqué par jose, re-planifié P24-B).
  - Runtime : SSRF blocklist, crypto roundtrip, IDOR cross-tenant blocked, idempotence event_id,
    dry_run excluded from score.
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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


print("=== test_p24a_security.py — P24-A Sécurisation résiduelle ===\n")

# ─── Source : services/url_safety.py (SSRF) ───────────────────────────────────
url_src = (ROOT / "backend" / "app" / "services" / "url_safety.py").read_text(encoding="utf-8")
print("=== services/url_safety.py : SSRF protection ===")
ck("def validate_url(url: str)" in url_src, True, "validate_url défini")
ck("def safe_urlopen(" in url_src, True, "safe_urlopen défini (redirect re-validation)")
ck("_BLOCKED_NETWORKS" in url_src, True, "_BLOCKED_NETWORKS blocklist")
ck('ipaddress.ip_network("127.0.0.0/8")' in url_src, True, "block loopback 127/8")
ck('ipaddress.ip_network("10.0.0.0/8")' in url_src, True, "block private 10/8")
ck('ipaddress.ip_network("172.16.0.0/12")' in url_src, True, "block private 172.16/12")
ck('ipaddress.ip_network("192.168.0.0/16")' in url_src, True, "block private 192.168/16")
ck('ipaddress.ip_network("169.254.0.0/16")' in url_src, True, "block link-local + cloud metadata 169.254/16")
ck('ipaddress.ip_network("fc00::/7")' in url_src, True, "block ULA IPv6 fc00::/7")
ck("class _SSRFRedirectHandler" in url_src, True, "_SSRFRedirectHandler (redirect re-validation)")
ck("socket.getaddrinfo" in url_src, True, "DNS resolve (getaddrinfo) — DNS rebinding protection")

# ─── Source : services/crypto.py (Fernet) ─────────────────────────────────────
crypto_src = (ROOT / "backend" / "app" / "services" / "crypto.py").read_text(encoding="utf-8")
print("\n=== services/crypto.py : Fernet encrypt-at-rest ===")
ck("from cryptography.fernet import Fernet" in crypto_src, True, "import Fernet")
ck("def encrypt(plaintext: str)" in crypto_src, True, "encrypt() défini")
ck("def decrypt(ciphertext: str)" in crypto_src, True, "decrypt() défini")
ck("WEBHOOK_SECRET_KEY" in crypto_src, True, "clé depuis env WEBHOOK_SECRET_KEY")
ck("def new_secret()" in crypto_src, True, "new_secret() (rotation)")

# ─── Source : models.py (secret_enc + InboundEvent) ───────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("\n=== models.py : Webhook.secret_enc + InboundEvent ===")
ck("secret_enc = Column(Text, nullable=True)" in models_src, True, "Webhook.secret_enc (encrypted)")
ck("class InboundEvent(Base):" in models_src, True, "InboundEvent model")
ck('UniqueConstraint("tenant_id", "webhook_id", "event_id"' in models_src, True, "InboundEvent UniqueConstraint")
ck('__tablename__ = "inbound_events"' in models_src, True, "table inbound_events")

# ─── Source : migration 0013 ──────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0013_p24a_security.py").read_text(encoding="utf-8")
print("\n=== migration 0013_p24a_security ===")
ck('revision = "0013_p24a_security"' in mig, True, "revision 0013_p24a_security")
ck('down_revision = "0012_p24_analytics"' in mig, True, "down_revision 0012 (chaîne)")
ck('op.add_column("webhooks"' in mig, True, "ALTER TABLE webhooks ADD COLUMN secret_enc")
ck('sa.Column("secret_enc", sa.Text()' in mig, True, "secret_enc Text nullable")
ck('op.create_table' in mig and '"inbound_events"' in mig, True, "CREATE TABLE inbound_events")
ck("uq_inbound_event" in mig, True, "UniqueConstraint uq_inbound_event")

# ─── Source : services/webhooks.py (SSRF + secret + lock) ─────────────────────
wh_src = (ROOT / "backend" / "app" / "services" / "webhooks.py").read_text(encoding="utf-8")
print("\n=== services/webhooks.py : SSRF + _get_secret + safe_urlopen + lock ===")
ck("from . import crypto" in wh_src, True, "import crypto")
ck("from .url_safety import safe_urlopen, validate_url" in wh_src, True, "import validate_url + safe_urlopen")
ck("def _get_secret(wh" in wh_src, True, "_get_secret (decrypt + lazy-migrate)")
ck("def _store_secret(wh" in wh_src, True, "_store_secret (encrypt + clear plaintext)")
ck("validate_url(wh.url)" in wh_src, True, "deliver : validate_url avant POST")
ck("SSRF bloqué" in wh_src, True, "deliver : status=failed + last_error SSRF si ko")
ck("safe_urlopen(req, DELIVERY_TIMEOUT)" in wh_src, True, "deliver : safe_urlopen (redirect re-validated)")
ck("with_for_update(skip_locked=True)" in wh_src, True, "deliver_pending : with_for_update skip_locked")

# ─── Source : services/sequences.py (lock + overlap guard) ────────────────────
seq_src = (ROOT / "backend" / "app" / "services" / "sequences.py").read_text(encoding="utf-8")
print("\n=== services/sequences.py : with_for_update + overlap guard ===")
ck(seq_src.count("with_for_update(skip_locked=True)") >= 2, True, "2x with_for_update (process_due + process_sequence)")
ck("_sched_lock = threading.Lock()" in seq_src, True, "_sched_lock (overlap guard)")
ck("_sched_lock.acquire(blocking=False)" in seq_src, True, "overlap guard : acquire non-bloquant")

# ─── Source : services/inbound_webhooks.py (idempotence) ──────────────────────
ibw_src = (ROOT / "backend" / "app" / "services" / "inbound_webhooks.py").read_text(encoding="utf-8")
print("\n=== services/inbound_webhooks.py : event_id dedup ===")
ck("event_id: str | None = None" in ibw_src, True, "process_inbound : param event_id")
ck("db.begin_nested()" in ibw_src, True, "SAVEPOINT pour dedup isolé")
ck("from sqlalchemy.exc import IntegrityError" in ibw_src, True, "import IntegrityError")
ck('"action": "duplicate"' in ibw_src, True, "retourne action=duplicate si doublon")
ck("models.InboundEvent(" in ibw_src, True, "insert InboundEvent")

# ─── Source : routers/webhooks.py (validate_url + rotate) ─────────────────────
rwh_src = (ROOT / "backend" / "app" / "routers" / "webhooks.py").read_text(encoding="utf-8")
print("\n=== routers/webhooks.py : validate_url + rotate ===")
ck("from ..services.url_safety import validate_url" in rwh_src, True, "import validate_url")
ck("URL refusée (SSRF)" in rwh_src, True, "create : 422 si URL SSRF")
ck("wh_service._store_secret(wh" in rwh_src, True, "create : _store_secret (encrypt-at-rest)")
ck("def rotate_secret(" in rwh_src, True, "rotate_secret endpoint")
ck('@router.post("/{wid}/rotate"' in rwh_src, True, "POST /{wid}/rotate")

# ─── Source : routers/tags.py (IDOR fix) ──────────────────────────────────────
tags_src = (ROOT / "backend" / "app" / "routers" / "tags.py").read_text(encoding="utf-8")
print("\n=== routers/tags.py : IDOR fix unassign_tag ===")
ck("Contact introuvable dans ce tenant" in tags_src, True, "unassign_tag : guard tenant sur contact")
ck("Tag introuvable dans ce tenant" in tags_src, True, "unassign_tag : guard tenant sur tag")
ck("models.Contact).filter_by(id=cid, tenant_id=tid)" in tags_src, True, "unassign_tag : filtre tenant_id")

# ─── Source : routers/crm.py (dry_run exclusion) ──────────────────────────────
crm_src = (ROOT / "backend" / "app" / "routers" / "crm.py").read_text(encoding="utf-8")
print("\n=== routers/crm.py : _engagement_signals exclude dry_run ===")
ck('.filter(models.Notification.status == "sent")' in crm_src, True, "_engagement_signals : status == sent (dry_run exclu)")
ck('["sent", "dry_run"]' not in crm_src, True, "plus de in_([sent, dry_run]) dans crm.py")
ck("dry_run exclu" in crm_src, True, "docstring mentionne dry_run exclu")

# ─── Source : routers/inbound_webhooks.py (header event_id) ───────────────────
ribw_src = (ROOT / "backend" / "app" / "routers" / "inbound_webhooks.py").read_text(encoding="utf-8")
print("\n=== routers/inbound_webhooks.py : header event_id ===")
ck("from fastapi import APIRouter, Depends, Header" in ribw_src, True, "import Header")
ck('Header(None, alias="X-Webhook-Id")' in ribw_src, True, "header X-Webhook-Id")
ck('Header(None, alias="Webhook-Id")' in ribw_src, True, "header Webhook-Id")
ck('payload.get("event_id")' in ribw_src, True, "fallback payload event_id")
ck("process_inbound(db, webhook=wh, payload=payload, event_id=event_id)" in ribw_src, True, "passe event_id à process_inbound")

# ─── Source : SECURITY.md (CVE table P24-A) ───────────────────────────────────
sec_md = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
print("\n=== SECURITY.md : table CVE P24-A ===")
ck("bloquée" in sec_md and "contrainte" in sec_md, True, "SECURITY.md : pyasn1 upgrade bloqué par jose documenté")
ck("requires pyasn1<0.5.0" in sec_md, True, "SECURITY.md : contrainte jose documentée")
ck("migration jose→PyJWT" in sec_md, True, "SECURITY.md : migration jose→PyJWT planifiée")
ck("| P24-B |" in sec_md, True, "SECURITY.md : échéance re-planifiée P24-B (jose/pyasn1/ecdsa)")
ck("| P25+ |" in sec_md, True, "SECURITY.md : échéance starlette P25+")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : SSRF + crypto + IDOR + idempotence + dry_run ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))

    # ── SSRF : validate_url blocklist ──
    from app.services.url_safety import validate_url
    print("--- SSRF : validate_url blocklist ---")
    ok, _, _ = validate_url("http://127.0.0.1/x")
    ck(ok, False, "bloque 127.0.0.1 (loopback)")
    ok, _, _ = validate_url("http://10.0.0.5/x")
    ck(ok, False, "bloque 10.0.0.5 (private 10/8)")
    ok, _, _ = validate_url("http://192.168.1.1/x")
    ck(ok, False, "bloque 192.168.1.1 (private 192.168/16)")
    ok, _, _ = validate_url("http://169.254.169.254/latest/meta-data/")
    ck(ok, False, "bloque 169.254.169.254 (cloud metadata)")
    ok, _, _ = validate_url("http://172.16.5.5/x")
    ck(ok, False, "bloque 172.16.5.5 (private 172.16/12)")
    ok, _, _ = validate_url("file:///etc/passwd")
    ck(ok, False, "bloque file:// scheme")
    ok, _, _ = validate_url("ftp://example.com/x")
    ck(ok, False, "bloque ftp:// scheme")
    ok, _, _ = validate_url("http://localhost/x")
    ck(ok, False, "bloque localhost (résout vers 127.0.0.1)")
    ok, _, ips = validate_url("http://example.com/webhook")
    ck(ok, True, "autorise example.com (public, résolution DNS OK)")

    # ── crypto : encrypt/decrypt roundtrip ──
    from app.services import crypto
    print("--- crypto : Fernet roundtrip ---")
    ct = crypto.encrypt("my_secret_hmac_key")
    ck(ct != "my_secret_hmac_key", True, "encrypt ≠ plaintext")
    ck(ct.startswith("gAAAAA"), True, "ciphertext Fernet (commence par gAAAAA)")
    ck(crypto.decrypt(ct), "my_secret_hmac_key", "decrypt(encrypt(x)) == x (roundtrip)")
    new = crypto.new_secret()
    ck(len(new), 32, "new_secret = 32 hex chars (128 bits)")

    # ── Setup DB pour IDOR + idempotence + dry_run ──
    from app import database as db_mod
    from app import models as M
    from app.routers import crm as crm_r
    from app.routers import tags as tags_r
    from app.services import inbound_webhooks as ibw_svc
    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_a", email="a@test.com", name="Alice", password_hash="x"))
    s.add(M.User(id="usr_b", email="b@test.com", name="Bob", password_hash="x"))
    s.add(M.Tenant(id="tnt_a", name="A", plan="Starter", owner_id="usr_a"))
    s.add(M.Tenant(id="tnt_b", name="B", plan="Starter", owner_id="usr_b"))
    s.flush()
    s.add(M.Member(id="mem_a", tenant_id="tnt_a", user_id="usr_a", email="a@test.com", name="Alice", role="owner", status="active"))
    s.add(M.Member(id="mem_b", tenant_id="tnt_b", user_id="usr_b", email="b@test.com", name="Bob", role="owner", status="active"))
    c_b = M.Contact(tenant_id="tnt_b", first_name="Victim", last_name="X", email="victim@b.com", status="new", source="manual")
    s.add(c_b); s.flush()
    tag_b = M.Tag(tenant_id="tnt_b", name="vip", color="#f00")
    s.add(tag_b); s.flush()
    s.add(M.ContactTag(contact_id=c_b.id, tag_id=tag_b.id))
    s.commit()
    mem_a = SimpleNamespace(user_id="usr_a", name="Alice", role="owner", tenant_id="tnt_a", status="active")

    # ── IDOR : unassign_tag cross-tenant blocked ──
    print("--- IDOR : unassign_tag cross-tenant ---")
    from fastapi import HTTPException
    try:
        tags_r.unassign_tag("tnt_a", c_b.id, tag_b.id, db=s, _t=None, membership=mem_a)
        ck(False, True, "IDOR : unassign cross-tenant DEVRAIT lever 404 (a réussi = VULN)")
    except HTTPException as e:
        ck(e.status_code, 404, "IDOR : unassign cross-tenant → 404 (guard tenant actif)")
    still = s.query(M.ContactTag).filter_by(contact_id=c_b.id, tag_id=tag_b.id).first()
    ck(still is not None, True, "IDOR : tag toujours assigné au contact de B (pas de delete cross-tenant)")

    # ── Idempotence : process_inbound event_id dedup ──
    print("--- idempotence : process_inbound event_id ---")
    s.add(M.InboundWebhook(id="ibw1", tenant_id="tnt_a", token="tok_inbound_1", source_name="form", active=True))
    s.commit()
    wh_in = s.query(M.InboundWebhook).filter_by(token="tok_inbound_1").first()
    payload = {"first_name": "Dup", "last_name": "Test", "email": "dup@test.com"}
    r1 = ibw_svc.process_inbound(s, webhook=wh_in, payload=payload, event_id="evt-123")
    s.commit()
    ck(r1["action"], "created", "1er appel event_id=evt-123 → created")
    n_before = s.query(M.Contact).filter_by(tenant_id="tnt_a").count()
    r2 = ibw_svc.process_inbound(s, webhook=wh_in, payload=payload, event_id="evt-123")
    s.commit()
    ck(r2["action"], "duplicate", "2e appel event_id=evt-123 → duplicate (idempotent)")
    ck(r2["contact_id"], None, "duplicate → contact_id=None")
    ck(r2["matched_by"], "event_id", "duplicate → matched_by=event_id")
    n_after = s.query(M.Contact).filter_by(tenant_id="tnt_a").count()
    ck(n_after, n_before, "idempotence : aucun contact dupliqué (count inchangé)")
    r3 = ibw_svc.process_inbound(s, webhook=wh_in, payload=payload, event_id="evt-456")
    s.commit()
    ck(r3["action"], "updated", "event_id différent + même email → updated (dedup email, pas duplicate)")

    # ── dry_run exclusion du scoring ──
    print("--- dry_run : exclusion du scoring ---")
    c_score = M.Contact(tenant_id="tnt_a", first_name="Score", last_name="Test", email="score@test.com", status="new", source="manual")
    s.add(c_score); s.commit()
    base_score, _ = crm_r._score_contact(s, c_score)
    s.commit()
    s.add(M.Notification(
        tenant_id="tnt_a", recipient_contact_id=c_score.id, type="email", status="dry_run",
        template_id="tpl1", subject="x", body_text="x", created_at=datetime.utcnow()))
    s.commit()
    dry_score, _ = crm_r._score_contact(s, c_score)
    s.commit()
    ck(dry_score, base_score, "dry_run n'augmente PAS le score (exclu de l'engagement)")
    s.add(M.Notification(
        tenant_id="tnt_a", recipient_contact_id=c_score.id, type="email", status="sent",
        template_id="tpl2", subject="x", body_text="x", created_at=datetime.utcnow()))
    s.commit()
    sent_score, _ = crm_r._score_contact(s, c_score)
    s.commit()
    ck(sent_score > base_score, True, f"sent augmente le score ({sent_score} > {base_score})")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
