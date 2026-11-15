"""Test bout-en-bout de l'API RevPulse — autoporté (lit BACKEND_URL, défaut http://127.0.0.1:8000).

Usage :
    BACKEND_URL=http://localhost:8000 python tests/test_api.py

Le script attend que /api/health réponde (jusqu'à ~30s) puis enchaîne 16 scénarios
couvrant auth, multi-tenant, members + quota, approvals, dashboard, billing, RBAC.
Exit code 0 si tout passe, 1 sinon.
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


def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


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

print("=== 1. POST /api/auth/demo ===")
s, dem = call("POST", "/api/auth/demo")
tok = dem.get("token", "")
ck(dem.get("user", {}).get("email"), "demo@revpulse.io", "demo login user")
ck(bool(tok), True, "token returned")

print("=== 2. GET /api/auth/me ===")
s, me = call("GET", "/api/auth/me", tok)
ck(me.get("name"), "Alex Démo", "me name")

print("=== 3. GET /api/tenants ===")
s, tn = call("GET", "/api/tenants", tok)
ck(len(tn), 2, "tenant count (RevPulse Demo + Acme)")
ck(tn[0]["name"], "RevPulse Demo", "first tenant")
ck(tn[0]["profile"]["type"], "linkedin", "profile type")
ck(tn[0]["member_count"], 4, "member count")

print("=== 4. GET /api/tenants/tnt-demo/members ===")
s, mb = call("GET", "/api/tenants/tnt-demo/members", tok)
ck(len(mb), 4, "members (1 owner + 2 active + 1 invited)")
ck(sum(1 for m in mb if m["status"] == "invited"), 1, "invited count (Inès)")

print("=== 5. GET /api/tenants/tnt-demo/approvals ===")
s, ap = call("GET", "/api/tenants/tnt-demo/approvals", tok)
ck(len(ap), 6, "pending approvals")
aid = ap[0]["id"]

print(f"=== 6. POST /api/tenants/tnt-demo/approvals/{aid}/approve ===")
call("POST", f"/api/tenants/tnt-demo/approvals/{aid}/approve", tok)
s, ap2 = call("GET", "/api/tenants/tnt-demo/approvals", tok)
ck(len(ap2), 5, "approvals after approve (decremented)")

print("=== 7. GET /api/tenants/tnt-demo/dashboard/modules ===")
s, mod = call("GET", "/api/tenants/tnt-demo/dashboard/modules", tok)
ck(len(mod), 34, "dashboard modules served")
ck("team_workspace" in mod, True, "team_workspace module present")

print("=== 8. GET /api/tenants/tnt-demo/billing ===")
s, bl = call("GET", "/api/tenants/tnt-demo/billing", tok)
ck(bl["plan"], "Pro", "plan")
ck(bl["seats"], 10, "seats (Pro=10)")

print("=== 9. POST invite (new member) ===")
s, inv = call("POST", "/api/tenants/tnt-demo/members/invite", tok,
              {"email": "marc@test.io", "name": "Marc Test", "role": "Contributor"})
ck(inv.get("status"), "invited", "invite status")
ck(bool(inv.get("token")), True, "invite token generated")

print("=== 10. POST profile (onboarding) ===")
s, nt = call("POST", "/api/tenants", tok, {"name": "Test Co", "plan": "Starter"})
ck(s, 201, "create tenant for profile test")
s, prof = call("POST", f"/api/tenants/{nt['id']}/profile", tok,
               {"type": "native", "name": "Test User", "headline": "Founder",
                "company": "Test Co", "followers": 100, "initials": "TU"})
ck(prof.get("name"), "Test User", "profile created")

print("=== 11. PATCH rename tenant ===")
s, rn = call("PATCH", "/api/tenants/tnt-demo", tok, {"name": "RevPulse Pro Demo"})
ck(rn.get("name"), "RevPulse Pro Demo", "renamed")

print("=== 12. PATCH billing → Enterprise ===")
s, cp = call("PATCH", "/api/tenants/tnt-demo/billing", tok, {"plan": "Enterprise"})
ck(cp.get("plan"), "Enterprise", "plan changed")

print("=== 13. register + login (new user) ===")
s, reg = call("POST", "/api/auth/register", body={"name": "Pat Test", "email": "pat@test.io", "password": "pass1234"})
ck(reg.get("user", {}).get("email"), "pat@test.io", "register")
ptok = reg.get("token", "")
s, log = call("POST", "/api/auth/login", body={"email": "pat@test.io", "password": "pass1234"})
ck(bool(log.get("token")), True, "login new user")

print("=== 14. RBAC isolation: pat (non-member) → 403 on tnt-demo ===")
s, _ = call("GET", "/api/tenants/tnt-demo", ptok)
ck(s, 403, "tenant isolation (non-member blocked)")

print("=== 15. wrong password → 401 ===")
s, _ = call("POST", "/api/auth/login", body={"email": "demo@revpulse.io", "password": "wrong"})
ck(s, 401, "wrong password rejected")

print("=== 16. DELETE tenant tnt-acme ===")
s, dele = call("DELETE", "/api/tenants/tnt-acme", tok)
ck("deleted" in dele, True, "delete acme")
s, tn2 = call("GET", "/api/tenants", tok)
ck(len(tn2), 2, "tenants after delete (Test Co remains)")

print("=== 17. quota enforcement: downgrade blocked ===")
s, _ = call("PATCH", "/api/tenants/tnt-demo/billing", tok, {"plan": "Starter"})
ck(s, 409, "downgrade blocked when members > seats")

print("=== 18. POST /content (authoring → file d'approbation) ===")
s, created = call("POST", "/api/tenants/tnt-demo/content", tok,
                  {"type": "post", "title": "Post test auto", "content_body": "Bonjour LinkedIn ! #test",
                   "priority": "high", "submitter": "Test Bot"})
ck(s, 201, "create → 201")
ck(created.get("status"), "pending", "nouveau contenu en pending")
ck(created.get("content_body"), "Bonjour LinkedIn ! #test", "content_body stocké")
cid = created.get("id", "")
s, ap3 = call("GET", "/api/tenants/tnt-demo/approvals", tok)
ck(len(ap3), 6, "pending après create (5 seed + 1 nouveau)")

print("=== 19. POST /approvals/{aid}/reject + GET /history ===")
s, rej = call("POST", f"/api/tenants/tnt-demo/approvals/{cid}/reject", tok)
ck(rej.get("rejected"), cid, "reject renvoie l'id")
s, ap4 = call("GET", "/api/tenants/tnt-demo/approvals", tok)
ck(len(ap4), 5, "pending après reject (-1)")
s, hist = call("GET", "/api/tenants/tnt-demo/approvals/history", tok)
ck(len(hist), 2, "history = 1 approuvé (test 6) + 1 refusé")
ck(sum(1 for h in hist if h["status"] == "rejected"), 1, "history contient 1 rejected")
ck(sum(1 for h in hist if h["status"] == "approved"), 1, "history contient 1 approved")

print("=== 20. LinkedIn status (mock, avant connexion) ===")
s, lk = call("GET", "/api/tenants/tnt-demo/linkedin/status", tok)
ck(lk.get("connected"), False, "linkedin non connecté")
ck(lk.get("mock"), True, "mode mock actif (pas de creds)")

print("=== 21. LinkedIn auth (mock → mock-connect) ===")
s, lkauth = call("GET", "/api/tenants/tnt-demo/linkedin/auth", tok)
ck(lkauth.get("mock"), True, "auth mock=true")
ck("mock-connect" in lkauth.get("auth_url", ""), True, "auth_url → mock-connect")

print("=== 22. LinkedIn mock-connect (crée connexion factice) + status ===")
s, _ = call("GET", "/api/tenants/tnt-demo/linkedin/mock-connect")  # redirect navigateur, pas d'auth
s, lk2 = call("GET", "/api/tenants/tnt-demo/linkedin/status", tok)
ck(lk2.get("connected"), True, "linkedin connecté (mock)")
ck("mock" in lk2.get("member_name", "").lower(), True, "member_name = profil mock")

print("=== 23. LinkedIn publish (mock, sur contenu approuvé du test 6) ===")
s, pub = call("POST", "/api/tenants/tnt-demo/linkedin/publish?aid=" + aid, tok)
ck(pub.get("published"), True, "publish mock success")
ck(pub.get("post_urn", "").startswith("urn:li:ugcPost:mock"), True, "URN mock retourné")

print("=== 24. LinkedIn publish sans connexion (autre tenant) → 409 ===")
s, tn3 = call("GET", "/api/tenants", tok)
other_tid = [t["id"] for t in tn3 if t["id"] != "tnt-demo"][0]
s, _ = call("POST", f"/api/tenants/{other_tid}/linkedin/publish?aid={aid}", tok)
ck(s, 409, "publish sans connexion LinkedIn → 409")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
