"""Tests P4 : audit log — trace des actions mutantes, lecture Owner, isolation.

Autoporté (lit BACKEND_URL, défaut http://127.0.0.1:8000). Exit 0 si tout passe.
Lancé en CI après test_p3.py (serveur déjà up). Robuste à l'ordre d'exécution :
capture le total audit (T0) puis vérifie les incrémentations après chaque action.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

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
    name_l = name.lower()
    for k, v in headers.items():
        if k.lower() == name_l:
            return v
    return None


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

# --- login démo (Owner de tnt-demo) ---
s, dem, _ = call("POST", "/api/auth/demo")
tok = dem.get("token", "")
ck(bool(tok), True, "demo token obtenu (Owner tnt-demo)")

# --- 1. Lecture audit (Owner) — baseline ---
print("\n=== 1. Lecture audit (Owner) — baseline ===")
s, a0, h0 = call("GET", "/api/tenants/tnt-demo/audit", tok)
ck(s, 200, "GET /audit -> 200 (Owner)")
ck(isinstance(a0, list), True, "réponse = liste")
T0 = int(hget(h0, "X-Total-Count") or 0)
ck(T0 >= 0, True, f"X-Total-Count baseline = {T0}")

# --- 2. Approve → audit 'approval.approve' ---
print("\n=== 2. Approve → audit 'approval.approve' ===")
s, ap, _ = call("GET", "/api/tenants/tnt-demo/approvals", tok)
ck(len(ap) >= 1, True, "au moins 1 approbation pending disponible")
if len(ap) >= 1:
    aid = ap[0]["id"]
    s, _, _ = call("POST", f"/api/tenants/tnt-demo/approvals/{aid}/approve", tok)
    ck(s, 200, "approve -> 200")
    s, a1, h1 = call("GET", "/api/tenants/tnt-demo/audit", tok)
    T1 = int(hget(h1, "X-Total-Count") or 0)
    ck(T1, T0 + 1, "X-Total-Count incrémenté de 1 après approve")
    ck(a1[0]["action"], "approval.approve", "dernière entrée = approval.approve")
    ck(a1[0]["target_id"], aid, "target_id == aid approuvé")
    ck(bool(a1[0]["actor_name"]), True, "actor_name renseigné")
    ck(bool(a1[0]["request_id"]), True, "request_id corrélé (P3)")
else:
    T1 = T0
    print("  (skip: plus d'approbation pending)")

# --- 3. Reject → audit 'approval.reject' ---
print("\n=== 3. Reject → audit 'approval.reject' ===")
s, ap2, _ = call("GET", "/api/tenants/tnt-demo/approvals", tok)
if len(ap2) >= 1:
    aid2 = ap2[0]["id"]
    s, _, _ = call("POST", f"/api/tenants/tnt-demo/approvals/{aid2}/reject", tok)
    ck(s, 200, "reject -> 200")
    s, a2, h2 = call("GET", "/api/tenants/tnt-demo/audit", tok)
    T2 = int(hget(h2, "X-Total-Count") or 0)
    ck(T2, T1 + 1, "X-Total-Count incrémenté après reject")
    ck(a2[0]["action"], "approval.reject", "dernière entrée = approval.reject")
    ck(a2[0]["target_id"], aid2, "target_id == aid rejeté")
else:
    T2 = T1
    print("  (skip: plus d'approbation pending à refuser)")

# --- 4. Invite member → audit 'member.invite' ---
print("\n=== 4. Invite member → audit 'member.invite' ===")
email = f"audit-test-{int(time.time())}@example.com"
s, inv, _ = call("POST", "/api/tenants/tnt-demo/members/invite", tok,
                 body={"email": email, "role": "Viewer", "name": "Audit Test"})
ck(s, 201, "invite -> 201")
mid = inv.get("id", "")
s, a3, h3 = call("GET", "/api/tenants/tnt-demo/audit", tok)
ck(int(hget(h3, "X-Total-Count") or 0), T2 + 1, "X-Total-Count incrémenté après invite")
ck(a3[0]["action"], "member.invite", "dernière entrée = member.invite")
ck(a3[0]["target_id"], mid, "target_id == mid invité")

# --- 5. Pagination audit ---
print("\n=== 5. Pagination audit ===")
s, apg, hpg = call("GET", "/api/tenants/tnt-demo/audit?limit=1", tok)
ck(len(apg), 1, "?limit=1 -> 1 item")
ck(int(hget(hpg, "X-Total-Count") or 0) >= 3, True, "X-Total-Count >= 3 (total cumulé)")
s, _, _ = call("GET", "/api/tenants/tnt-demo/audit?limit=999", tok)
ck(s, 422, "cap pagination 200 appliqué sur /audit")

# --- 6. Isolation : nouvel user (non-membre de tnt-demo) -> 403 ---
print("\n=== 6. Isolation : non-membre -> 403 sur /audit ===")
s, reg, _ = call("POST", "/api/auth/register",
                 body={"name": "Iso Test", "email": f"iso-{int(time.time())}@x.com", "password": "iso1234"})
ck(bool(reg.get("token")), True, "register nouvel user -> token")
s, _, _ = call("GET", "/api/tenants/tnt-demo/audit", reg.get("token"))
ck(s, 403, "non-membre de tnt-demo -> 403 (isolation tenant)")

# --- 7. 401 sans token ---
s, _, _ = call("GET", "/api/tenants/tnt-demo/audit")
ck(s, 401, "sans token -> 401")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
