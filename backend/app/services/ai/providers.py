"""P24-B — Implémentations concrètes des providers IA (httpx REST, pas de SDK lourds).

7 providers + Simulated (tests) :
  - OpenAICompatibleProvider (base) : POST {base}/chat/completions (OpenAI format). Sous-classes :
    OpenAIProvider, DeepSeekProvider, GLMProvider, OpenRouterProvider, OllamaProvider (local, cost=0).
  - AnthropicProvider : POST {base}/v1/messages (Anthropic format, headers x-api-key + anthropic-version).
  - GeminiProvider : POST {base}/models/{model}:generateContent?key= (Google format).
  - SimulatedProvider : retourne une réponse canned, simulated=True (jamais présenté comme réel).

Tous implémentent BaseProvider.complete(req, api_key, base_url) → GenerationResponse. Le gateway
catch ProviderError + fallback vers le provider suivant (priority). Coûts calculés via
BaseProvider._compute_cost (ModelInfo.cost_in/out_per_1k). Catalogue de modèles statique par
provider (coûts + context window connus — list_models() peut enrichir via API pour OpenAI/Ollama).

Timeout : 30s par défaut (AI_GATEWAY_TIMEOUT, monkey-patchable en tests). Retry/backoff gérés par
le gateway (pas par le provider — un provider = un appel, le gateway décide du fallback).
"""
from __future__ import annotations

import logging
import os
import time
from typing import ClassVar

import httpx

from .base import BaseProvider, GenerationRequest, GenerationResponse, ModelInfo, ProviderError

log = logging.getLogger("revpulse.ai.providers")

DEFAULT_TIMEOUT = float(os.getenv("AI_GATEWAY_TIMEOUT", "30"))


# ─── OpenAI-compatible base (OpenAI, DeepSeek, GLM, OpenRouter, Ollama) ───────


class OpenAICompatibleProvider(BaseProvider):
    """Base pour les providers compatibles OpenAI /v1/chat/completions. Sous-classes set name,
    default_base_url + models (catalogue coûts). complete() fait le POST + parse."""

    def _url(self, base_url: str | None) -> str:
        base = base_url or self.default_base_url
        return f"{base.rstrip('/')}/chat/completions"

    def _headers(self, api_key: str | None) -> dict:
        h = {"Content-Type": "application/json"}
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        return h

    def _payload(self, req: GenerationRequest) -> dict:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        return {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }

    def _find_model(self, model_name: str) -> ModelInfo:
        """Cherche le modèle dans le catalogue statique (pour le coût). Inconnu → cost=0."""
        for m in self.models:
            if m.name == model_name:
                return m
        return ModelInfo(name=model_name, context_window=8000, cost_in_per_1k=0.0, cost_out_per_1k=0.0)

    def complete(self, req: GenerationRequest, api_key: str | None, base_url: str | None) -> GenerationResponse:
        url = self._url(base_url)
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                r = client.post(url, json=self._payload(req), headers=self._headers(api_key))
                if r.status_code >= 400:
                    raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} réseau : {e}") from e
        text = ""
        choices = data.get("choices") or []
        if choices:
            text = choices[0].get("message", {}).get("content", "") or ""
        usage = data.get("usage", {}) or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        model_info = self._find_model(req.model)
        cost = self._compute_cost(tokens_in, tokens_out, model_info)
        return GenerationResponse(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, model=req.model,
        )

    def list_models(self, api_key: str | None, base_url: str | None) -> list[ModelInfo]:
        """Enrichit le catalogue statique via GET /models si l'API le permet. Fallback : self.models."""
        base = base_url or self.default_base_url
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{base.rstrip('/')}/models", headers=self._headers(api_key))
                if r.status_code < 400:
                    remote = {m.get("id"): True for m in (r.json().get("data") or [])}
                    # Marque les modèles statiques comme disponibles remotly + ajoute les inconnus.
                    out = []
                    for m in self.models:
                        out.append(ModelInfo(m.name, m.context_window, m.cost_in_per_1k, m.cost_out_per_1k,
                                             m.label or m.name))
                    for rid in remote:
                        if rid not in {m.name for m in self.models}:
                            out.append(ModelInfo(rid, label=rid))
                    return out
        except (httpx.HTTPError, ValueError):
            pass
        return list(self.models)


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    default_base_url = "https://api.openai.com/v1"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("gpt-4o", 128000, 0.0025, 0.01, "GPT-4o"),
        ModelInfo("gpt-4o-mini", 128000, 0.00015, 0.0006, "GPT-4o mini"),
        ModelInfo("gpt-4-turbo", 128000, 0.01, 0.03, "GPT-4 Turbo"),
        ModelInfo("gpt-3.5-turbo", 16385, 0.0005, 0.0015, "GPT-3.5 Turbo"),
    ]


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    default_base_url = "https://api.deepseek.com/v1"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("deepseek-chat", 64000, 0.00014, 0.00028, "DeepSeek Chat"),
        ModelInfo("deepseek-reasoner", 64000, 0.00055, 0.0022, "DeepSeek Reasoner"),
    ]


class GLMProvider(OpenAICompatibleProvider):
    name = "glm"
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("glm-4-plus", 128000, 0.005, 0.005, "GLM-4 Plus"),
        ModelInfo("glm-4-flash", 128000, 0.0001, 0.0001, "GLM-4 Flash"),
        ModelInfo("glm-4-air", 128000, 0.001, 0.001, "GLM-4 Air"),
    ]


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter : agrégateur (accès à 100+ modèles via un seul endpoint). Modèles prefixés
    provider/model (openai/gpt-4o, anthropic/claude-3.5-sonnet). Cost via API /key/api-credits."""
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("openai/gpt-4o-mini", 128000, 0.00015, 0.0006, "GPT-4o mini (via OpenRouter)"),
        ModelInfo("anthropic/claude-3.5-sonnet", 200000, 0.003, 0.015, "Claude 3.5 Sonnet (via OpenRouter)"),
        ModelInfo("google/gemini-flash-1.5", 1000000, 0.0000375, 0.00015, "Gemini Flash (via OpenRouter)"),
    ]

    def _headers(self, api_key: str | None) -> dict:
        h = super()._headers(api_key)
        h["HTTP-Referer"] = os.getenv("AI_GATEWAY_OPENROUTER_REFERER", "https://revpulse.app")
        h["X-Title"] = "RevPulse"
        return h


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama : modèles LOCAUX (localhost:11434). Cost=0 (local). api_key généralement null.
    base_url override obligatoire (http://localhost:11434/v1)."""
    name = "ollama"
    default_base_url = "http://localhost:11434/v1"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("llama3.2", 128000, 0.0, 0.0, "Llama 3.2 (local)"),
        ModelInfo("qwen2.5", 128000, 0.0, 0.0, "Qwen 2.5 (local)"),
        ModelInfo("mistral", 32000, 0.0, 0.0, "Mistral (local)"),
    ]


# ─── Anthropic (format /v1/messages) ─────────────────────────────────────────


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("claude-3-5-sonnet-20241022", 200000, 0.003, 0.015, "Claude 3.5 Sonnet"),
        ModelInfo("claude-3-5-haiku-20241022", 200000, 0.0008, 0.004, "Claude 3.5 Haiku"),
        ModelInfo("claude-3-opus-20240229", 200000, 0.015, 0.075, "Claude 3 Opus"),
    ]

    def complete(self, req: GenerationRequest, api_key: str | None, base_url: str | None) -> GenerationResponse:
        base = (base_url or self.default_base_url).rstrip("/")
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": "user", "content": req.prompt}],
            "temperature": req.temperature,
        }
        if req.system:
            payload["system"] = req.system
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                r = client.post(url, json=payload, headers=headers)
                if r.status_code >= 400:
                    raise ProviderError(f"anthropic HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic réseau : {e}") from e
        # Anthropic : content = [{type: text, text: ...}]
        text = ""
        content = data.get("content") or []
        if content and isinstance(content, list):
            text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
        usage = data.get("usage", {}) or {}
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        model_info = next((m for m in self.models if m.name == req.model),
                          ModelInfo(req.model, cost_in_per_1k=0.0, cost_out_per_1k=0.0))
        cost = self._compute_cost(tokens_in, tokens_out, model_info)
        return GenerationResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out,
                                  cost_usd=cost, model=req.model)


# ─── Google Gemini (format /v1beta/models/{model}:generateContent) ───────────


class GeminiProvider(BaseProvider):
    name = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com"
    models: ClassVar[list[ModelInfo]] = [
        ModelInfo("gemini-1.5-pro", 2000000, 0.00125, 0.005, "Gemini 1.5 Pro"),
        ModelInfo("gemini-1.5-flash", 1000000, 0.0000375, 0.00015, "Gemini 1.5 Flash"),
        ModelInfo("gemini-2.0-flash", 1000000, 0.0001, 0.0004, "Gemini 2.0 Flash"),
    ]

    def complete(self, req: GenerationRequest, api_key: str | None, base_url: str | None) -> GenerationResponse:
        base = (base_url or self.default_base_url).rstrip("/")
        url = f"{base}/v1beta/models/{req.model}:generateContent?key={api_key or ''}"
        payload = {
            "contents": [{"parts": [{"text": req.prompt}]}],
            "generationConfig": {"maxOutputTokens": req.max_tokens, "temperature": req.temperature},
        }
        if req.system:
            payload["systemInstruction"] = {"parts": [{"text": req.system}]}
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                r = client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if r.status_code >= 400:
                    raise ProviderError(f"gemini HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini réseau : {e}") from e
        text = ""
        candidates = data.get("candidates") or []
        if candidates:
            parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {}) or {}
        tokens_in = int(usage.get("promptTokenCount", 0))
        tokens_out = int(usage.get("candidatesTokenCount", 0))
        model_info = next((m for m in self.models if m.name == req.model),
                          ModelInfo(req.model, cost_in_per_1k=0.0, cost_out_per_1k=0.0))
        cost = self._compute_cost(tokens_in, tokens_out, model_info)
        return GenerationResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out,
                                  cost_usd=cost, model=req.model)


# ─── Simulated (tests — jamais présenté comme réel) ──────────────────────────


class SimulatedProvider(BaseProvider):
    """Provider simulé pour les tests + le mode dry_run (P24-B). simulated=True dans la réponse →
    le gateway + l'UI DOIVENT marquer la sortie comme simulée (jamais présentée comme une réponse
    réelle d'un fournisseur). Retourne une canned response déterministe (basée sur le prompt hash)
    pour des tests reproductibles."""
    name = "simulated"
    models: ClassVar[list[ModelInfo]] = [ModelInfo("simulated-1", 8000, 0.0, 0.0, "Simulated (tests)")]

    def complete(self, req: GenerationRequest, api_key: str | None, base_url: str | None) -> GenerationResponse:
        # Réponse déterministe (reproductible en tests) — reflète le prompt sans appeler de provider.
        time.sleep(0)  # no-op (latency simulée par le gateway si besoin)
        text = (
            f"[SIMULÉ — pas un vrai provider] Génération simulée pour le modèle {req.model} "
            f"(prompt: {req.prompt[:80]}…). En production, configurez un provider (OpenAI/Anthropic/"
            f"Gemini/DeepSeek/GLM/OpenRouter/Ollama) via POST /api/tenants/{{tid}}/ai/providers."
        )
        tokens_in = max(1, len(req.prompt) // 4)  # approximation 4 chars ≈ 1 token
        tokens_out = max(1, len(text) // 4)
        return GenerationResponse(
            text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=0.0,
            model=req.model, simulated=True,
        )


# ─── Registry : provider name → instance ─────────────────────────────────────

PROVIDERS: dict[str, BaseProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
    "deepseek": DeepSeekProvider(),
    "glm": GLMProvider(),
    "openrouter": OpenRouterProvider(),
    "ollama": OllamaProvider(),
    "simulated": SimulatedProvider(),
}


def get_provider(name: str) -> BaseProvider | None:
    """Retourne l'instance provider par nom. None si inconnu (le gateway skippe)."""
    return PROVIDERS.get(name)


SUPPORTED_PROVIDERS = sorted(PROVIDERS.keys())
