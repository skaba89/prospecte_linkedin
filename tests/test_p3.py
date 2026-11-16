"""Tests P3 : pagination (skip/limit + X-Total-Count), request-ID (X-Request-ID),
health approfondi (db_ok via SELECT 1). Autoporté (lit BACKEND_URL, défaut
http://127.0.0.1:8000). Exit 0 si tout passe. Lancé en CI après test_api.py
(serveur déjà up) — robuste à l'ordre d'exécution (assertions en ≥, pas en =).
"""
import json
import os
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


def hget(headers, name):
    """Lookup d'en-tête insensible à la casse (urllib normalise parfois la casse)."""
    name_l = name.lower()
    for k, v in headers.items():
        if k.lower() == name_l:
            return v
    return None


def call(method, path, token=None, body=None, extra_headers=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body_txt = r.read().decode()
            js = json.loads(body_txt) if body_txt else None
            return r.status, js, dict(r.headers)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        try:
            js = json.loads(body_txt) if body_txt else None
        except Exception:
            js = None
        return e.code, js, dict(e.headers)
    except Exception as e:
        return 0, {"error": str(e)}, {}


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label} ({got})")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


if not wait_health():
    print(f"IMPOSSIBLE DE JOINDRE {BASE}/api/health (timeout). Lancez d'abord la stack.")
    sys.exit(2)

print(f"Backend reachable at {BASE}\n")

# --- 1. Health approfondi (SELECT 1 réel) ---
print("=== 1. Health approfondi (db_ok + SELECT 1) ===")
s, h, _ = call("GET", "/api/health")
ck(s, 200, "health -> 200 (DB up)")
ck(h.get("status"), "ok", "status == ok")
ck(h.get("db_ok"), True, "db_ok == True (SELECT 1 réussi)")
ck(h.get("db") != "", True, "champ db (nom) conservé (rétro-compat)")

# --- login démo (bucket /demo dédié, non saturé par test_api) ---
s, dem, _ = call("POST", "/api/auth/demo")
tok = dem.get("token", "")
ck(bool(tok), True, "demo token obtenu")

# --- 2. Request-ID (X-Request-ID) ---
print("\n=== 2. Request-ID (X-Request-ID sur chaque réponse) ===")
s, _, hd = call("GET", "/api/auth/me", tok)
rid = hget(hd, "X-Request-Id")
ck(bool(rid), True, "X-Request-ID présent (auto-généré)")

CUSTOM = "p3-reqid-abcd1234"
s, _, hd2 = call("GET", "/api/auth/me", tok, extra_headers={"X-Request-ID": CUSTOM})
ck(hget(hd2, "X-Request-Id"), CUSTOM, "X-Request-ID fourni est echoé en réponse")

# X-Request-ID présent même sur un 401 (pas de token)
s, _, hd3 = call("GET", "/api/auth/me")
ck(bool(hget(hd3, "X-Request-Id")), True, "X-Request-ID présent sur 401")

# --- 3. Pagination /api/tenants ---
print("\n=== 3. Pagination /api/tenants (skip/limit + X-Total-Count) ===")
s, t1, h1 = call("GET", "/api/tenants?skip=0&limit=1", tok)
ck(s, 200, "tenants?limit=1 -> 200")
ck(len(t1), 1, "tenants?limit=1 -> exactement 1 item")
total = hget(h1, "X-Total-Count")
ck(bool(total), True, "X-Total-Count présent")
ck(int(total) >= 2, True, f"X-Total-Count >= 2 (full count, got {total})")

s, t2, _ = call("GET", "/api/tenants?skip=1&limit=100", tok)
ck(len(t2), int(total) - 1, "skip=1 renvoie total-1 items")

# sans params -> comportement défaut (limit=100), retourne tout (≤100)
s, tall, _ = call("GET", "/api/tenants", tok)
ck(len(tall), int(total), "sans params -> toutes les lignes (limit=100 défaut)")

# --- 4. Pagination /api/tenants/tnt-demo/members ---
print("\n=== 4. Pagination /api/tenants/tnt-demo/members ===")
s, m1, hm = call("GET", "/api/tenants/tnt-demo/members?limit=2", tok)
ck(len(m1), 2, "members?limit=2 -> 2 items")
mtotal = hget(hm, "X-Total-Count")
ck(int(mtotal) >= 4, True, f"members X-Total-Count >= 4 (got {mtotal})")

s, m2, _ = call("GET", "/api/tenants/tnt-demo/members?skip=100&limit=10", tok)
ck(len(m2), 0, "skip au-delà du total -> 0 item")

# --- 5. Pagination /api/tenants/tnt-demo/approvals ---
print("\n=== 5. Pagination /api/tenants/tnt-demo/approvals ===")
s, a1, ha = call("GET", "/api/tenants/tnt-demo/approvals?skip=0&limit=3", tok)
ck(len(a1) <= 3, True, "approvals?limit=3 -> ≤3 items")
atotal = hget(ha, "X-Total-Count")
ck(bool(atotal), True, "approvals X-Total-Count présent")

# --- 6. Validation du cap (limit > 200, limit <= 0) -> 422 ---
print("\n=== 6. Validation cap pagination -> 422 ===")
s, _, _ = call("GET", "/api/tenants?limit=999", tok)
ck(s, 422, "limit=999 rejeté (cap 200)")
s, _, _ = call("GET", "/api/tenants?limit=0", tok)
ck(s, 422, "limit=0 rejeté (ge=1)")
s, _, _ = call("GET", "/api/tenants?skip=-1", tok)
ck(s, 422, "skip=-1 rejeté (ge=0)")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
