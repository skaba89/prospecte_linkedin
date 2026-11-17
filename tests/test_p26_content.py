#!/usr/bin/env python3
"""test_p26_content.py — validation statique + runtime de P26 LinkedIn Content Studio.

Couvre (par lecture des sources + introspection runtime sur sqlite in-memory) :
  - models.py : ContentTemplate (modèles réutilisables, is_system) + ContentPost (workflow validation
    collaborative draft→pending_review→approved|rejected→scheduled→published, ai_generation_id FK P24-B).
  - migration 0016 : content_templates + content_posts (JSONB.with_variant pour carousel/media/stats).
  - services/content_studio.py : seed_content_templates (6 système, idempotent), generate_post (via
    LinkedInAgent P25), generate_carousel, request_validation, approve_post, reject_post, schedule_post,
    publish_post.
  - routers/content_studio.py : 17 endpoints + RBAC (content.read Viewer+, content.publish Contributor+,
    approvals.manage Editor+ pour approve/reject/publish) + audit content.post.*.
  - schemas.py : ContentTemplateRequest/Out, ContentGenerateRequest, ContentCarouselRequest,
    ContentPostRequest/Update/Out, ContentScheduleRequest, ContentRejectRequest, ContentCalendarItem,
    ContentStats.
  - deps.py : content.read ajouté à tous les rôles (Viewer non-vide désormais).
  - main.py : router content_studio inclus.
  - Runtime : seed (6 + idempotent), generate_post (simulated), generate_carousel (5 slides), workflow
    complet (draft→pending_review→approved→scheduled→published), reject, validation errors.
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


print("=== test_p26_content.py — P26 LinkedIn Content Studio ===\n")

# ─── Source : models.py (ContentTemplate + ContentPost) ──────────────────────
models_src = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
print("=== models.py : ContentTemplate + ContentPost ===")
ck("class ContentTemplate(Base):" in models_src, True, "ContentTemplate model")
ck('__tablename__ = "content_templates"' in models_src, True, "table content_templates")
ck("class ContentPost(Base):" in models_src, True, "ContentPost model")
ck('__tablename__ = "content_posts"' in models_src, True, "table content_posts")
ck("is_system = Column(Boolean" in models_src, True, "ContentTemplate.is_system flag")
ck("carousel = Column(JSONType" in models_src, True, "ContentPost.carousel JSON (slides)")
ck("ai_generation_id = Column(String, ForeignKey" in models_src, True, "ContentPost.ai_generation_id FK (audit trail P24-B→P25→P26)")
ck("validation_requested_by = Column(String, ForeignKey" in models_src, True, "workflow validation_requested_by")
ck("validated_by = Column(String, ForeignKey" in models_src, True, "workflow validated_by")
ck("rejected_reason = Column(Text" in models_src, True, "workflow rejected_reason")
ck("linkedin_urn = Column(String" in models_src, True, "linkedin_urn (publication LinkedIn)")
ck("stats = Column(JSONType" in models_src, True, "stats JSON (impressions, likes, comments, shares)")
ck("scheduled_at = Column(DateTime" in models_src, True, "scheduled_at (planification)")

# ─── Source : migration 0016 ─────────────────────────────────────────────────
mig = (ROOT / "backend" / "alembic" / "versions" / "0016_p26_content_studio.py").read_text(encoding="utf-8")
print("\n=== migration 0016_p26_content_studio ===")
ck('revision = "0016_p26_content_studio"' in mig, True, "revision 0016_p26_content_studio")
ck('down_revision = "0015_p25_ai_agents"' in mig, True, "down_revision 0015 (chaîne)")
ck('"content_templates"' in mig and '"content_posts"' in mig, True, "2 tables créées")
ck("JSONB().with_variant(sa.Text()" in mig, True, "carousel/media/stats JSONB PG / Text sqlite")

# ─── Source : services/content_studio.py ─────────────────────────────────────
svc_src = (ROOT / "backend" / "app" / "services" / "content_studio.py").read_text(encoding="utf-8")
print("\n=== services/content_studio.py : seed + generate + workflow ===")
ck("def seed_content_templates(" in svc_src, True, "seed_content_templates (idempotent)")
ck("_SYSTEM_TEMPLATES" in svc_src, True, "_SYSTEM_TEMPLATES (6 templates)")
ck("storytelling" in svc_src and "insight" in svc_src, True, "templates : storytelling + insight")
ck("announcement" in svc_src and "carousel" in svc_src, True, "templates : announcement + carousel")
ck("question" in svc_src and "bts" in svc_src, True, "templates : question + behind_the_scenes")
ck("def generate_post(" in svc_src, True, "generate_post (via LinkedInAgent P25)")
ck("def generate_carousel(" in svc_src, True, "generate_carousel")
ck("def request_validation(" in svc_src, True, "request_validation (draft→pending_review)")
ck("def approve_post(" in svc_src, True, "approve_post (pending_review→approved)")
ck("def reject_post(" in svc_src, True, "reject_post (pending_review→rejected)")
ck("def schedule_post(" in svc_src, True, "schedule_post (approved→scheduled)")
ck("def publish_post(" in svc_src, True, "publish_post (approved|scheduled→published)")
ck("from .ai.agents import get_agent" in svc_src, True, "import LinkedInAgent (relatif 1 dot — app.services.ai)")
ck("get_agent(\"linkedin\")" in svc_src or 'get_agent("linkedin")' in svc_src, True, "utilise LinkedInAgent P25")
ck("urn:li:share:simulated" in svc_src, True, "linkedin_urn simulé en v1 (publication réelle P12 concern séparé)")

# ─── Source : routers/content_studio.py (17 endpoints + RBAC) ────────────────
rtr_src = (ROOT / "backend" / "app" / "routers" / "content_studio.py").read_text(encoding="utf-8")
print("\n=== routers/content_studio.py : 17 endpoints + RBAC + audit ===")
ck('prefix="/api/tenants/{tid}/content-studio"' in rtr_src, True, "router prefix /content-studio (séparé de /content P17)")
ck('tags=["content-studio"]' in rtr_src, True, "router tag content-studio")
ck('@router.get("/posts"' in rtr_src, True, "GET /posts (list)")
ck('@router.post("/posts/generate"' in rtr_src, True, "POST /posts/generate (AI LinkedInAgent)")
ck('@router.post("/posts"' in rtr_src, True, "POST /posts (create manual)")
ck('@router.post("/carousel/generate"' in rtr_src, True, "POST /carousel/generate")
ck('@router.get("/posts/{pid}"' in rtr_src, True, "GET /posts/{pid} (detail)")
ck('@router.patch("/posts/{pid}"' in rtr_src, True, "PATCH /posts/{pid} (edit)")
ck('@router.delete("/posts/{pid}"' in rtr_src, True, "DELETE /posts/{pid}")
ck('@router.post("/posts/{pid}/request-validation"' in rtr_src, True, "POST /request-validation")
ck('@router.post("/posts/{pid}/approve"' in rtr_src, True, "POST /approve (approvals.manage)")
ck('@router.post("/posts/{pid}/reject"' in rtr_src, True, "POST /reject (approvals.manage)")
ck('@router.post("/posts/{pid}/schedule"' in rtr_src, True, "POST /schedule")
ck('@router.post("/posts/{pid}/publish"' in rtr_src, True, "POST /publish (approvals.manage)")
ck('@router.get("/templates"' in rtr_src, True, "GET /templates (auto-seed)")
ck('@router.post("/templates"' in rtr_src, True, "POST /templates (create)")
ck('@router.delete("/templates/{tpl_id}"' in rtr_src, True, "DELETE /templates/{tpl_id} (non-système)")
ck('@router.get("/calendar"' in rtr_src, True, "GET /calendar (éditorial)")
ck('@router.get("/stats"' in rtr_src, True, "GET /stats")
ck('require_permission("content.read")' in rtr_src, True, "RBAC content.read (Viewer+)")
ck('require_permission("content.publish")' in rtr_src, True, "RBAC content.publish (Contributor+)")
ck('require_permission("approvals.manage")' in rtr_src, True, "RBAC approvals.manage (Editor+ — approve/reject/publish)")
ck("content.post.generate" in rtr_src, True, "audit content.post.generate")
ck("content.post.approve" in rtr_src, True, "audit content.post.approve")
ck("content.post.publish" in rtr_src, True, "audit content.post.publish")
ck("from e" in rtr_src, True, "B904 fix : raise ... from e (5 occurrences)")
ck("is_system" in rtr_src, True, "templates système immuables (delete bloqué)")

# ─── Source : schemas.py (P26 schemas) ───────────────────────────────────────
sch_src = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
print("\n=== schemas.py : P26 schemas ===")
ck("class ContentTemplateRequest" in sch_src, True, "ContentTemplateRequest")
ck("class ContentTemplateOut" in sch_src, True, "ContentTemplateOut")
ck("class ContentGenerateRequest" in sch_src, True, "ContentGenerateRequest")
ck("class ContentCarouselRequest" in sch_src, True, "ContentCarouselRequest")
ck("class ContentPostRequest" in sch_src, True, "ContentPostRequest")
ck("class ContentPostUpdate" in sch_src, True, "ContentPostUpdate")
ck("class ContentPostOut" in sch_src, True, "ContentPostOut")
ck("class ContentScheduleRequest" in sch_src, True, "ContentScheduleRequest")
ck("class ContentRejectRequest" in sch_src, True, "ContentRejectRequest")
ck("class ContentCalendarItem" in sch_src, True, "ContentCalendarItem")
ck("class ContentStats" in sch_src, True, "ContentStats")

# ─── Source : deps.py (content.read ajouté) ──────────────────────────────────
deps_src = (ROOT / "backend" / "app" / "deps.py").read_text(encoding="utf-8")
print("\n=== deps.py : content.read (Viewer+) ===")
ck('"content.read"' in deps_src, True, "permission content.read définie")
ck('"Viewer": {"content.read"}' in deps_src, True, "Viewer a content.read (non-vide désormais)")

# ─── Source : main.py (router content_studio inclus) ─────────────────────────
main_src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
print("\n=== main.py : router content_studio inclus ===")
ck("    content_studio," in main_src, True, "main.py import content_studio")
ck("content_studio.router" in main_src, True, "main.py include content_studio.router")

# ─── Introspection runtime (sqlite in-memory) ────────────────────────────────
print("\n=== Introspection runtime : seed + generate + workflow ===")
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    print("  ⚠ Introspection ignorée (sqlalchemy non installé — validée via E2E).")
else:
    os.environ["REVPULSE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["REVPULSE_ENV"] = "test"
    sys.path.insert(0, str(ROOT / "backend"))

    from app import database as db_mod
    from app import models as M
    from app.services import content_studio as cs

    engine = db_mod.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    db_mod.SessionLocal.configure(bind=engine)
    M.Base.metadata.create_all(engine)
    s = db_mod.SessionLocal()
    s.add(M.User(id="usr_p26", email="p26@test.com", name="P26", password_hash="h"))
    s.add(M.Tenant(id="tnt_p26", name="P26", plan="Starter", owner_id="usr_p26"))
    s.add(M.Member(id="mem_p26", tenant_id="tnt_p26", user_id="usr_p26", email="p26@test.com",
                   name="P26", role="Owner", status="active"))
    s.commit()

    # ── seed_content_templates : 6 système, idempotent ──
    print("--- seed_content_templates : 6 système + idempotent ---")
    n1 = cs.seed_content_templates(s, "tnt_p26")
    ck(n1, 6, f"1er seed : {n1} templates (6 attendus)")
    n2 = cs.seed_content_templates(s, "tnt_p26")
    ck(n2, 0, f"2e seed : {n2} (0 — idempotent)")
    tpls = s.query(M.ContentTemplate).filter_by(tenant_id="tnt_p26").all()
    ck(len(tpls), 6, f"{len(tpls)} templates en DB (6)")
    sys_tpls = [t for t in tpls if t.is_system]
    ck(len(sys_tpls), 6, "tous is_system=True (immuables)")
    cats = {t.category for t in tpls}
    ck("storytelling" in cats, True, "catégorie storytelling présente")
    ck("carousel" in cats, True, "catégorie carousel présente")

    # ── generate_post : via LinkedInAgent P25 (simulated, no provider) ──
    print("--- generate_post : LinkedInAgent P25 (simulated) ---")
    post = cs.generate_post(s, tenant_id="tnt_p26",
                            params={"topic": "Sales B2B 2026", "style": "expert", "title": "Post test"},
                            user_id="usr_p26")
    ck(post.status, "draft", "generate_post → status=draft")
    ck(len(post.content_text) > 0, True, f"content_text généré ({len(post.content_text)} chars)")
    ck(post.ai_generation_id is None, True, "ai_generation_id=None en mode simulated (no provider — P24-B : gateway ne persiste pas d'AIGeneration sans config)")
    ck(post.topic, "Sales B2B 2026", "topic propagé")
    ck(post.created_by, "usr_p26", "created_by propagé")

    # ── generate_carousel : 5 slides ──
    print("--- generate_carousel : 5 slides ---")
    slides = cs.generate_carousel(s, tenant_id="tnt_p26", topic="Sales process", slides_count=5,
                                  user_id="usr_p26", style="éducatif")
    ck(len(slides), 5, f"{len(slides)} slides générés (5)")
    ck(slides[0]["order"], 1, "1er slide order=1")
    ck("text" in slides[0], True, "slides ont un champ text")
    ck(slides[0].get("image_url") is None, True, "image_url=NULL en v1 (visuels attachés via UI)")

    # ── Workflow validation collaborative : draft → pending_review → approved → scheduled → published ──
    print("--- workflow complet : draft → pending_review → approved → scheduled → published ---")
    ck(post.status, "draft", "post initial : draft")
    cs.request_validation(s, post, "usr_p26")
    ck(post.status, "pending_review", "request_validation → pending_review")
    ck(post.validation_requested_by, "usr_p26", "validation_requested_by setté")
    cs.approve_post(s, post, "usr_p26")
    ck(post.status, "approved", "approve_post → approved")
    ck(post.validated_by, "usr_p26", "validated_by setté")
    from datetime import datetime, timedelta
    future = datetime.utcnow() + timedelta(days=7)
    cs.schedule_post(s, post, future)
    ck(post.status, "scheduled", "schedule_post → scheduled")
    ck(post.scheduled_at, future, "scheduled_at setté")
    cs.publish_post(s, post, "usr_p26")
    ck(post.status, "published", "publish_post → published")
    ck(post.published_at is not None, True, "published_at setté")
    ck(post.linkedin_urn.startswith("urn:li:share:simulated"), True, f"linkedin_urn simulé ({post.linkedin_urn[:30]}...)")

    # ── Workflow reject : draft → pending_review → rejected ──
    print("--- workflow reject : draft → pending_review → rejected ---")
    post2 = cs.generate_post(s, tenant_id="tnt_p26", params={"topic": "Test reject", "style": "expert"},
                             user_id="usr_p26")
    ck(post2.status, "draft", "post2 initial : draft")
    cs.request_validation(s, post2, "usr_p26")
    ck(post2.status, "pending_review", "post2 → pending_review")
    cs.reject_post(s, post2, "Contenu hors-sujet", "usr_p26")
    ck(post2.status, "rejected", "reject_post → rejected")
    ck(post2.rejected_reason, "Contenu hors-sujet", "rejected_reason setté")
    # Re-submit après reject (draft → pending_review à nouveau)
    cs.request_validation(s, post2, "usr_p26")
    ck(post2.status, "pending_review", "re-submit après reject → pending_review")
    ck(post2.rejected_reason is None, True, "rejected_reason cleared après re-submit")

    # ── Validation errors (workflow transitions illégales) ──
    print("--- validation errors (transitions illégales) ---")
    post3 = cs.generate_post(s, tenant_id="tnt_p26", params={"topic": "Test errors", "style": "expert"},
                             user_id="usr_p26")
    err = None
    try:
        cs.approve_post(s, post3, "usr_p26")  # draft → approved illégal
    except ValueError as e:
        err = str(e)
    ck("pending_review requis" in err, True, f"approve depuis draft → ValueError ({err[:40]}...)")
    err2 = None
    try:
        cs.publish_post(s, post3, "usr_p26")  # draft → published illégal
    except ValueError as e:
        err2 = str(e)
    ck("approved ou scheduled requis" in err2, True, f"publish depuis draft → ValueError ({err2[:40]}...)")

    s.close()
    db_mod.SessionLocal.configure(bind=None)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
