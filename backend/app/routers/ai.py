"""P24-B — AI Gateway router : génération + providers CRUD + prompts versionnés + historique.

Endpoints (préfixe /api/tenants/{tid}/ai) :
  POST   /generate            — générer du texte (member ai.use). Retourne AIGenerateResponse
                                (simulated=True si dry_run/aucun provider → jamais présenté comme réel).
  GET    /providers           — lister les configs provider (admin workspace.manage).
  POST   /providers           — créer une config (api_key chiffré Fernet à la persistence).
  PATCH  /providers/{pid}     — update (api_key si fourni = rotation).
  DELETE /providers/{pid}     — delete.
  GET    /models              — lister les modèles disponibles (member — par provider configuré).
  GET    /prompts             — lister les prompts versionnés (member).
  POST   /prompts             — créer une version (auto-incrément + active switch).
  GET    /generations         — historique des générations (member, paginé).

RBAC :
  - ai.use (member+) : generate, list models/prompts/generations.
  - workspace.manage (admin/owner) : providers CRUD (clés API + coûts — réservé admin).

Sécurité (P24-B) : api_key jamais exposé en lecture (has_api_key booléen). Validation humaine
requise si injection détectée (require_human_validation=True dans la réponse — l'appelant DOIT
bloquer l'action externe). Audit log sur create/update/delete provider + create prompt.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import log_audit
from ..database import get_db
from ..deps import get_tenant, require_permission
from ..pagination import Page, page_dep, set_total
from ..services import crypto, url_safety
from ..services.ai import gateway
from ..services.ai.providers import SUPPORTED_PROVIDERS, get_provider

router = APIRouter(prefix="/api/tenants/{tid}/ai", tags=["ai"])


def _get_cfg(db: Session, tid: str, pid: str) -> models.AIProviderConfig:
    cfg = db.query(models.AIProviderConfig).filter_by(id=pid, tenant_id=tid).first()
    if not cfg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Config provider IA introuvable")
    return cfg


# ─── Génération ───────────────────────────────────────────────────────────────


@router.post("/generate", response_model=schemas.AIGenerateResponse)
def generate(
    tid: str,
    body: schemas.AIGenerateRequest,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("ai.use")),
):
    """Génère du texte via l'AI Gateway (P24-B). Orchestration : prompt rendu → PII redact →
    injection check → provider select + fallback → quota → historisation. simulated=True si dry_run
    ou aucun provider configuré (jamais présenté comme réel). require_human_validation=True si
    injection détectée (l'action externe est bloquée jusqu'à validation humaine)."""
    result = gateway.generate(
        db,
        tenant_id=tid,
        prompt_name=body.prompt_name,
        variables=body.variables,
        model=body.model,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        system=body.system,
        user_id=membership.user_id,
        dry_run=body.dry_run,
    )
    return result


# ─── Providers CRUD (admin) ──────────────────────────────────────────────────


@router.get("/providers", response_model=list[schemas.AIProviderConfigOut])
def list_providers(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("workspace.manage")),
):
    """Liste les configs provider du tenant (admin)."""
    return (
        db.query(models.AIProviderConfig)
        .filter_by(tenant_id=tid)
        .order_by(models.AIProviderConfig.priority.asc(), models.AIProviderConfig.created_at.desc())
        .all()
    )


@router.post("/providers", response_model=schemas.AIProviderConfigOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    tid: str,
    body: schemas.AIProviderConfigCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Crée une config provider IA (admin). api_key chiffré Fernet à la persistence (jamais en clair).
    provider doit être dans SUPPORTED_PROVIDERS. Audit ai.provider.create."""
    if body.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Provider non supporté : {body.provider} (supportés : {SUPPORTED_PROVIDERS})")
    if not get_provider(body.provider):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Provider inconnu : {body.provider}")
    # P32 : SSRF — valider base_url si fourni (validate_url_strict bloque metadata/CGNAT TOUJOURS,
    # même en dev — un provider IA ne vit jamais sur le metadata service. Le bypass dev (_ALLOW_PRIVATE)
    # ne s'applique qu'au loopback IPv4 (Ollama local) + private IPv4 (proxy interne)).
    if body.base_url:
        ok, err, _ = url_safety.validate_url_strict(body.base_url)
        if not ok:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"base_url refusée (SSRF) : {err}")
    cfg = models.AIProviderConfig(
        tenant_id=tid,
        provider=body.provider,
        label=body.label,
        base_url=body.base_url,
        enabled=body.enabled,
        priority=body.priority,
        quota_tokens_month=body.quota_tokens_month,
        cost_usd_month=body.cost_usd_month,
        tokens_used_month=0,
        cost_incurred_month=0.0,
        created_by=membership.user_id,
    )
    if body.api_key:
        cfg.api_key_enc = crypto.encrypt(body.api_key)
    db.add(cfg)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="ai.provider.create", target_type="ai_provider", target_id=cfg.id,
              detail={"provider": body.provider, "label": body.label})
    db.commit()
    db.refresh(cfg)
    return cfg


@router.patch("/providers/{pid}", response_model=schemas.AIProviderConfigOut)
def update_provider(
    tid: str,
    pid: str,
    body: schemas.AIProviderConfigUpdate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Update partielle d'une config provider (admin). api_key si fourni = rotation (re-chiffré)."""
    cfg = _get_cfg(db, tid, pid)
    detail = {}
    if body.label is not None:
        cfg.label = body.label
        detail["label"] = body.label
    if body.api_key is not None:
        cfg.api_key_enc = crypto.encrypt(body.api_key)
        detail["api_key_rotated"] = True
    if body.base_url is not None:
        # P32 : SSRF strict — valider la nouvelle base_url si non vide (clear = "" autorisé).
        if body.base_url:
            ok, err, _ = url_safety.validate_url_strict(body.base_url)
            if not ok:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    f"base_url refusée (SSRF) : {err}")
        cfg.base_url = body.base_url
        detail["base_url"] = body.base_url
    if body.enabled is not None:
        cfg.enabled = body.enabled
        detail["enabled"] = body.enabled
    if body.priority is not None:
        cfg.priority = body.priority
        detail["priority"] = body.priority
    if body.quota_tokens_month is not None:
        cfg.quota_tokens_month = body.quota_tokens_month
        detail["quota_tokens"] = body.quota_tokens_month
    if body.cost_usd_month is not None:
        cfg.cost_usd_month = body.cost_usd_month
        detail["cost_cap"] = body.cost_usd_month
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="ai.provider.update", target_type="ai_provider", target_id=cfg.id, detail=detail)
    db.commit()
    db.refresh(cfg)
    return cfg


@router.delete("/providers/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    tid: str,
    pid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("workspace.manage")),
):
    """Delete une config provider (admin). CASCADE sur ai_generations."""
    cfg = _get_cfg(db, tid, pid)
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="ai.provider.delete", target_type="ai_provider", target_id=cfg.id,
              detail={"provider": cfg.provider, "label": cfg.label})
    db.delete(cfg)
    db.commit()
    return None


# ─── Modèles disponibles (member) ─────────────────────────────────────────────


@router.get("/models", response_model=list[schemas.AIModelInfo])
def list_models(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("ai.use")),
):
    """Liste les modèles IA disponibles (par provider). configured=True si le tenant a une config
    active pour le provider. Coûts + context window depuis le catalogue statique."""
    return gateway.list_models_for_tenant(db, tid)


# ─── Prompts versionnés (member create, admin pas requis pour ses propres prompts) ─


@router.get("/prompts", response_model=list[schemas.PromptVersionOut])
def list_prompts(
    tid: str,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("ai.use")),
):
    """Liste les prompts versionnés du tenant (active d'abord, puis version desc)."""
    return (
        db.query(models.PromptVersion)
        .filter_by(tenant_id=tid)
        .order_by(models.PromptVersion.active.desc(), models.PromptVersion.name.asc(),
                  models.PromptVersion.version.desc())
        .all()
    )


@router.post("/prompts", response_model=schemas.PromptVersionOut, status_code=status.HTTP_201_CREATED)
def create_prompt(
    tid: str,
    body: schemas.PromptVersionCreate,
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    membership=Depends(require_permission("ai.use")),
):
    """Crée une version de prompt (member). version auto-incrémenté par (tenant, name). active=True
    → désactive les versions précédentes du même name (un seul actif). Audit ai.prompt.create."""
    last_version = (
        db.query(models.PromptVersion.version)
        .filter_by(tenant_id=tid, name=body.name)
        .order_by(models.PromptVersion.version.desc())
        .first()
    )
    next_version = (last_version[0] + 1) if last_version else 1
    if body.active:
        # Désactive les versions précédentes du même name.
        db.query(models.PromptVersion).filter(
            models.PromptVersion.tenant_id == tid, models.PromptVersion.name == body.name,
            models.PromptVersion.active.is_(True),
        ).update({models.PromptVersion.active: False}, synchronize_session=False)
    pv = models.PromptVersion(
        tenant_id=tid,
        name=body.name,
        version=next_version,
        content=body.content,
        variables=body.variables,
        active=body.active,
        notes=body.notes,
        created_by=membership.user_id,
    )
    db.add(pv)
    db.flush()
    log_audit(db, tenant_id=tid, actor_user_id=membership.user_id, actor_name=membership.name,
              action="ai.prompt.create", target_type="prompt_version", target_id=pv.id,
              detail={"name": body.name, "version": next_version, "active": body.active})
    db.commit()
    db.refresh(pv)
    return pv


# ─── Historique des générations (member) ──────────────────────────────────────


@router.get("/generations", response_model=list[schemas.AIGenerationOut])
def list_generations(
    tid: str,
    response: Response,
    page: Page = Depends(page_dep),
    db: Session = Depends(get_db),
    _t: models.Tenant = Depends(get_tenant),
    _perm=Depends(require_permission("ai.use")),
):
    """Historique des générations IA du tenant (plus récent d'abord, paginé)."""
    q = (
        db.query(models.AIGeneration)
        .filter_by(tenant_id=tid)
        .order_by(models.AIGeneration.created_at.desc())
    )
    set_total(response, q.count())
    return q.offset(page.skip).limit(page.limit).all()
