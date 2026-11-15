"""Prometheus metrics : compteurs de requêtes + latence, exposés sur /metrics.

Middleware Starlette léger (prometheus-client) :
  - Counter `revpulse_http_requests_total{method,route,status}`
  - Histogram `revpulse_http_request_duration_seconds{method,route}`

Le **route-template** (`/api/tenants/{tid}/audit`) est utilisé comme label au lieu
du path brut (`/api/tenants/tnt-demo/audit`) pour éviter l'explosion de cardinalité
(sinon chaque tid/aid/user créerait une nouvelle série temporelle — famine mémoire
Prometheus). `/metrics` est exclu du compteur (pas de boucle de scrapping).
"""
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "revpulse_http_requests_total",
    "Total HTTP requests by method/route/status.",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "revpulse_http_request_duration_seconds",
    "HTTP request latency in seconds by method/route.",
    ["method", "route"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        path = request.url.path
        if path == "/metrics":
            return response  # exclure /metrics du compteur (pas de boucle de scrapping)
        # route-template : collapse {tid}/{aid}/{mid}… pour limiter la cardinalité
        route = request.scope.get("route")
        tmpl = getattr(route, "path", None) or path
        status = str(response.status_code)
        try:
            REQUEST_COUNT.labels(request.method, tmpl, status).inc()
            REQUEST_LATENCY.labels(request.method, tmpl).observe(elapsed)
        except ValueError:
            # labels inattendus (rare) — ne jamais casser la requête
            pass
        return response


def metrics_response() -> Response:
    """Réponse Prometheus (texte, format expositions standard)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
