"""Test bout-en-bout de l'API RevPulse — autoporté (lit BACKEND_URL, défaut http://127.0.0.1:8000).

Usage :
    BACKEND_URL=http://localhost:8000 python tests/test_api.py

Le script attend que /api/health réponde (jusqu'à ~30s) puis enchaîne 25 scénarios
couvrant auth, multi-tenant, members + quota, approvals, dashboard, billing, RBAC,
authoring/reject/history, LinkedIn mock, invitation SMTP (dry-run) + rate-limit login.
Exit code 0 si tout passe, 1 sinon.
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


def call_upload(path, token, filename, content, content_type="text/csv"):
    """Upload multipart/form-data (pour /crm/contacts/import). Retourne (status, json)."""
    boundary = "----revpulse" + str(int(time.time() * 1000))
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--\r\n").encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


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
# P10.10 : publier un approval type "post" (les dm/comment routent vers manual_required).
aid = next(a["id"] for a in ap if a.get("type") == "post")

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
ck(inv.get("email_sent"), True, "invite email_sent (dry-run SMTP)")
ck(bool(inv.get("expires_at")), True, "invite expires_at set (7j)")

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

print("=== 25. Rate-limit login (anti brute-force → 429) ===")
# /login est limité à 5/min/IP (slowapi). Les tests 13+15 ont déjà fait 2 appels /login.
# On enchaîne des login erronés jusqu'à déclencher le 429 (au 6e appel cumulé).
got_429 = False
for _i in range(12):
    s, _ = call("POST", "/api/auth/login", body={"email": "brute@test.io", "password": "wrong"})
    if s == 429:
        got_429 = True
        break
ck(got_429, True, "rate-limit 429 déclenché")

# ─── P11 CRM (26-37) : contacts, pipeline, import, suppression, segments ─────
print("\n=== 26. GET /crm/contacts (seed démo) ===")
s, ct = call("GET", "/api/tenants/tnt-demo/crm/contacts", tok)
ck(s, 200, "list contacts CRM 200")
# X-Total-Count vient via l'en-tête — call() ne le retourne pas ; on compte la liste.
ck(len(ct), 4, "seed CRM contacts (Marie/Albert/Niels/Ada)")

print("=== 27. POST /crm/contacts (new, scoring) ===")
s, nc = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok,
             {"first_name": "E2E", "last_name": "Test", "email": "e2e.test@crm.io",
              "title": "CEO", "phone": "+33 1", "linkedin_url": "https://linkedin.com/in/e2e"})
ck(s, 201, "create contact CRM 201")
ck(nc.get("score"), 80, "score CEO+email+linkedin+senior+phone = 80")
ncid = nc.get("id", "")

print("=== 28. POST /crm/contacts (dedup → 409) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok, {"email": "e2e.test@crm.io"})
ck(s, 409, "dedup email → 409")
s, _ = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok, {"email": "E2E.TEST@CRM.IO"})
ck(s, 409, "dedup case-insensitive → 409")

print("=== 29. GET /crm/companies (seed 2) ===")
s, cp = call("GET", "/api/tenants/tnt-demo/crm/companies", tok)
ck(len(cp), 2, "seed companies (Acme + Globex)")

print("=== 30. GET /crm/opportunities/pipeline (6 colonnes Kanban) ===")
s, cols = call("GET", "/api/tenants/tnt-demo/crm/opportunities/pipeline", tok)
ck(len(cols), 6, "pipeline 6 stages")
stages = [c["stage"] for c in cols]
ck("prospecting" in stages and "closed_won" in stages and "closed_lost" in stages, True,
   "pipeline stages complets")

print("=== 31. PATCH /crm/opportunities/opp-1/stage (move Kanban) ===")
s, mv = call("PATCH", "/api/tenants/tnt-demo/crm/opportunities/opp-1/stage", tok, {"stage": "negotiation"})
ck(mv.get("stage"), "negotiation", "opp déplacée en negotiation")
s, _ = call("PATCH", "/api/tenants/tnt-demo/crm/opportunities/opp-1/stage", tok, {"stage": "invalid"})
ck(s, 400, "stage invalide → 400")

print("=== 32. POST /crm/contacts/import (CSV : dédup + suppression + erreur) ===")
csv_data = ("first_name,last_name,email,title,company,status\n"
            "Marie,X,marie.curie@acme.com,VP,Acme Corp,qualified\n"
            "New,E2E,new.e2e.import@crm.io,CTO,Globex,contacted\n"
            "Opt,Out,opt-out@example.com,Founder,,new\n"
            "Bad,Row,notanemail,Dir,,new\n")
s, res = call_upload("/api/tenants/tnt-demo/crm/contacts/import", tok, "test.csv", csv_data)
ck(s, 200, "import CSV 200")
ck(res.get("created"), 1, "import created 1 (new.e2e.import)")
ck(res.get("duplicates"), 1, "import dup 1 (marie.curie existe)")
ck(res.get("suppressed"), 1, "import suppressed 1 (opt-out RGPD)")
ck(res.get("total"), 4, "import total 4 lignes")

print("=== 33. GET /crm/contacts/export (CSV RGPD) ===")
# call() parse en JSON → utilise une requête brute pour vérifier le text/csv.
req = urllib.request.Request(BASE + "/api/tenants/tnt-demo/crm/contacts/export")
req.add_header("Authorization", f"Bearer {tok}")
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        exp_status = r.status
        exp_ct = r.headers.get("Content-Type", "")
        exp_body = r.read().decode()
        exp_has = "e2e.test@crm.io" in exp_body
except urllib.error.HTTPError as e:
    exp_status, exp_ct, exp_has = e.code, "", False
ck(exp_status, 200, "export CSV 200")
ck("text/csv" in exp_ct, True, "export content-type text/csv")
ck(exp_has, True, "export contient le contact créé")

print("=== 34. POST /crm/suppression (RGPD désinscription) ===")
s, sup = call("POST", "/api/tenants/tnt-demo/crm/suppression", tok,
              {"email": "unsubscribe.e2e@crm.io", "reason": "unsubscribe"})
ck(s, 201, "add suppression 201")
ck(sup.get("reason"), "unsubscribe", "suppression reason")
# idempotent : re-add met à jour la raison
s, sup2 = call("POST", "/api/tenants/tnt-demo/crm/suppression", tok,
               {"email": "unsubscribe.e2e@crm.io", "reason": "complaint"})
ck(sup2.get("reason"), "complaint", "suppression idempotent (reason updatée)")

print("=== 35. GET /crm/segments/seg-1/contacts (filtre sauvegardé) ===")
s, seg = call("GET", "/api/tenants/tnt-demo/crm/segments/seg-1/contacts", tok)
ck(s, 200, "apply segment 200")
ck(len(seg) >= 1, True, "segment 'Décideurs tech' matches ≥1")
ck(all("cto" in (m.get("title", "") + m.get("email", "")).lower() for m in seg), True,
   "tous les matchs du segment contiennent 'cto'")

print("=== 36. DELETE /crm/contacts/{id} (RGPD droit à l'oubli + cascade) ===")
s, dele = call("DELETE", f"/api/tenants/tnt-demo/crm/contacts/{ncid}", tok)
ck(s, 200, "delete contact 200")
ck(dele.get("deleted"), ncid, "deleted renvoie l'id")
s, _ = call("GET", f"/api/tenants/tnt-demo/crm/contacts/{ncid}", tok)
ck(s, 404, "contact supprimé → 404")

print("=== 37. CRM tenant isolation (non-member → 403) ===")
# pat@test.io (test 13) n'est membre que de son propre tenant → 403 sur tnt-demo CRM.
s, _ = call("GET", "/api/tenants/tnt-demo/crm/contacts", ptok)
ck(s, 403, "non-member → 403 sur CRM tnt-demo")

# ─── P12 LinkedIn → CRM sync (38-44) : mock-connect, sync, dédup, historique, 409, RBAC ─
print("=== 38. LinkedIn mock-connect (tnt-demo) ===")
# mock-connect n'a pas d'auth (P10) + redirige → call suit le redirect (HTML, non parsable).
# On l'invoque pour l'effet de bord (crée LinkedInConnection) puis on vérifie /status.
call("GET", "/api/tenants/tnt-demo/linkedin/mock-connect")
s, st = call("GET", "/api/tenants/tnt-demo/linkedin/status", tok)
ck(s, 200, "linkedin status 200")
ck(st.get("connected"), True, "linkedin connected après mock-connect")
ck(st.get("mock"), True, "status mock=True (mode dev sans creds)")

print("=== 39. POST /linkedin/sync-contacts (sync LinkedIn → CRM) ===")
s, res = call("POST", "/api/tenants/tnt-demo/linkedin/sync-contacts", tok)
ck(s, 200, "sync-contacts 200")
ck(res.get("status"), "completed", "sync status completed")
ck(res.get("total", 0) >= 8, True, f"sync total ≥8 ({res.get('total')})")
ck(res.get("imported", 0) >= 1, True, f"sync imported ≥1 ({res.get('imported')})")
ck(bool(res.get("sync_run_id")), True, "sync_run_id retourné (traçabilité)")

print("=== 40. GET /crm/contacts?source=linkedin (vérifie le sync → CRM) ===")
s, cs = call("GET", "/api/tenants/tnt-demo/crm/contacts?source=linkedin", tok)
ck(s, 200, "contacts source=linkedin 200")
ck(len(cs) >= 8, True, f"≥8 contacts source=linkedin ({len(cs)})")
ck(all(c.get("source") == "linkedin" for c in cs), True, "tous les contacts sont source=linkedin")
ck(any("cto" in c.get("title", "").lower() for c in cs), True,
   "≥1 contact CTO syncé (nourrit segment P11 'Décideurs tech')")
ck(any(c.get("linkedin_url", "").startswith("https://linkedin.com/in/") for c in cs), True,
   "contacts ont une linkedin_url")

print("=== 41. POST /linkedin/sync-contacts (idempotence — dédup par linkedin_url) ===")
s, res2 = call("POST", "/api/tenants/tnt-demo/linkedin/sync-contacts", tok)
ck(s, 200, "2e sync 200")
ck(res2.get("status"), "completed", "2e sync completed")
ck(res2.get("imported", -1) == 0, True, f"2e sync imported 0 (déjà tous là, {res2.get('imported')})")
ck(res2.get("updated", 0) >= 8, True, f"2e sync updated ≥8 (dédup, {res2.get('updated')})")

print("=== 42. GET /linkedin/sync-runs (historique paginé) ===")
s, runs = call("GET", "/api/tenants/tnt-demo/linkedin/sync-runs", tok)
ck(s, 200, "sync-runs 200")
ck(len(runs) >= 2, True, f"≥2 runs historisés ({len(runs)})")
ck(runs[0].get("status"), "completed", "dernier run completed")
ck(runs[0].get("total", 0) >= 8, True, "dernier run total ≥8")
ck(all("started_at" in r and "completed_at" in r for r in runs), True,
   "chaque run a started_at + completed_at")

print("=== 43. disconnect → sync → 409 (pas de connexion) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/linkedin/disconnect", tok)
ck(s, 200, "disconnect 200")
s, res3 = call("POST", "/api/tenants/tnt-demo/linkedin/sync-contacts", tok)
ck(s, 409, "sync après disconnect → 409 (pas de connexion)")
ck("connecté" in res3.get("detail", "") or "409" in str(s), True, "message 409 explicatif")

print("=== 44. sync-contacts RBAC (non-member → 403) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/linkedin/sync-contacts", ptok)
ck(s, 403, "non-member → 403 sur sync-contacts")

# ─── P13 Notifications & e-mails (45-53) : envoi, dry-run, feed, RGPD, placeholder, RBAC ─
print("=== 45. POST /crm/contacts/{id}/email (envoi e-mail dry-run) ===")
# Crée un contact avec un email réel pour tester l'envoi (smtp_enabled=false → dry_run).
s, nc = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok,
             {"first_name": "Sophie", "last_name": "Lambert",
              "email": "sophie.lambert@techcorp.com", "title": "CTO", "status": "qualified"})
ck(s, 201, "create contact pour email test 201")
ncid = nc.get("id")
s, res = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{ncid}/email", tok,
              {"template": "cold_outreach", "custom_message": "Hello Sophie, idée pour TechCorp !"})
ck(s, 200, "email_contact 200")
ck(res.get("status"), "dry_run", "email_contact status dry_run (smtp_enabled=false)")
ck(res.get("email_sent"), True, "email_sent True (dry-run = OK)")
ck(bool(res.get("notification_id")), True, "notification_id retourné (traçabilité)")

print("=== 46. GET /notifications (liste paginée) ===")
s, ns = call("GET", "/api/tenants/tnt-demo/notifications", tok)
ck(s, 200, "notifications list 200")
ck(len(ns) >= 1, True, f"≥1 notification ({len(ns)})")
ck(any(n.get("status") == "dry_run" for n in ns), True, "≥1 notif status dry_run")

print("=== 47. GET /notifications/feed (in-app non lus) ===")
s, feed = call("GET", "/api/tenants/tnt-demo/notifications/feed", tok)
ck(s, 200, "feed 200")
ck(len(feed) >= 1, True, f"≥1 notif non lue ({len(feed)})")
ck(all(f.get("read_at") is None for f in feed), True, "feed = uniquement non lus (read_at null)")

print("=== 48. POST /notifications/{id}/read (marque lu) ===")
nid = feed[0].get("id")
s, nr = call("POST", f"/api/tenants/tnt-demo/notifications/{nid}/read", tok)
ck(s, 200, "mark read 200")
ck(bool(nr.get("read_at")), True, "read_at set après mark read")
s, feed2 = call("GET", "/api/tenants/tnt-demo/notifications/feed", tok)
ck(len(feed2) < len(feed), True, f"feed réduit après mark read ({len(feed)}→{len(feed2)})")

print("=== 49. GET /notifications/templates (4 templates) ===")
s, ts = call("GET", "/api/tenants/tnt-demo/notifications/templates", tok)
ck(s, 200, "templates list 200")
ck(len(ts) == 4, True, f"4 templates ({len(ts)})")
ck(any(t.get("id") == "cold_outreach" for t in ts), True, "template cold_outreach présent")
ck(all("name" in t and "description" in t for t in ts), True, "chaque template a name + description")

print("=== 50. email_contact template inconnu → 400 ===")
s, _ = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{ncid}/email", tok, {"template": "unknown_tpl"})
ck(s, 400, "template inconnu → 400")

print("=== 51. email_contact placeholder (@linkedin.sync) → skipped ===")
# Un contact syncé LinkedIn (P12) a un email @linkedin.sync — pas un destinataire réel.
s, cs = call("GET", "/api/tenants/tnt-demo/crm/contacts?source=linkedin", tok)
ck(s == 200 and len(cs) >= 1, True, f"contact LinkedIn présent pour test placeholder ({len(cs)})")
if cs:
    s, res2 = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{cs[0]['id']}/email", tok,
                   {"template": "intro"})
    ck(s, 200, "email placeholder 200")
    ck(res2.get("status"), "skipped", "placeholder @linkedin.sync → skipped")
    ck(res2.get("email_sent"), False, "email_sent False (skipped)")
else:
    ck(False, True, "contact LinkedIn présent (test placeholder)")

print("=== 52. email_contact RGPD (suppression list) → skipped ===")
call("POST", "/api/tenants/tnt-demo/crm/suppression", tok,
     {"email": "sophie.lambert@techcorp.com", "reason": "unsubscribe"})
s, res3 = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{ncid}/email", tok, {"template": "follow_up"})
ck(s, 200, "email RGPD 200")
ck(res3.get("status"), "skipped", "email en suppression → skipped (RGPD)")
ck(res3.get("email_sent"), False, "email_sent False (RGPD bloqué)")

print("=== 53. email_contact RBAC (non-member → 403) ===")
s, _ = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{ncid}/email", ptok, {"template": "cold_outreach"})
ck(s, 403, "non-member → 403 sur email_contact")

# ─── P14 Analytics & reporting (54-59) : overview/pipeline/emails/sources/export CSV/RBAC ─
print("=== 54. GET /analytics/overview (KPIs agrégés CRM+emails+sync) ===")
s, ov = call("GET", "/api/tenants/tnt-demo/analytics/overview", tok)
ck(s, 200, "analytics overview 200")
ck(ov.get("contacts_total", -1) >= 1, True, f"contacts_total ≥ 1 ({ov.get('contacts_total')})")
ck("contacts_by_source" in ov, True, "contacts_by_source présent")
ck("contacts_by_status" in ov, True, "contacts_by_status présent")
ck("opportunities_total" in ov, True, "opportunities_total présent")
ck("pipeline_value" in ov, True, "pipeline_value présent (sum Opportunity.value)")
ck("emails_total" in ov, True, "emails_total présent (P13)")
ck(ov.get("emails_total", -1) >= 1, True, f"emails_total ≥ 1 ({ov.get('emails_total')}) — notifs P13")
ck("sync_runs_total" in ov, True, "sync_runs_total présent (P12)")
ck(ov.get("sync_runs_total", -1) >= 1, True, f"sync_runs_total ≥ 1 ({ov.get('sync_runs_total')}) — sync P12")

print("=== 55. GET /analytics/pipeline (par stage + win rate + value) ===")
s, pp = call("GET", "/api/tenants/tnt-demo/analytics/pipeline", tok)
ck(s, 200, "analytics pipeline 200")
ck("stages" in pp, True, "stages list présent")
ck("win_rate_pct" in pp, True, "win_rate_pct présent")
ck("total_value" in pp, True, "total_value présent")
ck("total_opportunities" in pp, True, "total_opportunities présent")

print("=== 56. GET /analytics/emails (par template + succès) ===")
s, em = call("GET", "/api/tenants/tnt-demo/analytics/emails", tok)
ck(s, 200, "analytics emails 200")
ck("by_template" in em, True, "by_template présent")
ck("by_status" in em, True, "by_status présent")
ck("success_rate_pct" in em, True, "success_rate_pct présent")
ck(em.get("total", -1) >= 1, True, f"emails total ≥ 1 ({em.get('total')})")

print("=== 57. GET /analytics/sources (par source + conversion) ===")
s, sr = call("GET", "/api/tenants/tnt-demo/analytics/sources", tok)
ck(s, 200, "analytics sources 200")
ck("by_source" in sr, True, "by_source présent")
ck("total" in sr, True, "total présent")
ck(sr.get("total", -1) >= 1, True, f"sources total ≥ 1 ({sr.get('total')})")

print("=== 58. GET /analytics/export (CSV contacts — raw urllib, non-JSON) ===")
_req = urllib.request.Request(BASE + "/api/tenants/tnt-demo/analytics/export")
_req.add_header("Authorization", f"Bearer {tok}")
try:
    with urllib.request.urlopen(_req, timeout=10) as _r:
        _csv_status = _r.status
        _csv_ct = _r.headers.get("content-type", "")
        _csv_cd = _r.headers.get("content-disposition", "")
        _csv_text = _r.read().decode("utf-8")
except urllib.error.HTTPError as _e:
    _csv_status, _csv_ct, _csv_cd, _csv_text = _e.code, "", "", ""
ck(_csv_status, 200, "analytics export 200")
ck("text/csv" in _csv_ct, True, f"content-type text/csv ({_csv_ct})")
ck("attachment" in _csv_cd, True, "Content-Disposition attachment (download)")
ck("first_name" in _csv_text and "email" in _csv_text, True, "CSV header (first_name, email)")
_csv_lines = [l for l in _csv_text.strip().split("\n") if l]
ck(len(_csv_lines) >= 2, True, f"≥1 ligne data CSV ({len(_csv_lines)} lignes total)")

print("=== 59. analytics RBAC (non-member → 403) ===")
s, _ = call("GET", "/api/tenants/tnt-demo/analytics/overview", ptok)
ck(s, 403, "non-member → 403 sur analytics/overview (get_tenant)")
s, _ = call("GET", "/api/tenants/tnt-demo/analytics/export", ptok)
ck(s, 403, "non-member → 403 sur analytics/export (crm.manage)")

# ─── P15 Séquences & automatisation (60-67) : create/enroll/scheduler/process/delete/RBAC ──
print("=== 60. POST /crm/contacts (fresh contact real email pour sequence) ===")
s, c = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok,
            {"first_name": "Seq", "last_name": "Tester", "email": "seq.tester@prospectmail.io",
             "title": "CTO", "source": "manual"})
ck(s, 201, "create contact for sequence 201")
ctid_seq = c["id"]

print("=== 61. POST /crm/sequences (create 2 steps delay_days=0, status=active) ===")
s, seq = call("POST", "/api/tenants/tnt-demo/crm/sequences", tok,
              {"name": "Drip E2E P15", "description": "Sequence de test", "status": "active",
               "steps": [{"template_id": "cold_outreach", "delay_days": 0},
                         {"template_id": "follow_up", "delay_days": 0}]})
ck(s, 201, "create sequence 201")
ck(len(seq.get("steps", [])), 2, "2 steps crees")
ck(seq.get("status"), "active", "status=active")
sid = seq["id"]

print("=== 62. GET /crm/sequences (list) + GET /{sid} (detail) ===")
s, seqs = call("GET", "/api/tenants/tnt-demo/crm/sequences", tok)
ck(s, 200, "list sequences 200")
ck(len(seqs) >= 1, True, ">=1 sequence dans la liste")
s, det = call("GET", f"/api/tenants/tnt-demo/crm/sequences/{sid}", tok)
ck(s, 200, "detail sequence 200")
ck(len(det.get("steps", [])), 2, "detail: 2 steps")
ck(det.get("enrollments_count"), 0, "detail: 0 enrollment avant enroll")

print("=== 63. POST /crm/sequences/{sid}/enroll → 201 active + 409 double ===")
s, enr = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid}/enroll", tok,
              {"contact_id": ctid_seq})
ck(s, 201, "enroll 201")
ck(enr.get("status"), "active", "enrollment active")
ck(enr.get("current_step_order"), 1, "current_step_order=1 (premier step)")
s, _ = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid}/enroll", tok,
            {"contact_id": ctid_seq})
ck(s, 409, "double enroll → 409 conflict")

print("=== 64. GET /crm/sequences/{sid}/stats (active=1, total=1) ===")
s, st = call("GET", f"/api/tenants/tnt-demo/crm/sequences/{sid}/stats", tok)
ck(s, 200, "stats 200")
ck(st.get("active"), 1, "active=1")
ck(st.get("total_enrollments"), 1, "total_enrollments=1")

print("=== 65. process flow (2 manual /process calls → 2 steps sent + completed) ===")
s, ov0 = call("GET", "/api/tenants/tnt-demo/analytics/overview", tok)
emails_before = ov0.get("emails_total", 0)
# Process call 1 : step 1 (due, next_step_at=now car delay_days=0).
s, pr1 = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid}/process", tok)
ck(s, 200, "process call 1 200")
ck(pr1.get("processed"), 1, "process 1: processed=1 (step 1 due)")
ck(pr1.get("sent"), 1, "process 1: sent=1 (email step 1 dry_run)")
# Process call 2 : step 2 (now due — next_step_at=now après advance step 1, delay_days=0).
s, pr2 = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid}/process", tok)
ck(s, 200, "process call 2 200")
ck(pr2.get("processed"), 1, "process 2: processed=1 (step 2 due)")
ck(pr2.get("completed"), 1, "process 2: completed=1 (dernier step → completed)")
# Verify enrollment completed + current_step=2 + ≥2 emails envoyés via process.
s, enrs = call("GET", f"/api/tenants/tnt-demo/crm/sequences/{sid}/enrollments", tok)
ck(s, 200, "enrollments list 200")
enr_after = enrs[0] if enrs else {}
ck(enr_after.get("status"), "completed", f"enrollment completed (status={enr_after.get('status')})")
ck(enr_after.get("current_step_order"), 2, "current_step_order=2 (dernier step)")
s, ov1 = call("GET", "/api/tenants/tnt-demo/analytics/overview", tok)
emails_after = ov1.get("emails_total", 0)
ck(emails_after >= emails_before + 2, True,
   f">=2 emails envoyes via process ({emails_after} >= {emails_before}+2)")

print("=== 66. POST /process (manual no-op) + DELETE + 404 ===")
s, pr = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid}/process", tok)
ck(s, 200, "process manual 200")
ck(pr.get("processed"), 0, "processed=0 (rien de du — tout complete)")
s, d = call("DELETE", f"/api/tenants/tnt-demo/crm/sequences/{sid}", tok)
ck(s, 200, "delete sequence 200 (retourne {deleted, name})")
ck(d.get("deleted"), sid, "delete retourne {deleted: sid}")
s, _ = call("GET", f"/api/tenants/tnt-demo/crm/sequences/{sid}", tok)
ck(s, 404, "GET after delete → 404")

print("=== 67. sequences RBAC (non-member → 403) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/crm/sequences", ptok,
            {"name": "x", "steps": [{"template_id": "cold_outreach"}]})
ck(s, 403, "non-member → 403 sur create sequence")
s, _ = call("POST", "/api/tenants/tnt-demo/crm/sequences/x/enroll", ptok, {"contact_id": "x"})
ck(s, 403, "non-member → 403 sur enroll")

# ─── P16 Ciblage avancé + batch enrollment (68-74) ───────────────────────────
print("=== 68. POST /crm/sequences (seq2 for batch enroll) ===")
s, seq2 = call("POST", "/api/tenants/tnt-demo/crm/sequences", tok,
               {"name": "Batch E2E P16", "status": "active",
                "steps": [{"template_id": "cold_outreach", "delay_days": 1}]})
ck(s, 201, "create seq2 for batch 201")
sid2 = seq2["id"]

print("=== 69. POST /crm/segments (criteria has_email=true) ===")
s, seg = call("POST", "/api/tenants/tnt-demo/crm/segments", tok,
              {"name": "Emailables P16", "criteria": {"has_email": True}})
ck(s, 201, "create segment has_email 201")
seg_id = seg["id"]

print("=== 70. GET /crm/segments/{seg_id}/contacts (apply — only real emails) ===")
s, sc = call("GET", f"/api/tenants/tnt-demo/crm/segments/{seg_id}/contacts", tok)
ck(s, 200, "apply segment 200")
ck(len(sc) >= 1, True, "≥1 contact avec email réel (seed Marie etc.)")
ck(all("@linkedin.sync" not in (c.get("email") or "") and "@local.revpulse.io" not in (c.get("email") or "") for c in sc),
   True, "aucun contact placeholder dans le segment has_email")

print("=== 71. POST /crm/sequences/{sid2}/enroll-batch {segment_id} ===")
s, eb = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid2}/enroll-batch", tok,
             {"segment_id": seg_id})
ck(s, 200, "enroll-batch segment 200")
ck(eb.get("enrolled", 0) >= 1, True, f"enrolled ≥1 ({eb.get('enrolled')})")
ck(eb.get("skipped"), 0, "skipped=0 (premier batch)")
batch_n = eb.get("enrolled", 0)

print("=== 72. GET /crm/sequences/{sid2}/enrollments (count = batch_n) ===")
s, enrs2 = call("GET", f"/api/tenants/tnt-demo/crm/sequences/{sid2}/enrollments", tok)
ck(s, 200, "enrollments list 200")
ck(len(enrs2), batch_n, f"{batch_n} enrollments créés via batch")

print("=== 73. POST enroll-batch AGAIN {segment_id} → enrolled=0, skipped=batch_n (idempotent) ===")
s, eb2 = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid2}/enroll-batch", tok,
              {"segment_id": seg_id})
ck(s, 200, "enroll-batch again 200")
ck(eb2.get("enrolled"), 0, "enrolled=0 (déjà tous actifs)")
ck(eb2.get("skipped"), batch_n, f"skipped={batch_n} (idempotent — tous déjà actifs)")
ck(eb2.get("total"), batch_n, f"total={batch_n}")

print("=== 74. enroll-batch {} → 400 + non-member → 403 ===")
s, _ = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid2}/enroll-batch", tok, {})
ck(s, 400, "enroll-batch empty (no segment_id, no contact_ids) → 400")
s, _ = call("POST", f"/api/tenants/tnt-demo/crm/sequences/{sid2}/enroll-batch", ptok, {"segment_id": seg_id})
ck(s, 403, "non-member → 403 sur enroll-batch")

# ─── P17 Scoring avancé (75-80) ──────────────────────────────────────────────
print("=== 75. POST /crm/contacts (cid_score : email+linkedin+VP+phone = profil 80) ===")
s, csc = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok,
              {"first_name": "Score", "last_name": "P17", "email": "score.p17@test.com",
               "linkedin_url": "https://linkedin.com/in/scorep17", "title": "VP Engineering",
               "phone": "+33612345678"})
ck(s, 201, "create cid_score 201")
cid_score = csc["id"]

print("=== 76. POST /crm/contacts/{cid_score}/score (profil=80, engagement=0) ===")
s, sb = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{cid_score}/score", tok)
ck(s, 200, "recompute score 200")
ck(sb.get("score"), 80, "score=80 (4 critères profil, no engagement yet)")
ck(sb.get("breakdown", {}).get("engagement"), 0, "engagement=0 (no email sent)")

print("=== 77. POST /crm/contacts/{cid_score}/email (envoi → hook recompute P17) ===")
s, em = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{cid_score}/email", tok,
             {"template": "cold_outreach"})
ck(s, 200, "send email 200")
ck(em.get("status") in ("sent", "dry_run"), True, f"email status={em.get('status')} (engagement valide)")

print("=== 78. POST /crm/contacts/{cid_score}/score AGAIN (engagement +5, decay 0) ===")
s, sb2 = call("POST", f"/api/tenants/tnt-demo/crm/contacts/{cid_score}/score", tok)
ck(s, 200, "recompute score again 200")
ck(sb2.get("score"), 85, "score=85 (80 profil + 5 engagement, decay=0 car contact récent)")
ck(sb2.get("breakdown", {}).get("engagement"), 5, "engagement=5 (1 email × 5)")
ck(sb2.get("breakdown", {}).get("decay"), 0, "decay=0 (last_contact = now)")

print("=== 79. POST /crm/contacts/score-recompute (batch) ===")
s, srr = call("POST", "/api/tenants/tnt-demo/crm/contacts/score-recompute", tok)
ck(s, 200, "batch recompute 200")
ck(srr.get("recomputed", 0) >= 1, True, f"recomputed ≥1 ({srr.get('recomputed')})")
ck(srr.get("avg_score", -1) >= 0, True, f"avg_score ≥0 ({srr.get('avg_score')})")

print("=== 80. score-recompute RBAC (non-member → 403) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/crm/contacts/score-recompute", ptok)
ck(s, 403, "non-member → 403 sur score-recompute")

# ─── P18 : Webhooks outbound (serveur local qui capte les livraisons + signature HMAC) ───
import hashlib  # noqa: E402
import hmac  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402

_hook_received = []


class _HookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        _hook_received.append({"event": self.headers.get("X-RevPulse-Event"),
                               "sig": self.headers.get("X-RevPulse-Signature"), "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


_hook_srv = http.server.HTTPServer(("127.0.0.1", 9099), _HookHandler)
threading.Thread(target=_hook_srv.serve_forever, daemon=True).start()

# Cleanup : supprime les webhooks préexistants (smoke test / run précédent) pour un état propre.
s, _existing = call("GET", "/api/tenants/tnt-demo/webhooks", tok)
for _w in _existing:
    call("DELETE", f"/api/tenants/tnt-demo/webhooks/{_w['id']}", tok)

print("=== 81. POST /webhooks (create, events contact.created) ===")
s, wh = call("POST", "/api/tenants/tnt-demo/webhooks", tok,
             {"url": "http://127.0.0.1:9099/wh", "events": ["contact.created"]})
ck(s, 201, "create webhook 201")
ck(wh.get("events"), ["contact.created"], "events retournés filtrés")
ck("secret" not in wh, True, "secret non exposé en WebhookOut")

print("=== 82. POST /webhooks/{wid}/test (ping → delivery success) ===")
s, tr = call("POST", f"/api/tenants/tnt-demo/webhooks/{wh['id']}/test", tok)
ck(s, 200, "test ping 200")
ck(tr.get("status"), "success", "test ping delivery success")
ck(tr.get("response_code"), 200, "test ping HTTP 200 reçu")

print("=== 83. POST /crm/contacts (emit contact.created → delivery) ===")
s, c = call("POST", "/api/tenants/tnt-demo/crm/contacts", tok,
            {"first_name": "HookE2E", "last_name": "Test", "email": "hooke2e@test.com", "title": "VP"})
ck(s, 201, "create contact 201 (emit contact.created déclenché)")
time.sleep(0.5)

print("=== 84. GET /webhooks/{wid}/deliveries (2 deliveries success) ===")
s, delivs = call("GET", f"/api/tenants/tnt-demo/webhooks/{wh['id']}/deliveries", tok)
ck(s, 200, "list deliveries 200")
ck(len(delivs), 2, "2 deliveries (webhook.test + contact.created)")
ck(all(d.get("status") == "success" for d in delivs), True, "toutes les deliveries success")

print("=== 85. signature HMAC reçue sur serveur local (64 hex) ===")
ck(len(_hook_received), 2, "2 requêtes reçues sur serveur local")
ck(all(len(r.get("sig", "")) == 64 for r in _hook_received), True, "signatures 64 hex chars (HMAC-SHA256)")
ck(all(all(ch in "0123456789abcdef" for ch in r.get("sig", "")) for r in _hook_received), True, "signatures hex valides")

print("=== 86. POST /webhooks RBAC (non-member → 403) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/webhooks", ptok,
            {"url": "http://127.0.0.1:9099/x", "events": ["contact.created"]})
ck(s, 403, "non-member → 403 sur create webhook")

print("=== 87. PATCH /webhooks/{wid} (toggle active=false) ===")
s, w2 = call("PATCH", f"/api/tenants/tnt-demo/webhooks/{wh['id']}", tok, {"active": False})
ck(s, 200, "patch webhook 200")
ck(w2.get("active"), False, "active=false après patch")

print("=== 88. DELETE /webhooks/{wid} (CASCADE deliveries) ===")
s, _ = call("DELETE", f"/api/tenants/tnt-demo/webhooks/{wh['id']}", tok)
ck(s, 200, "delete webhook 200")
s, lst = call("GET", "/api/tenants/tnt-demo/webhooks", tok)
ck(len(lst), 0, "webhook list vide après delete")
_hook_srv.shutdown()

# ─── P20 : Webhooks inbound (réception → create/update Contact) ────────────────
# Cleanup : supprime les inbound webhooks préexistants (run précédent) pour un état propre.
s, _existing_ibw = call("GET", "/api/tenants/tnt-demo/inbound-webhooks", tok)
for _iw in _existing_ibw:
    call("DELETE", f"/api/tenants/tnt-demo/inbound-webhooks/{_iw['id']}", tok)

print("=== 89. POST /inbound-webhooks (create, token auto-généré) ===")
s, ibw = call("POST", "/api/tenants/tnt-demo/inbound-webhooks", tok,
              {"source_name": "Website form", "active": True})
ck(s, 201, "create inbound webhook 201")
ck(len(ibw.get("token", "")), 32, "token 32 hex auto-généré + exposé en Out")
ck(ibw.get("source_name"), "Website form", "source_name retourné")
ck(ibw.get("active"), True, "active=true par défaut")

print("=== 90. GET /inbound-webhooks (list, 1 item) ===")
s, ibw_list = call("GET", "/api/tenants/tnt-demo/inbound-webhooks", tok)
ck(s, 200, "list inbound 200")
ck(len(ibw_list), 1, "1 inbound webhook après create")

print("=== 91. POST /api/inbound/webhook/{token} (public, no auth → create contact) ===")
_INBOUND_EMAIL = f"inbound-test-{ibw['token'][:8]}@example.com"
s, res = call("POST", f"/api/inbound/webhook/{ibw['token']}", None,
              {"first_name": "Inbound", "last_name": "Test", "email": _INBOUND_EMAIL, "title": "DevRel"})
ck(s, 200, "POST public inbound 200 (no auth — token authentifie)")
ck(res.get("action"), "created", "action=created (nouveau contact)")
ck(res.get("matched_by"), "none", "matched_by=none (création nette)")
_ibw_contact_id = res.get("contact_id", "")
ck(bool(_ibw_contact_id), True, "contact_id retourné")

print("=== 92. POST /api/inbound/webhook/{token} (même email → update, dedup) ===")
s, res2 = call("POST", f"/api/inbound/webhook/{ibw['token']}", None,
               {"email": _INBOUND_EMAIL, "title": "CTO", "phone": "+33 6 12 34"})
ck(s, 200, "POST public inbound 200 (update)")
ck(res2.get("action"), "updated", "action=updated (dedup email)")
ck(res2.get("contact_id"), _ibw_contact_id, "même contact_id (dedup)")
ck(res2.get("matched_by"), "email", "matched_by=email")

print("=== 93. GET /crm/contacts (vérifie le contact créé, source=inbound) ===")
s, contacts = call("GET", "/api/tenants/tnt-demo/crm/contacts", tok)
ck(s, 200, "list contacts 200")
_ibw_ct = next((c for c in contacts if c.get("id") == _ibw_contact_id), None)
ck(_ibw_ct is not None, True, "contact inbound présent dans la liste")
ck(_ibw_ct.get("source") if _ibw_ct else None, "inbound", "contact source=inbound")
ck(_ibw_ct.get("title") if _ibw_ct else None, "CTO", "title=CTO (mis à jour par le 2e POST)")
ck(_ibw_ct.get("first_name") if _ibw_ct else None, "Inbound", "first_name préservé (update non-vides uniquement)")

print("=== 94. POST /api/inbound/webhook/{token} (payload vide → 422) ===")
s, _ = call("POST", f"/api/inbound/webhook/{ibw['token']}", None, {"unknown_field": "x"})
ck(s, 422, "payload vide (aucun champ mappable) → 422")

print("=== 95. POST /api/inbound/webhook/unknowntoken (→ 404) ===")
s, _ = call("POST", "/api/inbound/webhook/unknowntoken123", None, {"email": "x@y.com"})
ck(s, 404, "token inconnu → 404")

print("=== 96. PATCH /inbound-webhooks/{id} (active=false) → POST → 410 Gone ===")
s, _ = call("PATCH", f"/api/tenants/tnt-demo/inbound-webhooks/{ibw['id']}", tok, {"active": False})
ck(s, 200, "patch inbound active=false 200")
s, _ = call("POST", f"/api/inbound/webhook/{ibw['token']}", None, {"email": "suspended@test.com"})
ck(s, 410, "inbound webhook suspendu → 410 Gone")

print("=== 97. DELETE /inbound-webhooks/{id} → POST → 404 (token invalide) ===")
# Réactive d'abord (pour pouvoir delete — optionnel, mais teste aussi la réactivation)
s, _ = call("PATCH", f"/api/tenants/tnt-demo/inbound-webhooks/{ibw['id']}", tok, {"active": True})
ck(s, 200, "patch inbound active=true (réactivation)")
s, _ = call("DELETE", f"/api/tenants/tnt-demo/inbound-webhooks/{ibw['id']}", tok)
ck(s, 200, "delete inbound 200")
s, ibw_list2 = call("GET", "/api/tenants/tnt-demo/inbound-webhooks", tok)
ck(len(ibw_list2), 0, "inbound list vide après delete")
s, _ = call("POST", f"/api/inbound/webhook/{ibw['token']}", None, {"email": "deleted@test.com"})
ck(s, 404, "token supprimé → 404")

print("=== 98. POST /inbound-webhooks RBAC (non-member → 403) ===")
s, _ = call("POST", "/api/tenants/tnt-demo/inbound-webhooks", ptok,
            {"source_name": "Should fail"})
ck(s, 403, "non-member → 403 sur create inbound webhook")

print("=== 99. POST /inbound-webhooks (field_map custom + anti-injection) ===")
s, ibw2 = call("POST", "/api/tenants/tnt-demo/inbound-webhooks", tok,
               {"source_name": "Zapier", "field_map": {"prénom": "first_name", "courriel": "email"}})
ck(s, 201, "create inbound avec field_map custom 201")
# POST avec payload utilisant les clés custom
s, res3 = call("POST", f"/api/inbound/webhook/{ibw2['token']}", None,
               {"prénom": "Zap", "courriel": "zapier-test@example.com"})
ck(s, 200, "POST avec field_map custom 200")
ck(res3.get("action"), "created", "contact créé via field_map custom")
# Anti-injection : field_map ciblant un champ non-writable → 422
s, _ = call("POST", "/api/tenants/tnt-demo/inbound-webhooks", tok,
            {"source_name": "Evil", "field_map": {"x": "tenant_id"}})
ck(s, 422, "field_map ciblant tenant_id → 422 (anti-injection)")
# Cleanup
call("DELETE", f"/api/tenants/tnt-demo/inbound-webhooks/{ibw2['id']}", tok)

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
