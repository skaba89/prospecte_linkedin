#!/usr/bin/env python3
"""test_p86_sequence_templates.py — Templates de séquences réutilisables (P86).

Démarre uvicorn, login démo, et valide :
  1. GET /sequences/templates → 8 templates, chacun avec champs valides
     (id, name, description, category dans CATEGORIES, step_count, steps avec template_id valide).
  2. Catégories couvertes : prospection, onboarding, re-engagement, closing (≥1 template chacune).
  3. Tous les template_id des steps ∈ {cold_outreach, follow_up, intro, re_engagement}.
  4. POST clone un template → 201 + SequenceOut avec :
     - name = nom du template (défaut)
     - status = draft (l'utilisateur révise avant activation)
     - steps matching le template (ordre, template_id, delay_days)
  5. Le clone apparaît dans GET /sequences (list du tenant).
  6. Clone avec name custom → surcharge le nom.
  7. Audit log : entry crm.sequence_template_clone créée (target_id = seq.id).
  8. RBAC : clone sans token → 401.
  9. Clone template inexistant → 404.
"""
import io
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
passed = failed = 0
SERVER_PROC = None

VALID_TPL_IDS = {"cold_outreach", "follow_up", "intro", "re_engagement"}
CATEGORIES = {"prospection", "onboarding", "re-engagement", "closing"}


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def start_server():
    global SERVER_PROC
    env = dict(os.environ)
    env["REVPULSE_DATABASE_URL"] = "sqlite:///./revpulse_p86.db"
    env["REVPULSE_ENV"] = "test"
    env["WEBHOOK_SSRF_ALLOW_PRIVATE"] = "true"
    env["REVPULSE_FRONTEND_DIR"] = str(ROOT / "frontend" / "public")
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run("pkill -f 'uvicorn' 2>/dev/null || true", shell=True)
    time.sleep(2)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p86.db", shell=True)
    SERVER_PROC = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            urllib.request.urlopen(URL + "/api/health", timeout=2); return True
        except Exception:
            time.sleep(1)
    return False


def call(method, path, token=None, body=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, None


def main():
    if not start_server():
        print("✗ Serveur n'a pas démarré"); sys.exit(1)
    print(f"✓ Serveur démarré sur {URL}\n")

    # --- Login démo + fetch tenants ---
    st, demo = call("POST", "/api/auth/demo")
    tok = demo.get("token") if isinstance(demo, dict) else None
    ck(bool(tok), f"login démo → token (got {bool(tok)})")
    st, tenants = call("GET", "/api/tenants", token=tok)
    ck(isinstance(tenants, list) and len(tenants) > 0, f"GET /tenants → liste non vide (got {len(tenants) if isinstance(tenants, list) else 'n/a'})")
    if not (tok and tenants):
        print("✗ Setup échoué — abort"); sys.exit(1)
    tid = tenants[0]["id"]

    print(f"\n--- GET /sequences/templates (tid={tid}) ---")
    st, tpls = call("GET", f"/api/tenants/{tid}/crm/sequences/templates", token=tok)
    ck(st == 200, f"statut 200 (got {st})")
    ck(isinstance(tpls, list), f"réponse = liste (got {type(tpls).__name__})")
    ck(len(tpls) == 8, f"8 templates curés (got {len(tpls)})")

    # Champs valides par template
    cats_seen = set()
    for t in tpls:
        ck(isinstance(t.get("id"), str) and t.get("id"), f"template {t.get('id','?')} : id slug non vide")
        ck(isinstance(t.get("name"), str) and t.get("name"), f"template {t.get('id','?')} : name non vide")
        ck(isinstance(t.get("description"), str) and len(t.get("description", "")) > 20, f"template {t.get('id','?')} : description ≥20 chars")
        cat = t.get("category")
        ck(cat in CATEGORIES, f"template {t.get('id','?')} : catégorie valide '{cat}'")
        if cat in CATEGORIES:
            cats_seen.add(cat)
        ck(t.get("step_count") == len(t.get("steps", [])), f"template {t.get('id','?')} : step_count == len(steps) ({t.get('step_count')} == {len(t.get('steps', []))})")
        ck(t.get("step_count", 0) >= 1, f"template {t.get('id','?')} : ≥1 step (got {t.get('step_count')})")
        for s in t.get("steps", []):
            ck(s.get("template_id") in VALID_TPL_IDS, f"template {t.get('id','?')} : step template_id valide '{s.get('template_id')}'")
            ck(isinstance(s.get("delay_days"), int) and s.get("delay_days", -1) >= 0, f"template {t.get('id','?')} : delay_days ≥0 ({s.get('delay_days')})")

    # 4 catégories couvertes
    ck(cats_seen == CATEGORIES, f"4 catégories couvertes (got {cats_seen})")

    # --- POST clone un template ---
    print("\n--- POST clone template (cold-outreach-3step) ---")
    tpl = next((t for t in tpls if t["id"] == "cold-outreach-3step"), tpls[0])
    tpl_id = tpl["id"]
    st, cloned = call("POST", f"/api/tenants/{tid}/crm/sequences/templates/{tpl_id}/clone", token=tok, body={})
    ck(st == 201, f"clone statut 201 (got {st})")
    ck(isinstance(cloned, dict) and cloned.get("id"), f"clone → SequenceOut avec id (got {cloned.get('id') if isinstance(cloned, dict) else 'n/a'})")
    if isinstance(cloned, dict) and cloned.get("id"):
        ck(cloned.get("name") == tpl["name"], f"clone name = nom template défaut (got '{cloned.get('name')}')")
        ck(cloned.get("status") == "draft", f"clone status = draft (got '{cloned.get('status')}')")
        ck(cloned.get("tenant_id") == tid, f"clone tenant_id = tid (got '{cloned.get('tenant_id')}')")
        ck(len(cloned.get("steps", [])) == tpl["step_count"], f"clone steps count = template ({len(cloned.get('steps', []))} == {tpl['step_count']})")
        # Vérif correspondance steps (ordre, template_id, delay_days)
        match = all(
            cloned["steps"][i].get("template_id") == tpl["steps"][i]["template_id"]
            and cloned["steps"][i].get("delay_days") == tpl["steps"][i]["delay_days"]
            for i in range(min(len(cloned.get("steps", [])), len(tpl["steps"])))
        )
        ck(match, "clone steps : template_id + delay_days correspondent au template (ordre préservé)")
        cloned_id = cloned["id"]
    else:
        cloned_id = None

    # --- Le clone apparaît dans la list du tenant ---
    print("\n--- Vérif présence dans GET /sequences ---")
    st, seqs = call("GET", f"/api/tenants/{tid}/crm/sequences", token=tok)
    ck(st == 200, f"GET /sequences statut 200 (got {st})")
    found = isinstance(seqs, list) and any(s.get("id") == cloned_id for s in seqs)
    ck(found, f"clone {cloned_id} présent dans la list du tenant")

    # --- Clone avec name custom ---
    print("\n--- POST clone avec name custom ---")
    st, cloned2 = call("POST", f"/api/tenants/{tid}/crm/sequences/templates/{tpl_id}/clone", token=tok, body={"name": "Ma séquence Q1 custom"})
    ck(st == 201, f"clone custom name statut 201 (got {st})")
    ck(isinstance(cloned2, dict) and cloned2.get("name") == "Ma séquence Q1 custom", f"clone custom name = 'Ma séquence Q1 custom' (got '{cloned2.get('name') if isinstance(cloned2, dict) else 'n/a'}')")

    # --- Audit log : crm.sequence_template_clone ---
    # Path = /audit (suffixe vide, pas /audit/entries). action = prefix match (ex
    # crm.sequence → tous les actions sequence). RBAC workspace.manage (Owner — demo l'est).
    print("\n--- Audit log crm.sequence_template_clone ---")
    st, audit = call("GET", f"/api/tenants/{tid}/audit?action=crm.sequence_template_clone", token=tok)
    # list_audit renvoie une liste plain (response_model=list[AuditLogOut])
    audit_entries = audit if isinstance(audit, list) else (audit.get("items", []) if isinstance(audit, dict) else [])
    # fallback sans filtre action si la route a changé
    if not audit_entries:
        st, audit_all = call("GET", f"/api/tenants/{tid}/audit", token=tok)
        audit_entries = audit_all if isinstance(audit_all, list) else (audit_all.get("items", []) if isinstance(audit_all, dict) else [])
    clone_audits = [a for a in audit_entries if a.get("action") == "crm.sequence_template_clone"]
    ck(len(clone_audits) >= 1, f"audit log : ≥1 entry crm.sequence_template_clone (got {len(clone_audits)})")
    if clone_audits:
        ck(any(a.get("target_id") == cloned_id for a in clone_audits), "audit log : target_id = cloned seq.id")
        ck(any(a.get("detail", {}).get("template_id") == tpl_id for a in clone_audits), "audit log : detail.template_id présent")

    # --- RBAC : clone sans token → 401 ---
    print("\n--- RBAC ---")
    st, _ = call("POST", f"/api/tenants/{tid}/crm/sequences/templates/{tpl_id}/clone", body={})
    ck(st == 401, f"clone sans token → 401 (got {st})")

    # --- Clone template inexistant → 404 ---
    print("\n--- Edge cases ---")
    st, _ = call("POST", f"/api/tenants/{tid}/crm/sequences/templates/template-inexistant-xxx/clone", token=tok, body={})
    ck(st == 404, f"clone template inexistant → 404 (got {st})")

    # --- GET templates sans token → 401 (get_tenant requiert auth) ---
    st, _ = call("GET", f"/api/tenants/{tid}/crm/sequences/templates")
    ck(st == 401, f"GET templates sans token → 401 (got {st})")

    if SERVER_PROC:
        SERVER_PROC.terminate(); SERVER_PROC.wait(timeout=5)
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p86.db", shell=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
