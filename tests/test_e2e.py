"""Tests P6 : E2E Playwright — intégration SPA↔backend bout-en-bout.

Le SPA (index.html + app-enhancements.js) n'était jusqu'ici testé que statiquement
(test_frontend : headers/markup). Ce test exerce le cheminement utilisateur réel dans
un vrai navigateur (chromium) contre le backend :

  1. SPA chargée par le backend (StaticFiles) + app-enhancements.js exécuté
  2. token démo injecté (localStorage 'revpulse_jwt') → FAB Studio visible
  3. drawer Studio ouvert après clic FAB
  4. refresh() a tourné (#rp-tid peuplé) + section audit visible (Owner) + fetch /audit
  5. compose un contenu (POST /content via le formulaire Studio) → banner OK
  6. file d'approbation affiche le nouveau contenu (GET /approvals)
  7. audit panel montre 'content.submit' (refresh après submit → GET /audit)
  8. approve (POST /approve) → banner OK
  9. audit panel montre 'approval.approve'
 10. /metrics scrapé par le navigateur (counter + histogram présents)

Requiert : backend sur BACKEND_URL (défaut http://127.0.0.1:8000) + Playwright chromium.
CI : pip install playwright && playwright install --with-deps chromium && python tests/test_e2e.py
"""
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def wait_health(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001,S110
            pass
        time.sleep(1)
    return False


def demo_token():
    req = urllib.request.Request(BASE + "/api/auth/demo", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["token"]


if not wait_health():
    print(f"IMPOSSIBLE DE JOINDRE {BASE}/api/health (timeout).")
    sys.exit(2)

print(f"Backend reachable at {BASE}")
tok = demo_token()
ck(bool(tok), True, "demo token obtenu (API /api/auth/demo)")

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    context = browser.new_context()
    # injecter le token AVANT le chargement → état post-login (app-enhancements.js
    # lit localStorage 'revpulse_jwt' directement, de façon autonome)
    context.add_init_script(f"localStorage.setItem('revpulse_jwt', {json.dumps(tok)})")
    page = context.new_page()

    # --- 1. SPA chargée ---
    print("\n=== 1. SPA chargée par le backend ===")
    page.goto(BASE + "/", wait_until="load")
    page.wait_for_function("() => typeof window.RevPulseStudio === 'object'", timeout=10000)
    ck(True, True, "page chargée + app-enhancements.js exécuté (window.RevPulseStudio)")

    # --- 2. FAB visible (token injecté → whenReady passe le callback) ---
    print("\n=== 2. FAB Studio visible (token injecté) ===")
    page.wait_for_selector("#rp-fab", state="visible", timeout=8000)
    fab_disp = page.eval_on_selector("#rp-fab", "el => getComputedStyle(el).display")
    ck(fab_disp != "none", True, f"FAB #rp-fab visible (display={fab_disp})")

    # --- 3. Drawer Studio ouvert ---
    print("\n=== 3. Drawer Studio ouvert après clic FAB ===")
    page.click("#rp-fab")
    page.wait_for_selector("#rp-studio.open", timeout=5000)
    ck(True, True, "drawer #rp-studio.open")

    # --- 4. refresh() tourné + section audit visible (Owner) + fetch /audit ---
    print("\n=== 4. refresh() + section audit (Owner) + fetch /audit ===")
    # #rp-tid peuplé ("· <tid>") prouve que refresh() a fetché /api/tenants
    page.wait_for_function(
        '() => { var t=document.getElementById("rp-tid"); return t && t.textContent.includes("·"); }',
        timeout=10000)
    ck(True, True, "refresh() a tourné (#rp-tid peuplé via /api/tenants)")
    asec_disp = page.eval_on_selector("#rp-audit-sec", "el => getComputedStyle(el).display")
    ck(asec_disp != "none", True, f"section audit visible (Owner, display={asec_disp})")
    # renderAudit a résolu : #rp-audit a des enfants (rows OU message 'Aucune action')
    page.wait_for_function(
        '() => { var b=document.getElementById("rp-audit"); return b && b.children.length>0; }',
        timeout=10000)
    ck(True, True, "fetch /audit terminé (#rp-audit peuplé)")

    # --- 5. Composer un contenu ---
    print("\n=== 5. Composer un contenu (POST /content via formulaire Studio) ===")
    page.fill("#rp-c-title", "E2E test post")
    page.fill("#rp-c-body", "Contenu de test généré par Playwright E2E (P6).")
    # clearer tout banner résiduel avant l'action
    page.evaluate("document.getElementById('rp-banner')?.remove()")
    page.click("#rp-c-submit")
    try:
        page.wait_for_selector("#rp-banner.ok", timeout=6000)
        ck(True, True, "banner OK 'Contenu soumis à la file'")
    except PWTimeout:
        ck(False, True, "banner OK soumission (timeout)")

    # --- 6. File d'approbation affiche le nouveau contenu ---
    print("\n=== 6. File d'approbation (GET /approvals) ===")
    page.wait_for_function(
        '() => document.getElementById("rp-pending").textContent.includes("E2E test post")',
        timeout=10000)
    ck(True, True, "file #rp-pending contient 'E2E test post'")

    # --- 7. Audit panel montre 'content.submit' ---
    print("\n=== 7. Audit panel — 'content.submit' après compose ===")
    page.wait_for_function(
        '() => document.getElementById("rp-audit").textContent.includes("content.submit")',
        timeout=10000)
    ck(True, True, "audit panel contient 'content.submit' (refresh → GET /audit)")

    # --- 8. Approve ---
    print("\n=== 8. Approve (POST /approve) ===")
    page.evaluate("document.getElementById('rp-banner')?.remove()")
    page.click('[data-act="approve"]')
    try:
        page.wait_for_selector("#rp-banner.ok", timeout=6000)
        ck(True, True, "banner OK 'Approuvé'")
    except PWTimeout:
        ck(False, True, "banner OK approve (timeout)")

    # --- 9. Audit panel montre 'approval.approve' ---
    print("\n=== 9. Audit panel — 'approval.approve' après approve ===")
    page.wait_for_function(
        '() => document.getElementById("rp-audit").textContent.includes("approval.approve")',
        timeout=10000)
    ck(True, True, "audit panel contient 'approval.approve'")

    # --- 10. /metrics scrapé par le navigateur ---
    print("\n=== 10. /metrics scrapé par le navigateur ===")
    page.goto(BASE + "/metrics", wait_until="domcontentloaded")
    body = page.inner_text("body")
    ck("revpulse_http_requests_total" in body, True, "/metrics contient le counter (browser)")
    ck("revpulse_http_request_duration_seconds" in body, True, "/metrics contient l'histogram (browser)")

    browser.close()

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
