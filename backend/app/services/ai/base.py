"""P24-B — Interfaces provider IA (BaseProvider) + dataclasses de requête/réponse.

Tous les providers (OpenAI, Anthropic, Gemini, DeepSeek, GLM, OpenRouter, Ollama, Simulated)
implémentent BaseProvider. Le gateway appelle provider.complete(req, api_key, base_url) →
GenerationResponse (text + tokens + cost + simulated). provider.list_models(api_key, base_url) →
liste ModelInfo (pour la sélection dynamique côté UI).

Coûts : ModelInfo porte cost_in_per_1k + cost_out_per_1k (USD). Le gateway calcule
cost_usd = (tokens_in/1000 * cost_in) + (tokens_out/1000 * cost_out). Pour Ollama (local), cost=0.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class ModelInfo:
    """Métadonnées d'un modèle IA (P24-B). name = id API (gpt-4o, claude-3-5-sonnet, gemini-1.5-pro).
    context_window = max tokens. cost_in_per_1k / cost_out_per_1k = USD par 1K tokens (0 pour local)."""
    name: str
    context_window: int = 8000
    cost_in_per_1k: float = 0.0
    cost_out_per_1k: float = 0.0
    label: str = ""


@dataclass
class GenerationRequest:
    """Requête de génération (P24-B). model = id API. prompt = instruction utilisateur rendue
    (APRÈS PII redact). system = instruction système optionnelle. max_tokens/temperature = params."""
    model: str
    prompt: str
    system: str | None = None
    max_tokens: int = 1000
    temperature: float = 0.7


@dataclass
class GenerationResponse:
    """Réponse d'une génération (P24-B). text = sortie générée. tokens_in/out = usage. cost_usd =
    coût calculé. simulated=True → provider simulé (jamais présenté comme réel — anti-fake)."""
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""
    simulated: bool = False
    error: str | None = field(default=None, repr=False)


class BaseProvider(ABC):
    """Interface provider IA (P24-B). Implémentée par OpenAI/Anthropic/Gemini/DeepSeek/GLM/
    OpenRouter/Ollama + SimulatedProvider (tests). complete() = génération synchrone (streaming
    géré séparément par le gateway via stream_chunk si activé). list_models() = catalogue du
    provider (pour la sélection dynamique)."""

    name: str = ""  # "openai", "anthropic", "gemini", "deepseek", "glm", "openrouter", "ollama"
    default_base_url: str = ""
    # Catalogue de modèles connu (coûts + context window). list_models() peut enrichir via API.
    models: ClassVar[list[ModelInfo]] = []

    @abstractmethod
    def complete(self, req: GenerationRequest, api_key: str | None, base_url: str | None) -> GenerationResponse:
        """Génère une complétion synchrone. Lève ProviderError en cas d'échec (le gateway catch + fallback)."""
        ...

    def list_models(self, api_key: str | None, base_url: str | None) -> list[ModelInfo]:
        """Retourne le catalogue de modèles connu du provider (default : self.models statique).
        Les providers avec une API /models peuvent surcharger pour enrichir dynamiquement."""
        return list(self.models)

    @staticmethod
    def _compute_cost(tokens_in: int, tokens_out: int, model: ModelInfo) -> float:
        """Cost USD = (tokens_in/1000 * cost_in) + (tokens_out/1000 * cost_out). 0 pour local (Ollama)."""
        return round((tokens_in / 1000.0) * model.cost_in_per_1k + (tokens_out / 1000.0) * model.cost_out_per_1k, 6)


class ProviderError(Exception):
    """Erreur provider IA (P24-B). Le gateway catch + fallback vers le provider suivant (priority)."""
    pass
