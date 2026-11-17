"""P26 — LinkedIn Content Studio professionnel router.

Endpoints (préfixe /api/tenants/{tid}/content-studio) :
  GET    /posts                       — lister les posts (filter status, paginé) (content.read).
  POST   /posts/generate              — générer un post via LinkedInAgent P25 (content.publish).
  POST   /posts                       — créer un post manuellement (content.publish).
  POST   /carousel/generate           — générer un carrousel (content.publish).
  GET    /posts/{pid}                 — détail d'un post (content.read).
  PATCH  /posts/{pid}                 — éditer un post (content.publish).
  DELETE /posts/{pid}                 — supprimer un post (content.publish).
  POST   /posts/{pid}/request-validation — demander validation (draft → pending_review) (content.publish).
  POST   /posts/{pid}/approve         — approuver (pending_review → approved) (approvals.manage).
  POST   /posts/{pid}/reject          — rejeter + reason (approvals.manage).
  POST   /posts/{pid}/schedule        — planifier (approved → scheduled + scheduled_at) (content.publish).
  POST   /posts/{pid}/publish         — publier (approved|scheduled → published) (approvals.manage).
  GET    /templates                   — lister templates (auto-seed 6 système si absent) (content.read).
  POST   /templates                   — créer un template (content.publish).
  DELETE /templates/{tpl_id}          — supprimer un template (non-système seulement) (content.publish).
  GET    /calendar                    — calendrier éditorial (posts par date) (content.read).
  GET    /stats                       — statistiques Content Studio (content.read).

RBAC (P26) :
  - content.read (Viewer+) — lecture (posts, templates, calendar, stats).
  - content.publish (Contributor+) — write (generate, create, edit, delete, schedule, request-validation).
  - approvals.manage (Editor+) — approve, reject, publish (validation collaborative + action externe).

Sécurité (P26) :
  - generate_post utilise LinkedInAgent P25 → require_human_validation propagé (injection OU action
    externe). publish est bloqué par le workflow (require approved — qui nécessite validation humaine
    explicite via approve endpoint).
  - simulated propagé depuis l'AI Gateway (anti-fake P24-B).
  - publish NE publie PAS réellement sur LinkedIn en v1 (linkedin_urn simulé) — la publication réelle
    via LinkedIn API P12 (UGC posts endpoint) est un concern séparé.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import content_studio as cs

router = APIRouter(prefix="/api/tenants/{tid}/content-studio", tags=["content-studio"])


# ─── Templates (auto-seed 6 système) ─────────────────────────────────────────


@router.get("/templates", response_model=list[schemas.ContentTemplateOut])
def list_templates(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Liste les templates du tenant (auto-seed les 6 templates système au 1er accès — idempotent)."""
    cs.seed_content_templates(db, tid)  # idempotent
    db.commit()  # persiste les templates seedés (seed fait flush seulement — commit explicite requis)
    return (
        db.query(models.ContentTemplate)
        .filter_by(tenant_id=tid)
        .order_by(models.ContentTemplate.is_system.desc(), models.ContentTemplate.name.asc())
        .all()
    )


@router.post("/templates", response_model=schemas.ContentTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    tid: str,
    body: schemas.ContentTemplateRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Crée un template personnalisé (is_system=False — modifiable/supprimable)."""
    tpl = models.ContentTemplate(
        tenant_id=tid,
        name=body.name,
        category=body.category,
        structure=body.structure,
        variables=body.variables,
        style=body.style,
        is_system=False,
        created_by=membership.user_id,
    )
    db.add(tpl)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.template.create", target_type="content_template", target_id=tpl.id,
              detail={"name": body.name, "category": body.category})
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/templates/{tpl_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    tid: str,
    tpl_id: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Supprime un template (non-système seulement — les système sont immuables)."""
    tpl = db.query(models.ContentTemplate).filter_by(id=tpl_id, tenant_id=tid).first()
    if not tpl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template introuvable")
    if tpl.is_system:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Les templates système sont immuables (non supprimables)")
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.template.delete", target_type="content_template", target_id=tpl.id,
              detail={"name": tpl.name})
    db.delete(tpl)
    db.commit()


# ─── Posts : génération + CRUD ───────────────────────────────────────────────


@router.post("/posts/generate", response_model=schemas.ContentPostOut, status_code=status.HTTP_201_CREATED)
def generate_post(
    tid: str,
    body: schemas.ContentGenerateRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Génère un post LinkedIn via LinkedInAgent P25 (connecté AI Gateway P24-B). Crée un ContentPost
    en status='draft' + ai_generation_id (audit trail)."""
    post = cs.generate_post(db, tenant_id=tid,
                            params={"topic": body.topic, "style": body.style, "title": body.title},
                            user_id=membership.user_id)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.generate", target_type="content_post", target_id=post.id,
              detail={"topic": body.topic, "ai_generation_id": post.ai_generation_id})
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts", response_model=schemas.ContentPostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    tid: str,
    body: schemas.ContentPostRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Crée un post manuellement (status='draft')."""
    post = models.ContentPost(
        tenant_id=tid,
        title=body.title,
        content_text=body.content_text,
        topic=body.topic,
        style=body.style,
        hashtags=body.hashtags,
        template_id=body.template_id,
        status="draft",
        created_by=membership.user_id,
    )
    db.add(post)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.create", target_type="content_post", target_id=post.id,
              detail={"title": body.title})
    db.commit()
    db.refresh(post)
    return post


@router.post("/carousel/generate", response_model=list[dict], status_code=status.HTTP_200_OK)
def generate_carousel(
    tid: str,
    body: schemas.ContentCarouselRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Génère un carrousel (slides_count slides sur un topic via LinkedInAgent P25). Retourne les slides
    [{order, text, image_url=NULL}]. L'utilisateur attache ses visuels via le Content Studio UI."""
    slides = cs.generate_carousel(db, tenant_id=tid, topic=body.topic, slides_count=body.slides_count,
                                  user_id=membership.user_id, style=body.style)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.carousel.generate", target_type="content_post",
              detail={"topic": body.topic, "slides_count": body.slides_count, "slides": len(slides)})
    db.commit()
    return slides


@router.get("/posts", response_model=list[schemas.ContentPostOut])
def list_posts(
    tid: str,
    response: Response,
    status_filter: str | None = Query(None, alias="status"),
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Liste les posts du tenant (filterable par status, paginé, plus récent d'abord)."""
    q = db.query(models.ContentPost).filter_by(tenant_id=tid)
    if status_filter:
        q = q.filter(models.ContentPost.status == status_filter)
    q = q.order_by(models.ContentPost.created_at.desc())
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()


@router.get("/posts/{pid}", response_model=schemas.ContentPostOut)
def get_post(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Détail d'un post."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    return post


@router.patch("/posts/{pid}", response_model=schemas.ContentPostOut)
def update_post(
    tid: str,
    pid: str,
    body: schemas.ContentPostUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Édite un post (partial update). Bloqué si status='published' (déjà publié — immuable)."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    if post.status == "published":
        raise HTTPException(status.HTTP_409_CONFLICT, "Post déjà publié — immuable (créez une nouvelle version)")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(post, k, v)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.update", target_type="content_post", target_id=post.id,
              detail={"fields": list(data.keys())})
    db.commit()
    db.refresh(post)
    return post


@router.delete("/posts/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Supprime un post. Bloqué si status='published' (archive au lieu de supprimer)."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    if post.status == "published":
        raise HTTPException(status.HTTP_409_CONFLICT, "Post publié — archivage requis (ne peut être supprimé)")
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.delete", target_type="content_post", target_id=post.id)
    db.delete(post)
    db.commit()


# ─── Workflow validation collaborative ───────────────────────────────────────


@router.post("/posts/{pid}/request-validation", response_model=schemas.ContentPostOut)
def request_validation(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Demande validation (draft|rejected → pending_review). Validation collaborative."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    try:
        post = cs.request_validation(db, post, membership.user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.request_validation", target_type="content_post", target_id=post.id)
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{pid}/approve", response_model=schemas.ContentPostOut)
def approve_post(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("approvals.manage")),
):
    """Approuve un post (pending_review → approved). RBAC approvals.manage (Editor+)."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    try:
        post = cs.approve_post(db, post, membership.user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.approve", target_type="content_post", target_id=post.id)
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{pid}/reject", response_model=schemas.ContentPostOut)
def reject_post(
    tid: str,
    pid: str,
    body: schemas.ContentRejectRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("approvals.manage")),
):
    """Rejette un post + reason (pending_review → rejected). RBAC approvals.manage (Editor+)."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    try:
        post = cs.reject_post(db, post, body.reason, membership.user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.reject", target_type="content_post", target_id=post.id,
              detail={"reason": body.reason[:200]})
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{pid}/schedule", response_model=schemas.ContentPostOut)
def schedule_post(
    tid: str,
    pid: str,
    body: schemas.ContentScheduleRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("content.publish")),
):
    """Planifie un post (approved → scheduled + scheduled_at). Le scheduled_at doit être futur."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    if body.scheduled_at <= datetime.utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scheduled_at doit être dans le futur")
    try:
        post = cs.schedule_post(db, post, body.scheduled_at)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.schedule", target_type="content_post", target_id=post.id,
              detail={"scheduled_at": body.scheduled_at.isoformat()})
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{pid}/publish", response_model=schemas.ContentPostOut)
def publish_post(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("approvals.manage")),
):
    """Publie un post (approved|scheduled → published + linkedin_urn). RBAC approvals.manage (Editor+).
    En v1, linkedin_urn est simulé (urn:li:share:simulated-...) — la publication RÉELLE via LinkedIn
    API P12 (UGC posts endpoint) est un concern séparé."""
    post = db.query(models.ContentPost).filter_by(id=pid, tenant_id=tid).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post introuvable")
    try:
        post = cs.publish_post(db, post, membership.user_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="content.post.publish", target_type="content_post", target_id=post.id,
              detail={"linkedin_urn": post.linkedin_urn})
    db.commit()
    db.refresh(post)
    return post


# ─── Calendrier éditorial + stats ────────────────────────────────────────────


@router.get("/calendar", response_model=list[schemas.ContentCalendarItem])
def editorial_calendar(
    tid: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Calendrier éditorial : posts scheduled + published sur les N prochains jours (groupés par date)."""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    posts = (
        db.query(models.ContentPost)
        .filter(models.ContentPost.tenant_id == tid)
        .filter(models.ContentPost.status.in_(("scheduled", "published")))
        .filter(models.ContentPost.scheduled_at.isnot(None) | models.ContentPost.published_at.isnot(None))
        .all()
    )
    by_date: dict[str, list[models.ContentPost]] = {}
    for p in posts:
        d = (p.scheduled_at or p.published_at).strftime("%Y-%m-%d")
        by_date.setdefault(d, []).append(p)
    items = []
    cur = start
    while cur <= end:
        d = cur.strftime("%Y-%m-%d")
        items.append(schemas.ContentCalendarItem(date=d, posts=by_date.get(d, [])))
        cur += timedelta(days=1)
    return items


@router.get("/stats", response_model=schemas.ContentStats)
def content_stats(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("content.read")),
):
    """Statistiques Content Studio : posts par status, total published/scheduled, engagement total."""
    posts = db.query(models.ContentPost).filter_by(tenant_id=tid).all()
    by_status: dict[str, int] = {}
    engagement = 0
    for p in posts:
        by_status[p.status] = by_status.get(p.status, 0) + 1
        if p.status == "published" and p.stats:
            st = p.stats or {}
            engagement += int(st.get("impressions", 0)) + int(st.get("likes", 0)) + \
                int(st.get("comments", 0)) + int(st.get("shares", 0))
    total_templates = db.query(models.ContentTemplate).filter_by(tenant_id=tid).count()
    return schemas.ContentStats(
        total_posts=len(posts),
        posts_by_status=by_status,
        total_published=by_status.get("published", 0),
        total_scheduled=by_status.get("scheduled", 0),
        total_templates=total_templates,
        total_engagement=engagement,
    )
