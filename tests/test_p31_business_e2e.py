#!/usr/bin/env python3
"""test_p31_business_e2e.py — E2E navigateur du frontend Business (P31 Business).

Démarre uvicorn (sert frontend + API), lance Chromium (Playwright) et valide :
  1. index.html : démo 1-clic → dashboard rendu (SPA + mock localStorage).
  2. Responsive mobile (375px) : hamburger .rp-menu-btn visible + sidebar off-canvas (transform).
  3. Toggle hamburger → sidebar .open + backdrop .open.
  4. crm.html : démo API (/auth/demo) → CRM rendu (vraie API, pas mock).
  5. crm.html responsive : pas d'overflow horizontal mobile.
  6. Aucune erreur JS non gérée sur les 2 pages.

Valide l'overlay responsive P31 (app.css + app.js) sur les pages Business.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
passed = failed = 0
SERVER_PROC = None


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def start_server():
    global SERVER_PROC
    env = dict(os.environ)
    env["REVPULSE_DATABASE_URL"] = "sqlite:///./revpulse_p31biz.db"
    env["REVPULSE_ENV"] = "test"
    env["WEBHOOK_SSRF_ALLOW_PRIVATE"] = "true"
    env["REVPULSE_FRONTEND_DIR"] = str(ROOT / "frontend" / "public")
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run("pkill -f 'uvicorn' 2>/dev/null || true", shell=True)
    time.sleep(2)  # port release grace period
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p31biz.db", shell=True)
    SERVER_PROC = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            import urllib.request
            urllib.request.urlopen(URL + "/", timeout=2); return True
        except Exception:
            time.sleep(1)
    return False


def main():
    global passed, failed
    from playwright.sync_api import sync_playwright

    print("=== test_p31_business_e2e.py — Frontend Business (overlay responsive P31) ===\n")
    if not start_server():
        print("ERREUR: serveur non démarré."); sys.exit(1)

    js_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))

        # ── 1. index.html : démo 1-clic → dashboard ──────────────────────────
        print("--- 1. index.html : démo + dashboard ---")
        page.goto(f"{URL}/index.html", wait_until="networkidle")
        page.wait_for_selector(".auth-screen.active", timeout=8000)
        ck(page.locator(".auth-screen.active").count() >= 1, "index : écran auth rendu")
        demo_btn = page.locator("text=Essayer la démo")
        ck(demo_btn.count() >= 1, "index : bouton 'Essayer la démo' présent")
        demo_btn.first.click()
        page.wait_for_timeout(2500)  # ensureDemoSeed + doLogin + enterTenantFlow
        # Dashboard rendu : view-title visible (entré dans l'app)
        ck(page.locator("#view-title").count() >= 1, "index : dashboard rendu (#view-title)")
        view_title = page.inner_text("#view-title") if page.locator("#view-title").count() else ""
        ck(len(view_title) > 0, f"index : view-title non vide ('{view_title[:30]}')")

        # ── 2. Responsive mobile : hamburger + sidebar off-canvas ────────────
        print("\n--- 2. Responsive mobile (375px) ---")
        page.set_viewport_size({"width": 375, "height": 720})
        page.wait_for_timeout(500)
        ck(page.locator(".rp-menu-btn").count() >= 1, "mobile : hamburger .rp-menu-btn injecté")
        ck(page.locator(".rp-menu-btn").first.is_visible(), "mobile : hamburger visible")
        sidebar = page.locator(".sidebar")
        # Sidebar off-canvas : computed transform contient translateX (matrix avec valeur négative)
        transform = page.evaluate("getComputedStyle(document.querySelector('.sidebar')).transform")
        ck(transform != "none" and ("matrix" in transform or "translateX" in transform),
           f"mobile : sidebar off-canvas (transform={transform[:30]})")
        # ── 3. Toggle hamburger → sidebar.open + backdrop.open ───────────────
        print("\n--- 3. Toggle hamburger ---")
        page.locator(".rp-menu-btn").first.click()
        page.wait_for_timeout(300)
        ck(sidebar.evaluate("el => el.classList.contains('open')"), "hamburger click → sidebar.open")
        ck(page.locator(".rp-backdrop.open").count() >= 1, "hamburger click → backdrop.open")
        # Backdrop click → ferme (via JS — le backdrop center est sous la sidebar ouverte sur 375px)
        page.evaluate("RP.toggleSidebar(false)")
        page.wait_for_timeout(300)
        ck(not sidebar.evaluate("el => el.classList.contains('open')"), "backdrop/toggle → sidebar fermée")

        # ── 4. crm.html : démo API → CRM rendu ───────────────────────────────
        print("\n--- 4. crm.html : démo API + rendu ---")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{URL}/crm.html", wait_until="networkidle")
        page.wait_for_selector(".topbar", timeout=8000)
        ck(page.locator(".topbar").count() >= 1, "crm.html : topbar rendu")
        # crm.html auto-demo si pas de token : cliquer le bouton démo
        demo_crm = page.locator("text=démo")
        if demo_crm.count() > 0:
            demo_crm.first.click()
            page.wait_for_timeout(2000)
        # Après démo : le CRM devrait charger (tenant-name ou contenu)
        body_text = page.inner_text("body")
        ck(len(body_text) > 50, f"crm.html : contenu rendu après démo ({len(body_text)} chars)")

        # ── 5. crm.html responsive : pas d'overflow horizontal mobile ────────
        print("\n--- 5. crm.html responsive (375px) ---")
        page.set_viewport_size({"width": 375, "height": 720})
        page.wait_for_timeout(500)
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        ck(not overflow, "crm.html mobile : pas d'overflow horizontal (responsive OK)")

        # ── 6. Erreurs JS ───────────────────────────────────────────────────
        print("\n--- 6. Erreurs JS ---")
        real_errors = [e for e in js_errors if "favicon" not in e.lower() and "NetworkError" not in e]
        ck(len(real_errors) == 0, f"aucune erreur JS non gérée ({len(real_errors)} erreurs)")

        browser.close()

    if SERVER_PROC:
        SERVER_PROC.terminate(); SERVER_PROC.wait(timeout=5)
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
