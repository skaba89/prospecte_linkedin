"""Tests P5 : Prometheus /metrics — format, compteur incrémenté, labels route-template, no-auth.

Autoporté (lit BACKEND_URL, défaut http://127.0.0.1:8000). Exit 0 si tout passe.
Lancé en CI après test_audit.py (serveur déjà up). /metrics est public (scraped par
Prometheus) — vérifie aussi l'exclusion du compteur (pas de boucle de scrapping).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
passed = failed = 0


def wait_health(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def raw_get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body_txt = r.read().decode()
            return r.status, json.loads(body_txt) if body_txt else None, dict(r.headers)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        try:
            js = json.loads(body_txt) if body_txt else None
        except Exception:
            js = None
        js = js or {}
        js["_status"] = e.code
        return e.code, js, dict(e.headers)


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label} ({got})")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def count_route(metrics_text, route):
    """Somme des compteurs revpulse_http_requests_total pour un route donné."""
    total = 0
    for m in re.finditer(r'revpulse_http_requests_total\{([^}]*)\}\s+(\d+)', metrics_text):
        labels, val = m.group(1), int(m.group(2))
        if f'route="{route}"' in labels:
            total += val
    return total


if not wait_health():
    print(f"IMPOSSIBLE DE JOINDRE {BASE}/api/health (timeout).")
    sys.exit(2)

print(f"Backend reachable at {BASE}\n")

# --- 1. /metrics format ---
print("=== 1. GET /metrics — format ===")
s, body, h = raw_get("/metrics")
ck(s, 200, "GET /metrics -> 200 (public, no auth)")
ct = (h.get("Content-Type") or h.get("content-type") or "")
ck("text/plain" in ct, True, f"Content-Type text/plain ({ct.split(';')[0]})")
ck("revpulse_http_requests_total" in body, True, "counter revpulse_http_requests_total présent")
ck("revpulse_http_request_duration_seconds" in body, True, "histogram latence présent")

# --- 2. Compteur incrémenté sur /api/health ---
print("\n=== 2. Compteur incrémenté après requêtes /api/health ===")
c0 = count_route(body, "/api/health")
for _ in range(3):
    raw_get("/api/health")
s, body2, _ = raw_get("/metrics")
c1 = count_route(body2, "/api/health")
ck(c1, c0 + 3, f"counter /api/health +3 ({c0} → {c1})")

# --- 3. Labels route-template (cardinalité contrôlée) ---
print("\n=== 3. Labels route = template (pas d'explosion cardinalité) ===")
s, dem, _ = call("POST", "/api/auth/demo")
tok = dem.get("token", "")
ck(bool(tok), True, "demo token obtenu")
s, _, _ = call("GET", "/api/tenants/tnt-demo/audit", tok)
ck(s, 200, "GET /audit (Owner) -> 200")
s, body3, _ = raw_get("/metrics")
ck('route="/api/tenants/{tid}/audit"' in body3, True, "label route = template {tid}")
ck('route="/api/tenants/tnt-demo/audit"' not in body3, True,
   "pas de label raw tid (cardinalité contrôlée)")

# --- 4. Labels status (200 + 401) ---
print("\n=== 4. Labels status présents ===")
call("GET", "/api/tenants/tnt-demo/audit")  # no token -> 401
s, body4, _ = raw_get("/metrics")
ck('status="200"' in body4, True, "label status=200 présent")
ck('status="401"' in body4, True, "label status=401 présent (après req sans token)")

# --- 5. /metrics exclu du compteur (pas de boucle) ---
print("\n=== 5. /metrics exclu du compteur (pas de boucle de scrapping) ===")
ck('route="/metrics"' not in body4, True, "/metrics absent des compteurs (exclu)")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
