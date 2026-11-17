#!/usr/bin/env python3
"""test_inbound_webhooks.py — validation statique des webhooks inbound (P20).

Couvre (par lecture des sources + introspection runtime avec sqlite in-memory) :
  - models.py : InboundWebhook (id ibw, tenant_id FK CASCADE, token unique index, source_name,
    field_map JSONType nullable, active bool default true, created_by FK users nullable,
    created_at/updated_at).
  - schemas.py : InboundWebhookCreate (source_name/field_map/active), InboundWebhookUpdate
    (token non modifiable), InboundWebhookOut (token EXPOSÉ — contrairement au secret HMAC
    outbound), InboundResult (action created/updated, contact_id, matched_by email/linkedin_url/none).
  - services/inbound_webhooks.py : gen_token (32 hex), DEFAULT_FIELD_MAP (identity 6 champs),
    WRITABLE_FIELDS whitelist (anti-injection), _normalize_email (lowercase+strip),
    _apply_field_map (filtre non-writable + valeurs vides), process_inbound (dedup email puis
    linkedin_url, create source=inbound status=new score=0, update non-vides uniquement,
    emit contact.created pour nouveaux → chaîne in→out P18, audit log inbound.contact_*).
  - routers/inbound_webhooks.py : router public POST /api/inbound/webhook/{token} (no auth,
    404/410/422), router_admin CRUD /api/tenants/{tid}/inbound-webhooks (RBAC workspace.manage,
    audit inbound.create/update/delete, field_map validé contre WRITABLE_FIELDS).
  - main.py : router + router_admin enregistrés.
  - migration 0009_p20_inbound_webhooks : table inbound_webhooks + unique index token + JSONB
    field_map + FK CASCADE tenant.
  - CI : test_inbound_webhooks.py dans static checks.
"""
import os
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


print("=== test_inbound_webhooks.py — webhooks inbound (P20) ===\n")

# ─── Source : models.py ────────────────────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")

print("=== models.py : InboundWebhook ===")
ck("class InboundWebhook(Base):" in models_src, True, "classe InboundWebhook définie")
ck('__tablename__ = "inbound_webhooks"' in models_src, True, "table inbound_webhooks")
ck('_uid("ibw")' in models_src, True, "id prefix ibw")
ck('ForeignKey("tenants.id", ondelete="CASCADE")' in models_src, True, "tenant_id FK CASCADE")
ck("unique=True" in models_src, True, "token unique (index unique)")
ck("source_name = Column(String" in models_src, True, "source_name (label système source)")
ck("field_map = Column(JSONType" in models_src, True, "field_map JSONType (dict mappage payload→contact)")
ck("active = Column(Boolean, default=True" in models_src, True, "active bool default true")
ck('ForeignKey("users.id")' in models_src, True, "created_by FK users nullable")

# ─── Source : schemas.py ───────────────────────────────────────────────────────
schemas_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")

print("\n=== schemas.py : Inbound schemas ===")
ck("class InboundWebhookCreate" in schemas_src, True, "InboundWebhookCreate")
ck("class InboundWebhookUpdate" in schemas_src, True, "InboundWebhookUpdate")
ck("class InboundWebhookOut" in schemas_src, True, "InboundWebhookOut (token exposé)")
ck("class InboundResult" in schemas_src, True, "InboundResult (action/contact_id/matched_by)")
ck("token: str" in schemas_src, True, "token exposé en Out (public URL identifier)")
ck("action: str" in schemas_src, True, "InboundResult.action (created/updated)")
ck("matched_by: str" in schemas_src, True, "InboundResult.matched_by (email/linkedin_url/none)")
ck("field_map: dict[str, str] | None = None" in schemas_src, True, "field_map dict optionnel")

# ─── Source : services/inbound_webhooks.py ─────────────────────────────────────
svc = (ROOT / "backend" / "app" / "services" / "inbound_webhooks.py").read_text(encoding="utf-8")

print("\n=== services/inbound_webhooks.py ===")
ck("def gen_token()" in svc, True, "gen_token (token public 32 hex)")
ck("_secrets.token_hex(16)" in svc, True, "token_hex(16) = 32 chars (128 bits)")
ck("DEFAULT_FIELD_MAP" in svc, True, "DEFAULT_FIELD_MAP (identity map 6 champs)")
ck('"first_name": "first_name"' in svc, True, "default map first_name")
ck('"linkedin_url": "linkedin_url"' in svc, True, "default map linkedin_url")
ck("WRITABLE_FIELDS" in svc, True, "WRITABLE_FIELDS whitelist (anti-injection field_map custom)")
ck('"first_name"' in svc and '"tenant_id"' not in svc.split("WRITABLE_FIELDS")[1][:100], True,
   "WRITABLE_FIELDS n'inclut pas tenant_id (anti-injection)")
ck("def _normalize_email" in svc, True, "_normalize_email (lowercase+strip)")
ck(".strip().lower()" in svc, True, "normalize email lowercase+strip")
ck("def _apply_field_map" in svc, True, "_apply_field_map")
ck("if contact_field not in WRITABLE_FIELDS:" in svc, True, "_apply_field_map filtre non-writable")
ck("def process_inbound" in svc, True, "process_inbound (create/update Contact)")
ck('source="inbound"' in svc, True, "source=inbound (distingue des manual/import/linkedin)")
ck('status="new"' in svc, True, "status=new pour les nouveaux")
ck("score=0" in svc, True, "score=0 pour les nouveaux")
ck("models.Contact.email ==" in svc, True, "dedup par email")
ck("models.Contact.linkedin_url ==" in svc, True, "dedup par linkedin_url (fallback)")
ck("emit_event" in svc, True, "emit contact.created (chaîne in→out P18)")
ck('"contact.created"' in svc, True, "event contact.created emit pour nouveaux")
ck("log_audit" in svc, True, "audit log inbound.contact_*")
ck('"inbound.contact_created"' in svc, True, "audit action inbound.contact_created")
ck('"inbound.contact_updated"' in svc, True, "audit action inbound.contact_updated")
ck("setattr(existing" in svc, True, "update via setattr (champs non-vides uniquement)")

# ─── Source : routers/inbound_webhooks.py ──────────────────────────────────────
rtr = (ROOT / "backend" / "app" / "routers" / "inbound_webhooks.py").read_text(encoding="utf-8")

print("\n=== routers/inbound_webhooks.py ===")
ck('prefix="/api/inbound"' in rtr, True, "router public prefix /api/inbound")
ck('prefix="/api/tenants/{tid}/inbound-webhooks"' in rtr, True, "router_admin prefix CRUD")
ck("@router.post(\"/webhook/{token}\"" in rtr, True, "POST /webhook/{token} (endpoint public)")
ck("def receive_inbound" in rtr, True, "handler receive_inbound")
ck("HTTP_404_NOT_FOUND" in rtr, True, "404 si token inconnu")
ck("HTTP_410_GONE" in rtr, True, "410 si inactif (suspendu)")
ck("HTTP_422_UNPROCESSABLE_ENTITY" in rtr, True, "422 si payload vide (aucun champ mappable)")
ck("require_permission(\"workspace.manage\")" in rtr, True, "RBAC workspace.manage sur CRUD admin")
ck("log_audit" in rtr, True, "audit log sur create/update/delete admin")
ck('"inbound.create"' in rtr, True, "audit action inbound.create")
ck('"inbound.delete"' in rtr, True, "audit action inbound.delete")
ck("ibw_service.WRITABLE_FIELDS" in rtr, True, "field_map validé contre WRITABLE_FIELDS (anti-injection)")
# endpoint public n'a PAS de require_permission (no auth)
recv_block = rtr.split("def receive_inbound")[1].split("def ")[0] if "def receive_inbound" in rtr else ""
ck("require_permission" not in recv_block, True, "endpoint public SANS require_permission (token authentifie)")

# ─── Source : main.py ──────────────────────────────────────────────────────────
main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

print("\n=== main.py : routers enregistrés ===")
ck("inbound_webhooks" in main, True, "inbound_webhooks importé")
ck("inbound_webhooks.router," in main, True, "router public enregistré")
ck("inbound_webhooks.router_admin," in main, True, "router_admin enregistré")

# ─── Source : migration 0009 ───────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0009_p20_inbound_webhooks.py").read_text(encoding="utf-8")

print("\n=== migration 0009_p20_inbound_webhooks ===")
ck('revision = "0009_p20_inbound_webhooks"' in mig, True, "revision 0009")
ck('down_revision = "0008_p18_webhooks"' in mig, True, "down_revision 0008 (chaînée)")
ck('op.create_table(' in mig, True, "create_table")
ck('"inbound_webhooks"' in mig, True, "table inbound_webhooks")
ck("JSONB().with_variant" in mig, True, "field_map JSONB PG / Text sqlite (pattern 0008)")
ck("op.create_index" in mig, True, "create_index sur token")
ck("unique=True" in mig, True, "index token unique")
ck("ondelete=\"CASCADE\"" in mig, True, "FK tenant_id CASCADE")

# ─── Introspection runtime (sqlite in-memory) ─────────────────────────────────
print("\n=== Introspection runtime : gen_token + _apply_field_map + process_inbound ===")
try:
    import sqlalchemy  # noqa: F401 — garde-fou
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M
    from app.services import inbound_webhooks as ibw

    # gen_token
    tok = ibw.gen_token()
    ck(len(tok), 32, f"gen_token → 32 chars (got {len(tok)})")
    ck(all(c in "0123456789abcdef" for c in tok), True, "gen_token → hex uniquement")

    # _apply_field_map : default map + filtre non-writable + valeurs vides
    # NB: _apply_field_map ne normalise PAS l'email (la norm lowercase+strip se fait dans
    # process_inbound via _normalize_email) — ici on vérifie le raw stripped.
    ck(ibw._apply_field_map({"first_name": "Alice", "email": "Alice@Example.COM"}, None),
       repr({"first_name": "Alice", "email": "Alice@Example.COM"}), "_apply_field_map default (raw stripped, non normalisé)")
    # field_map custom avec cible non-writable → filtré (anti-injection)
    fm = {"prénom": "first_name", "evil": "tenant_id", "courriel": "email"}
    got = ibw._apply_field_map({"prénom": "Bob", "evil": "hacked", "courriel": "bob@x.com"}, fm)
    ck("tenant_id" not in got, True, "field_map custom filtre tenant_id (anti-injection)")
    ck(got.get("first_name"), "Bob", "field_map custom mappe prénom→first_name")
    # valeurs vides ignorées
    ck(ibw._apply_field_map({"first_name": "", "email": "  "}, None), repr({}), "_apply_field_map ignore valeurs vides")

    # process_inbound : create puis update (dedup email) sur sqlite in-memory
    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    # seed user + tenant (User n'a pas tenant_id/role ; Tenant a owner_id FK users NOT NULL)
    u = M.User(id="usr_test", email="admin@test.com", name="Admin", password_hash="x")
    s.add(u)
    s.flush()
    t = M.Tenant(id="tnt_test", name="Test", plan="Starter", owner_id="usr_test")
    s.add(t)
    s.flush()
    wh = M.InboundWebhook(id="ibw_1", tenant_id="tnt_test", token="tok" + "0" * 29, source_name="Form", active=True, created_by="usr_test")
    s.add(wh)
    s.flush()

    # 1) create : nouveau contact
    r1 = ibw.process_inbound(s, webhook=wh, payload={"first_name": "Alice", "last_name": "Dupont", "email": "Alice@Example.com"})
    ck(r1["action"], "created", "process_inbound create (nouveau contact)")
    ck(r1["matched_by"], "none", "create → matched_by none")
    cid = r1["contact_id"]
    c1 = s.query(M.Contact).filter_by(id=cid).first()
    ck(c1.source, "inbound", "contact source=inbound")
    ck(c1.status, "new", "contact status=new")
    ck(c1.email, "alice@example.com", "email normalisé (lowercase)")
    ck(c1.score, 0, "score=0")

    # 2) update : même email → update (dedup)
    r2 = ibw.process_inbound(s, webhook=wh, payload={"email": "alice@example.com", "title": "CTO", "phone": "+33 6"})
    ck(r2["action"], "updated", "process_inbound update (dedup email)")
    ck(r2["contact_id"], cid, "update → même contact_id (dedup)")
    ck(r2["matched_by"], "email", "update → matched_by email")
    c2 = s.query(M.Contact).filter_by(id=cid).first()
    ck(c2.title, "CTO", "update a mis title=CTO")
    ck(c2.first_name, "Alice", "update n'a pas vidé first_name (non-vides uniquement)")

    # 3) dedup linkedin_url (fallback quand email absent)
    r3 = ibw.process_inbound(s, webhook=wh, payload={"linkedin_url": "https://linkedin.com/in/alice", "title": "CEO"})
    # r3 a un email vide → pas de dedup email → create nouveau (linkedin_url pas encore existant)
    ck(r3["action"], "created", "linkedin_url nouveau → create")
    # Maintenant re-POST avec même linkedin_url → update (dedup linkedin_url)
    r4 = ibw.process_inbound(s, webhook=wh, payload={"linkedin_url": "https://linkedin.com/in/alice", "first_name": "Alice2"})
    ck(r4["action"], "updated", "linkedin_url existant → update (dedup linkedin_url)")
    ck(r4["matched_by"], "linkedin_url", "update → matched_by linkedin_url")

    s.close()
    # Restore SessionLocal (ne pas polluer les autres tests)
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
