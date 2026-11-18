"""Logging structuré + middleware request-ID.

- configure_logging(level, fmt) : formateur JSON (prod) ou texte lisible (dev/test).
  Le JSON inclut ts/level/logger/msg + request_id/user_id/tenant_id (contextvars)
  + les champs extra du log (method/path/status/duration_ms).
- RequestLoggingMiddleware : génère/propage un request_id (header X-Request-ID
  entrant sinon uuid4), le met en contextvar (visible par les logs métier via
  héritage parent→enfant), logge chaque requête (method/path/status/duration_ms)
  et renvoie X-Request-ID en réponse.
- user_id_var / tenant_id_var : positionnés par les deps auth (get_current_user /
  get_tenant) pour que les logs métier incluent le contexte utilisateur/tenant.
"""
import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")
# P48 : IP + User-Agent capturés par le middleware → pick automatique par audit.log_audit()
# (sans toucher aux 17 routers déjà instrumentés). Requis SOC2/GDPR (traçabilité accès).
request_ip_var: ContextVar[str] = ContextVar("request_ip", default="")
request_ua_var: ContextVar[str] = ContextVar("request_ua", default="")

log = logging.getLogger("revpulse")

# Attributs standards d'un LogRecord (à exclure des extras pour ne pas polluer le JSON).
_STD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "getMessage", "message",
}


class JsonFormatter(logging.Formatter):
    """Formateur JSON : un objet par ligne, parsable par un agrégateur de logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        uid = user_id_var.get()
        if uid:
            payload["user_id"] = uid
        tid = tenant_id_var.get()
        if tid:
            payload["tenant_id"] = tid
        # Champs extra arbitraires (method, path, status, duration_ms...) du log.
        for k, v in vars(record).items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure le logger root : JSON (prod) ou texte (dev/test). Idempotent."""
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logge chaque requête (method/path/status/duration_ms) + propage X-Request-ID."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        # P48 : capture IP (X-Forwarded-For si proxy, sinon client.host) + User-Agent pour audit log.
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")
        ua = request.headers.get("user-agent", "")
        token_ip = request_ip_var.set(ip)
        token_ua = request_ua_var.set(ua)
        start = time.perf_counter()
        status_code = 0
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            dur_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info("request",
                     extra={"method": request.method, "path": request.url.path,
                            "status": status_code, "duration_ms": dur_ms})
            request_id_var.reset(token)
            request_ip_var.reset(token_ip)
            request_ua_var.reset(token_ua)
