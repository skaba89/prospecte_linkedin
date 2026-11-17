#!/usr/bin/env python3
"""test_tags.py — validation statique + runtime de P22 Tags & catégorisation de contacts.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : Tag (id/tenant_id/name/color/created_at + UniqueConstraint tenant_id,name) +
    ContactTag (PK composite contact_id+tag_id, CASCADE sur les 2 FK).
  - schemas.py : TagCreate/TagUpdate/TagOut (avec contact_count)/ContactTagAssign + ContactOut.tags.
  - routers/tags.py : 7 endpoints (CRUD tags + list/assign/unassign sur contacts), RBAC crm.manage
    sur les mutations, audit.log_audit (tag.create/update/delete + contact.tag_assign/unassign),
    idempotent assign (skip doublons via PK composite), validation tenant (tag_ids hors tenant → 422),
    409 sur doublon de nom, 404 sur tag/contact introuvable.
  - routers/crm.py : filtre tag_id dans _apply_contact_filters (EXISTS ContactTag) + param tag_id
    dans list_contacts + helper _attach_tags (batch query) appelé dans list_contacts + get_contact.
  - main.py : tags.router enregistré.
  - migration 0010 : tables tags + contact_tags (CASCADE, UniqueConstraint, PK composite).
  - frontend : tags.html (CRUD + color) + crm.html (filtre f-tag + colonne Tags badges + loadTagsFilter).
  - CI : test_tags.py dans static checks.
  - Runtime : create tag → assign → idempotent → _attach_tags peuple c.tags → filtre tag_id isole
    le bon contact → tenant isolation (tag tenant B rejeté sur contact tenant A → 422).
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


print("=== test_tags.py — Tags & catégorisation de contacts (P22) ===\n")

# ─── Source : models.py ────────────────────────────────────────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")

print("=== models.py : Tag + ContactTag ===")
ck("class Tag(Base):" in models_src, True, "Tag model définie")
ck('__tablename__ = "tags"' in models_src, True, "table tags")
ck("class ContactTag(Base):" in models_src, True, "ContactTag association définie")
ck('__tablename__ = "contact_tags"' in models_src, True, "table contact_tags")
ck('default=lambda: _uid("tag")' in models_src, True, "Tag.id prefix 'tag'")
ck("UniqueConstraint(\"tenant_id\", \"name\"" in models_src, True, "UniqueConstraint tenant_id+name (pas de doublon)")
ck("ForeignKey(\"contacts.id\", ondelete=\"CASCADE\")" in models_src, True, "ContactTag.contact_id CASCADE")
ck("ForeignKey(\"tags.id\", ondelete=\"CASCADE\")" in models_src, True, "ContactTag.tag_id CASCADE")
ck("PrimaryKeyConstraint" in models_src or 'primary_key=True' in models_src, True, "ContactTag PK composite (contact_id+tag_id)")
ck('color = Column(String, default="#635bff"' in models_src, True, "Tag.color default #635bff")

# ─── Source : schemas.py ───────────────────────────────────────────────────────
schemas_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")

print("\n=== schemas.py : Tag* + ContactOut.tags ===")
ck("class TagCreate" in schemas_src, True, "TagCreate")
ck("class TagUpdate" in schemas_src, True, "TagUpdate")
ck("class TagOut" in schemas_src, True, "TagOut")
ck("class ContactTagAssign" in schemas_src, True, "ContactTagAssign")
ck("tag_ids: list[str]" in schemas_src, True, "ContactTagAssign.tag_ids list[str]")
ck("contact_count: int = 0" in schemas_src, True, "TagOut.contact_count")
ck("tags: list[TagOut] = []" in schemas_src, True, "ContactOut.tags field (peuplé par _attach_tags)")

# ─── Source : routers/tags.py ──────────────────────────────────────────────────
tags_rtr = (ROOT / "backend" / "app" / "routers" / "tags.py").read_text(encoding="utf-8")

print("\n=== routers/tags.py : endpoints + RBAC + audit ===")
ck('prefix="/api/tenants/{tid}/tags"' in tags_rtr, True, "prefix /tags")
ck("def list_tags" in tags_rtr, True, "GET / (list tags + contact_count)")
ck("def create_tag" in tags_rtr, True, "POST / (create tag)")
ck("def update_tag" in tags_rtr, True, "PATCH /{tag_id} (update)")
ck("def delete_tag" in tags_rtr, True, "DELETE /{tag_id} (delete + cascade)")
ck("def list_contact_tags" in tags_rtr, True, "GET /contacts/{cid} (tags d'un contact)")
ck("def assign_tags" in tags_rtr, True, "POST /contacts/{cid} (assign idempotent)")
ck("def unassign_tag" in tags_rtr, True, "DELETE /contacts/{cid}/{tag_id} (unassign)")
ck(tags_rtr.count("@router."), 7, "7 endpoints enregistrés")
ck('require_permission("crm.manage")' in tags_rtr, True, "RBAC crm.manage sur mutations")
ck(tags_rtr.count('require_permission("crm.manage")'), 5, "5 mutations avec RBAC crm.manage (create/update/delete/assign/unassign)")
ck('action="tag.create"' in tags_rtr, True, "audit tag.create")
ck('action="tag.update"' in tags_rtr, True, "audit tag.update")
ck('action="tag.delete"' in tags_rtr, True, "audit tag.delete")
ck('action="contact.tag_assign"' in tags_rtr, True, "audit contact.tag_assign")
ck('action="contact.tag_unassign"' in tags_rtr, True, "audit contact.tag_unassign")
ck("409" in tags_rtr, True, "409 sur doublon de nom (UniqueConstraint)")
ck("422" in tags_rtr, True, "422 sur tag_ids hors tenant (validation)")
ck("404" in tags_rtr, True, "404 sur tag/contact introuvable")
ck("existing = {ct.tag_id" in tags_rtr, True, "idempotent assign (skip doublons via existing set)")
ck("func.count(models.ContactTag.contact_id)" in tags_rtr, True, "contact_count via func.count (group_by)")

# ─── Source : routers/crm.py (filtre tag_id + _attach_tags) ────────────────────
crm_rtr = (ROOT / "backend" / "app" / "routers" / "crm.py").read_text(encoding="utf-8")

print("\n=== routers/crm.py : filtre tag_id + _attach_tags ===")
ck("tag_id=None" in crm_rtr, True, "_apply_contact_filters accepte tag_id")
ck("tag_id: str | None = Query" in crm_rtr, True, "list_contacts param tag_id (Query)")
ck("tag_id=tag_id" in crm_rtr, True, "list_contacts passe tag_id à _apply_contact_filters")
ck("models.ContactTag.contact_id == models.Contact.id" in crm_rtr, True, "filtre EXISTS ContactTag")
ck("def _attach_tags" in crm_rtr, True, "helper _attach_tags défini")
ck("_attach_tags(db," in crm_rtr, True, "_attach_tags appelé (list + get)")
ck("c.tags = by_contact.get" in crm_rtr, True, "_attach_tags setattr c.tags (pour from_attributes)")

# ─── Source : main.py (tags router enregistré) ────────────────────────────────
main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : tags router enregistré ===")
ck("tags," in main, True, "import tags")
ck("tags.router" in main, True, "tags.router enregistré dans le tuple")

# ─── Migration 0010 ────────────────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0010_p22_tags.py").read_text(encoding="utf-8")
print("\n=== migration 0010_p22_tags ===")
ck('revision = "0010_p22_tags"' in mig, True, "revision 0010_p22_tags")
ck('down_revision = "0009_p20_inbound_webhooks"' in mig, True, "down_revision 0009 (chaîne)")
ck('op.create_table(' in mig, True, "create_table présent (tags + contact_tags)")
ck(mig.count("op.create_table("), 2, "2 create_table (tags + contact_tags)")
ck('"tags"' in mig, True, "table tags nommée")
ck('"contact_tags"' in mig, True, "table contact_tags nommée")
ck("ondelete=\"CASCADE\"" in mig, True, "CASCADE sur FK (contact + tag)")
ck("uq_tenant_tag_name" in mig, True, "UniqueConstraint tenant_id+name")
ck("pk_contact_tag" in mig, True, "PK composite contact_id+tag_id")

# ─── Frontend ──────────────────────────────────────────────────────────────────
tags_html = (ROOT / "frontend" / "public" / "tags.html").read_text(encoding="utf-8")
crm_html = (ROOT / "frontend" / "public" / "crm.html").read_text(encoding="utf-8")
print("\n=== frontend tags.html + crm.html ===")
ck(bool(tags_html), True, "tags.html existe")
ck("createTag" in tags_html, True, "tags.html createTag")
ck("loadTags" in tags_html, True, "tags.html loadTags")
ck("deleteTag" in tags_html, True, "tags.html deleteTag")
ck('type="color"' in tags_html, True, "tags.html color picker")
ck("/tenants/' + TID + '/tags" in tags_html, True, "tags.html appelle /tags")
ck("f-tag" in crm_html, True, "crm.html filtre f-tag dropdown")
ck("loadTagsFilter" in crm_html, True, "crm.html loadTagsFilter (peuple dropdown)")
ck("c.tags" in crm_html, True, "crm.html rend c.tags (badges)")
ck("tag_id=" in crm_html, True, "crm.html passe tag_id à la query contacts")
ck('href="tags.html"' in crm_html, True, "crm.html lien vers tags.html")

# ─── Introspection runtime (sqlite in-memory) ─────────────────────────────────
print("\n=== Introspection runtime : create/assign/idempotent/filtre/isolation ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))
    from app import database as db_mod
    from app import models as M, schemas as S
    from app.routers import tags as tags_r
    from app.routers import crm as crm_r

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    # seed : 2 tenants (A, B) + 1 user + 2 contacts dans A
    s.add(M.User(id="usr_a", email="a@test.com", name="Alice", password_hash="x"))
    s.add(M.Tenant(id="tnt_a", name="A", plan="Starter", owner_id="usr_a"))
    s.add(M.Tenant(id="tnt_b", name="B", plan="Starter", owner_id="usr_b"))
    s.flush()
    c1 = M.Contact(tenant_id="tnt_a", first_name="C1", last_name="X", email="c1@test.com", status="new")
    c2 = M.Contact(tenant_id="tnt_a", first_name="C2", last_name="Y", email="c2@test.com", status="new")
    s.add_all([c1, c2]); s.flush()
    # fake membership (require_permission retourne un objet avec user_id + name)
    mem = SimpleNamespace(user_id="usr_a", name="Alice", role="owner", tenant_id="tnt_a", status="active")

    # create_tag (A) — tag « VIP »
    tA = tags_r.create_tag("tnt_a", S.TagCreate(name="VIP", color="#ff0000"), db=s, _t=None, membership=mem)
    ck(tA.name, "VIP", "create_tag retourne TagOut name=VIP")
    ck(tA.color, "#ff0000", "create_tag color=#ff0000")
    ck(tA.contact_count, 0, "create_tag contact_count=0 (nouveau)")

    # create_tag doublon → 409
    try:
        tags_r.create_tag("tnt_a", S.TagCreate(name="VIP"), db=s, _t=None, membership=mem)
        ck("no raise", "409", "doublon nom → 409")
    except Exception as e:
        ck(getattr(e, "status_code", None), 409, "doublon nom → 409 (HTTPException status_code)")

    # create_tag tenant B (pour test isolation)
    tB = tags_r.create_tag("tnt_b", S.TagCreate(name="VIP-B"), db=s, _t=None, membership=mem)
    s.flush()

    # assign_tags : tA → c1 (idempotent)
    res = tags_r.assign_tags("tnt_a", c1.id, S.ContactTagAssign(tag_ids=[tA.id]), db=s, _t=None, membership=mem)
    ck(len(res), 1, "assign tA→c1 retourne 1 tag")
    ck(res[0].name, "VIP", "tag assigné = VIP")

    # idempotent : assigner encore → pas de doublon (toujours 1 association)
    res2 = tags_r.assign_tags("tnt_a", c1.id, S.ContactTagAssign(tag_ids=[tA.id]), db=s, _t=None, membership=mem)
    ck(len(res2), 1, "assign idempotent → toujours 1 (pas de doublon PK composite)")
    ct_count = s.query(M.ContactTag).filter_by(contact_id=c1.id).count()
    ck(ct_count, 1, "ContactTag count=1 après 2x assign (idempotent)")

    # isolation : assigner tB (tenant B) à c1 (tenant A) → 422
    try:
        tags_r.assign_tags("tnt_a", c1.id, S.ContactTagAssign(tag_ids=[tB.id]), db=s, _t=None, membership=mem)
        ck("no raise", "422", "tag hors tenant → 422")
    except Exception as e:
        ck(getattr(e, "status_code", None), 422, "tag hors tenant → 422 (validation tenant)")

    # _attach_tags : peuple c1.tags + c2.tags (c2 n'a pas de tag)
    crm_r._attach_tags(s, [c1, c2])
    ck(len(c1.tags), 1, "_attach_tags c1.tags = 1 (a le tag VIP)")
    ck(len(c2.tags), 0, "_attach_tags c2.tags = 0 (aucun tag)")
    ck(c1.tags[0].name, "VIP", "_attach_tags c1.tags[0].name = VIP")

    # _apply_contact_filters tag_id=tA.id → 1 contact (c1)
    q = crm_r._apply_contact_filters(s.query(M.Contact).filter_by(tenant_id="tnt_a"), "tnt_a", tag_id=tA.id)
    ck(q.count(), 1, "filtre tag_id=tA → 1 contact (c1 uniquement)")

    # list_contact_tags c1 → 1 tag
    lt = tags_r.list_contact_tags("tnt_a", c1.id, db=s, _t=None)
    ck(len(lt), 1, "list_contact_tags c1 → 1 tag")
    # list_contact_tags c2 → 0
    lt2 = tags_r.list_contact_tags("tnt_a", c2.id, db=s, _t=None)
    ck(len(lt2), 0, "list_contact_tags c2 → 0 tag")

    # list_tags tnt_a → 1 tag (tA) avec contact_count=1
    fake_resp = SimpleNamespace(headers={})
    lt3 = tags_r.list_tags("tnt_a", page=SimpleNamespace(skip=0, limit=50), response=fake_resp, db=s, _t=None)
    ck(len(lt3), 1, "list_tags tnt_a → 1 tag (tA, tB est dans tnt_b)")
    ck(lt3[0].contact_count, 1, "list_tags tnt_a contact_count=1 (c1 assigné)")

    # unassign_tag c1/tA → association supprimée
    tags_r.unassign_tag("tnt_a", c1.id, tA.id, db=s, _t=None, membership=mem)
    ck(s.query(M.ContactTag).filter_by(contact_id=c1.id).count(), 0, "unassign → 0 association")

    # delete_tag tA → cascade-supprime (déjà unassign, mais teste le delete lui-même)
    tags_r.delete_tag("tnt_a", tA.id, db=s, _t=None, membership=mem)
    ck(s.query(M.Tag).filter_by(id=tA.id).count(), 0, "delete_tag → tag supprimé")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
