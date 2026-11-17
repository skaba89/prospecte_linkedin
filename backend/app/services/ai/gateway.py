"""P24-B — AI Gateway : orchestrateur multi-provider (sélection + fallback + quota + coût +
historisation + safety).

generate(db, tenant_id, *, prompt_name, variables, model, user_id, dry_run) → dict :
  1. Charge la version active du PromptVersion {prompt_name} → rendu avec {{variables}}.
  2. redact_pii(prompt) → supprime emails/phones/cards AVANT l'envoi au provider.
  3. detect_injection(prompt) → si suspect, require_human_validation=True (action externe bloquée).
  4. Sélectionne les AIProviderConfig actifs (enabled, ordre priority asc).
  5. Si dry_run OR aucun provider configuré → SimulatedProvider (simulated=True, JAMAIS présenté
     comme réel — le retour porte simulated=True + le texte est préfixé [SIMULÉ]).
  6. Pour chaque provider (fallback) :
     - Check quota (tokens_used_month < quota_tokens_month, cost_incurred_month < cost_usd_month).
     - get_provider(config.provider) → instance BaseProvider.
     - model = celui demandé OU le 1er du catalogue provider.
     - provider.complete(req, api_key_decrypted, base_url) → GenerationResponse.
     - Si success → update quota (tokens_used_month +=, cost_incurred_month +=, last_used_at) + break.
     - Si ProviderError → log + try next provider (fallback automatique).
  7. Historise AIGeneration (input rendu après PII, output, tokens, cost, latency, status, simulated).
  8. Retourne {generation_id, text, provider, model, tokens_in/out, cost_usd, simulated,
     require_human_validation, error}.

Sécurité (P24-B) :
  - Clés API chiffrées au repos (services/crypto) — decrypt via _get_api_key juste avant l'appel.
  - PII jamais envoyée au provider (redact_pii avant complete).
  - Injection detection → require_human_validation (l'action externe est bloquée jusqu'à validation).
  - simulated=True marqué clairement (jamais présenté comme réel — anti-fake).
  - Validation humaine avant toute action externe (envoi e-mail, publication LinkedIn) — le gateway
    ne déclenche JAMAIS d'action externe, il génère du texte ; l'action est faite par le router/
    l'agent qui DOIT vérifier require_human_validation.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from ... import models
from .. import billing_service, crypto
from .base import GenerationRequest, ProviderError
from .providers import PROVIDERS, SimulatedProvider, get_provider
from .safety import detect_injection, redact_pii, render_prompt

log = logging.getLogger("revpulse.ai.gateway")


def _get_api_key(config: models.AIProviderConfig) -> str | None:
    """Decrypt la clé API (services/crypto). Null pour Ollama sans auth."""
    if not config.api_key_enc:
        return None
    try:
        return crypto.decrypt(config.api_key_enc)
    except Exception as e:
        log.error("decrypt api_key échoué pour provider %s (%s): %s", config.provider, config.id, e)
        return None


def _select_model(provider_name: str, requested_model: str | None) -> str:
    """Sélectionne le modèle : celui demandé OU le 1er du catalogue provider (default)."""
    if requested_model:
        return requested_model
    provider = get_provider(provider_name)
    if provider and provider.models:
        return provider.models[0].name
    return "default"


def _within_quota(config: models.AIProviderConfig) -> bool:
    """True si le provider est dans son quota mensuel (tokens + cost). Null = illimité."""
    if config.quota_tokens_month is not None and config.tokens_used_month >= config.quota_tokens_month:
        return False
    if config.cost_usd_month is not None and config.cost_incurred_month >= config.cost_usd_month:
        return False
    return True


def generate(
    db: Session,
    *,
    tenant_id: str,
    prompt_name: str,
    variables: dict | None = None,
    model: str | None = None,
    max_tokens: int = 1000,
    temperature: float = 0.7,
    system: str | None = None,
    user_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Génère du texte via l'AI Gateway. Retourne un dict avec generation_id, text, provider, model,
    tokens_in/out, cost_usd, simulated, require_human_validation, error. Ne lève jamais (best-effort
    — en cas d'échec de tous les providers, fallback SimulatedProvider + error explicite)."""
    start = time.time()

    # 1. Charger + rendre le prompt versionné.
    pv = (
        db.query(models.PromptVersion)
        .filter(models.PromptVersion.tenant_id == tenant_id, models.PromptVersion.name == prompt_name,
                models.PromptVersion.active.is_(True))
        .order_by(models.PromptVersion.version.desc())
        .first()
    )
    if not pv:
        # Pas de prompt versionné → utiliser le prompt brut si fourni dans variables["_prompt"].
        raw_prompt = (variables or {}).get("_prompt") or prompt_name
        prompt_name_used = prompt_name
        prompt_version_id = None
    else:
        raw_prompt = render_prompt(pv.content, variables or {})
        prompt_name_used = pv.name
        prompt_version_id = pv.id

    # 2. PII redact (jamais envoyée au provider).
    redacted_prompt, pii_count = redact_pii(raw_prompt)

    # 3. Injection detection → require_human_validation si suspect.
    suspicious, injection_reasons = detect_injection(redacted_prompt)
    require_human_validation = suspicious

    # 3.bis P30 — Plan-based IA quota guard. Compte l'appel dans le quota mensuel du tenant (Free=10,
    # Starter=200, Pro=2000, Enterprise=illimité). Si quota dépassé → fallback SimulatedProvider +
    # flag quota_exceeded (le gateway ne lève jamais — best-effort). Skip si dry_run (ne consomme pas).
    quota_exceeded = False
    quota_used = 0
    quota_limit: int | None = None
    if not dry_run:
        allowed, quota_used, quota_limit = billing_service.check_and_increment_ia_quota(db, tenant_id)
        if not allowed:
            quota_exceeded = True

    # 4. Sélection provider configs (enabled, priority asc).
    configs = (
        db.query(models.AIProviderConfig)
        .filter(models.AIProviderConfig.tenant_id == tenant_id, models.AIProviderConfig.enabled.is_(True))
        .order_by(models.AIProviderConfig.priority.asc())
        .all()
    )

    # 5. Détermine si on doit simuler (dry_run OU aucun provider configuré OU aucun dans quota OU quota plan dépassé).
    # P24-B : historization_config_id = premier config (avant filter quota) → persiste l'AIGeneration
    # même si le provider est hors quota (audit trail complet). None si aucun config (dry_run sans config).
    historization_config_id = configs[0].id if configs else None
    use_simulated = dry_run or not configs or quota_exceeded
    if not use_simulated:
        configs_in_quota = [c for c in configs if _within_quota(c)]
        if not configs_in_quota:
            use_simulated = True
            configs = []
        else:
            configs = configs_in_quota

    # 6. Exécution : Simulated OU fallback sur les providers.
    response_text = ""
    tokens_in = tokens_out = 0
    cost_usd = 0.0
    used_provider = "simulated"
    used_model = _select_model("simulated", model)
    simulated = True
    error = None

    if use_simulated:
        sim = SimulatedProvider()
        req = GenerationRequest(model=used_model, prompt=redacted_prompt, system=system,
                                max_tokens=max_tokens, temperature=temperature)
        resp = sim.complete(req, None, None)
        response_text, tokens_in, tokens_out, cost_usd, used_model, simulated = (
            resp.text, resp.tokens_in, resp.tokens_out, resp.cost_usd, resp.model, True)
    else:
        simulated = False
        last_error = None
        for cfg in configs:
            provider = get_provider(cfg.provider)
            if not provider:
                continue
            used_model = _select_model(cfg.provider, model)
            req = GenerationRequest(model=used_model, prompt=redacted_prompt, system=system,
                                    max_tokens=max_tokens, temperature=temperature)
            api_key = _get_api_key(cfg)
            try:
                resp = provider.complete(req, api_key, cfg.base_url)
                response_text = resp.text
                tokens_in, tokens_out = resp.tokens_in, resp.tokens_out
                cost_usd = resp.cost_usd
                used_provider = cfg.provider
                used_model = resp.model
                simulated = resp.simulated
                # Update quota + last_used_at.
                cfg.tokens_used_month = (cfg.tokens_used_month or 0) + tokens_in + tokens_out
                cfg.cost_incurred_month = (cfg.cost_incurred_month or 0.0) + cost_usd
                cfg.last_used_at = datetime.utcnow()
                last_error = None
                break  # success — pas de fallback.
            except ProviderError as e:
                last_error = str(e)
                log.warning("provider %s (%s) échoué : %s — fallback suivant", cfg.provider, cfg.id, e)
                continue
            except Exception as e:  # catch large (provider bug) — fallback.
                last_error = f"{type(e).__name__}: {e}"
                log.exception("provider %s exception : %s — fallback", cfg.provider, e)
                continue
        if last_error and not response_text:
            # Tous les providers ont échoué → fallback Simulated (simulated=True, error explicite).
            sim = SimulatedProvider()
            req = GenerationRequest(model=_select_model("simulated", model), prompt=redacted_prompt,
                                    system=system, max_tokens=max_tokens, temperature=temperature)
            resp = sim.complete(req, None, None)
            response_text, tokens_in, tokens_out, cost_usd, used_model, simulated = (
                resp.text, resp.tokens_in, resp.tokens_out, resp.cost_usd, resp.model, True)
            used_provider = "simulated"
            error = f"tous les providers ont échoué — dernier : {last_error}"

    latency_ms = int((time.time() - start) * 1000)

    # 7. Historisation (AIGeneration) — input = prompt rendu APRÈS PII redact.
    # Si use_simulated/dry_run + aucun config → on crée un AIProviderConfig simulé éphémère ? Non :
    # on lie à un config existant si possible, sinon on ne persiste pas (dry_run sans config).
    config_id = historization_config_id  # P24-B : persiste même si quota filtered (audit trail complet)
    status = "simulated" if simulated else ("success" if response_text and not error else "failed")
    gen = None
    if config_id:
        gen = models.AIGeneration(
            tenant_id=tenant_id,
            provider_config_id=config_id,
            provider=used_provider,
            model=used_model,
            prompt_version_id=prompt_version_id,
            prompt_name=prompt_name_used,
            input_text=redacted_prompt[:8000],  # truncate pour stockage
            output_text=(response_text or "")[:8000],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            status=status,
            simulated=simulated,
            error=error,
            created_by=user_id,
        )
        db.add(gen)
        db.flush()

    db.commit()

    return {
        "generation_id": gen.id if gen else None,
        "text": response_text,
        "provider": used_provider,
        "model": used_model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 6),
        "simulated": simulated,
        "require_human_validation": require_human_validation,
        "injection_reasons": injection_reasons,
        "pii_redacted_count": pii_count,
        "latency_ms": latency_ms,
        "error": error,
        "status": status,
        "quota_exceeded": quota_exceeded,
        "quota_used": quota_used,
        "quota_limit": quota_limit,
    }


def list_models_for_tenant(db: Session, tenant_id: str) -> list[dict]:
    """Liste les modèles disponibles pour un tenant (par provider configuré). Pour les providers
    non configurés, retourne le catalogue statique (info). Format : [{provider, model, label,
    context_window, cost_in_per_1k, cost_out_per_1k, configured}]."""
    configs = (
        db.query(models.AIProviderConfig)
        .filter(models.AIProviderConfig.tenant_id == tenant_id, models.AIProviderConfig.enabled.is_(True))
        .all()
    )
    configured_providers = {c.provider for c in configs}
    out = []
    for pname, provider in PROVIDERS.items():
        if pname == "simulated":
            continue
        configured = pname in configured_providers
        for m in provider.models:
            out.append({
                "provider": pname,
                "model": m.name,
                "label": m.label or m.name,
                "context_window": m.context_window,
                "cost_in_per_1k": m.cost_in_per_1k,
                "cost_out_per_1k": m.cost_out_per_1k,
                "configured": configured,
            })
    return out
