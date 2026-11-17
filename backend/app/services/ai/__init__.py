"""P24-B — AI Gateway multi-provider package.

Indépendant des fournisseurs : OpenAI, Anthropic, Google Gemini, DeepSeek, GLM, OpenRouter, Ollama.
Gateway orchestre la sélection provider (priority), le fallback automatique, les quotas/coûts,
l'historisation, le chiffrement des clés API (services/crypto), la protection PII + la défense
anti-injection. SimulatedProvider pour les tests (jamais présenté comme réel).

Submodules :
  - base.py : interfaces (BaseProvider, GenerationRequest/Response, ModelInfo).
  - providers.py : implémentations concrètes (httpx REST, pas de SDK lourds).
  - safety.py : redact_pii (emails/phones/cards) + detect_injection (anti prompt-injection).
  - gateway.py : generate() — orchestration complète (prompt rendu → PII → injection check →
    provider select + fallback → quota check → historisation → return).
"""
