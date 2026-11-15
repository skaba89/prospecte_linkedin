"""Extensions partagées (évite les imports circulaires main ↔ routers).

Le Limiter slowapi vit ici : il ne dépend que de la config ; main.py y branche
le middleware + le handler 429, les routers l'utilisent via @limiter.limit(...).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings

limiter = Limiter(key_func=get_remote_address, enabled=settings.rate_limit_enabled)
