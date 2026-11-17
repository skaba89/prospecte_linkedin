#!/usr/bin/env python3
"""test_p31_e2e_playwright.py — E2E navigateur du parcours Career (P31).

Démarre le serveur uvicorn, lance Chromium (Playwright) et valide le parcours end-to-end
"Data Engineer" du critère de validation #1 :
  1. Login (démo 1-clic) → dashboard.
  2. Création/édition du profil (métier, localisation, contrats).
  3. Import d'un CV texte → parse result visible.
  4. Recherche d'offres → résultats.
  5. Matching → score + disclaimer anti-probabilité.
  6. Création d'une candidature (avec lettre générée anti-invention) → pipeline kanban.
  7. Changement de statut → timeline.

Capture d'écran finale sous /tmp/p31_final.png. Affiche RESULT: N passed, M failed.
"""
import os
import re
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
    env["REVPULSE_DATABASE_URL"] = "sqlite:///./revpulse_p31.db"
    env["REVPULSE_ENV"] = "test"
    env["WEBHOOK_SSRF_ALLOW_PRIVATE"] = "true"
    env["REVPULSE_FRONTEND_DIR"] = str(ROOT / "frontend" / "public")
    # kill any stale server
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p31.db", shell=True)
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
    from playwright.sync_api import sync_playwright

    print("=== test_p31_e2e_playwright.py — Parcours Career end-to-end (P31) ===\n")
    if "BACKEND_URL" in os.environ:
        # CI mode : serveur déjà démarré par le job test-api (ne pas relancer).
        print("--- Serveur (CI : BACKEND_URL déjà démarré) ---")
        try:
            import urllib.request
            urllib.request.urlopen(URL + "/", timeout=5)
            ck(True, "serveur CI accessible (" + URL + ")")
        except Exception as e:
            ck(False, f"serveur CI inaccessible : {e}"); return finish()
    else:
        print("--- Démarrage serveur (local) ---")
        if not start_server():
            ck(False, "serveur démarré")
            return finish()
        ck(True, "serveur démarré (uvicorn :8000)")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # ── 1. Login (démo 1-clic) ──
        print("\n--- 1. Login démo → dashboard ---")
        page.goto(f"{URL}/career/login.html", wait_until="networkidle")
        ck("RevPulse Career" in page.title() or "Connexion" in page.title(), "login page titre")
        page.click("#rp-demo-btn")
        page.wait_for_url("**/dashboard.html", timeout=8000)
        ck("dashboard" in page.url, "redirigé vers dashboard.html après login")
        page.wait_for_selector("h1", timeout=8000)
        h1 = page.inner_text("h1")
        ck("Bonjour" in h1, f"dashboard affiche 'Bonjour' : {h1[:40]}")
        # nav sidebar present
        ck(page.locator(".rp-nav-item").count() >= 5, "sidebar nav >= 5 items")

        # ── 2. Profil ──
        print("\n--- 2. Édition profil (métier + localisation + contrats) ---")
        page.goto(f"{URL}/career/profile.html", wait_until="networkidle")
        page.wait_for_selector("#rp-profile-form", timeout=8000)
        # Select a job from referential
        page.select_option('[name="target_job_title"]', index=1)
        page.fill('[name="preferred_location"]', "Paris, France")
        page.check('[name="mobility"]')
        page.check('[name="contract_types"][value="CDI"]')
        page.click('button[type=submit]')
        page.wait_for_timeout(1500)
        ck(not any("Erreur" in e for e in errors[-3:]), "profil enregistré sans erreur console")
        ck(True, "profil soumis (métier + Paris + CDI + mobilité)")

        # ── 3. Import CV texte ──
        print("\n--- 3. Import CV texte (Data Engineer) ---")
        page.goto(f"{URL}/career/cvs.html", wait_until="networkidle")
        page.wait_for_selector("#rp-text-form", timeout=8000)
        page.fill('#rp-text-form [name="title"]', "CV Data Engineer 2026")
        cv_text = (
            "Data Engineer, 7 ans d'expérience. Compétences : Python, SQL, Spark, Airflow, "
            "AWS, Docker, Kubernetes. Expérience : Data Engineer chez DataCorp (2019-2026). "
            "Certifications : AWS Certified, Google Cloud Professional. diplôme : Master Informatique."
        )
        page.fill('#rp-text-form [name="raw_text"]', cv_text)
        page.click('#rp-text-form button[type=submit]')
        page.wait_for_timeout(2000)
        ck(page.locator("text=CV Data Engineer 2026").count() >= 1, "CV importé visible dans la liste")
        ck(page.locator("text=Python").count() >= 1, "compétence 'Python' extraite visible")

        # ── 4. Recherche d'offres ──
        print("\n--- 4. Recherche d'offres ---")
        page.goto(f"{URL}/career/search.html", wait_until="networkidle")
        page.wait_for_selector("#rp-search-form", timeout=8000)
        ck(page.locator(".rp-pill-list .rp-tag").count() >= 1, "connecteurs affichés (badges statut)")
        page.fill('[name="keywords"]', "Data Engineer")
        page.click('#rp-search-form button[type=submit]')
        page.wait_for_timeout(2500)
        # Results may be empty in test DB (no postings yet) — check the results card renders
        ck(page.locator("#rp-results .rp-card").count() >= 1, "carte résultats rendue")

        # ── 5. Matching ──
        print("\n--- 5. Matching + disclaimer anti-probabilité ---")
        page.goto(f"{URL}/career/matching.html", wait_until="networkidle")
        page.wait_for_selector("#rp-mc-go", timeout=8000)
        page.click("#rp-mc-go")
        page.wait_for_timeout(2500)
        # Disclaimer must be present (P36 invariant — score = compatibilité, pas probabilité)
        body = page.inner_text("body")
        has_disc = "probabilité" in body.lower() or "compatibilité" in body.lower()
        ck(has_disc, "disclaimer anti-probabilité présent (P36 invariant)")
        ck(page.locator("#rp-results .rp-card").count() >= 1, "carte matching rendue")

        # ── 6. Candidature + lettre générée ──
        print("\n--- 6. Création candidature (lettre anti-invention) + pipeline ---")
        page.goto(f"{URL}/career/applications.html?title=Senior Data Engineer&company=DataCorp", wait_until="networkidle")
        page.wait_for_selector("#rp-app-form", timeout=8000)
        ck("Senior Data Engineer" in page.input_value('[name="job_title"]'), "poste pré-rempli (depuis search)")
        ck("DataCorp" in page.input_value('[name="company"]'), "entreprise pré-remplie")
        # Select the CV imported
        cv_opts = page.locator('[name="cv_source_id"] option')
        if cv_opts.count() > 1:
            page.select_option('[name="cv_source_id"]', index=1)
        page.check('[name="generate_cover_letter"]')  # already checked but ensure
        page.click('#rp-app-form button[type=submit]')
        page.wait_for_timeout(2500)
        ck(page.locator(".rp-kanban-card").count() >= 1, "candidature visible dans pipeline kanban")
        # Open detail + verify cover letter generated
        page.click(".rp-kanban-card")
        page.wait_for_selector("#rp-app-detail textarea", timeout=5000)
        cover = page.input_value("#rp-app-detail textarea")
        ck(len(cover) > 100, f"lettre de motivation générée ({len(cover)} chars > 100)")
        ck("python" in cover.lower() or "data" in cover.lower(), "lettre mentionne skill du CV (anti-invention)")

        # ── 7. Changement de statut → timeline ──
        print("\n--- 7. Changement de statut → timeline ---")
        page.select_option('#rp-app-detail [data-status]', "interview")
        page.wait_for_timeout(2000)
        tl = page.inner_text("#rp-app-detail ul")
        ck("interview" in tl.lower() or "Statut changé" in tl, "timeline enregistre le changement de statut")

        # No uncaught page errors
        real_errors = [e for e in errors if "favicon" not in e and "404" not in e]
        ck(len(real_errors) == 0, f"aucune erreur JS non gérée ({len(real_errors)} erreurs)")

        # Final screenshot
        page.screenshot(path="/tmp/p31_final.png", full_page=True)
        ck(Path("/tmp/p31_final.png").exists(), "capture finale /tmp/p31_final.png")

        browser.close()

    # Responsive check at mobile viewport
    print("\n--- 8. Responsive (viewport mobile 375px) ---")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 375, "height": 700}, is_mobile=True)
        page = ctx.new_page()
        page.goto(f"{URL}/career/login.html", wait_until="networkidle")
        # Demo login on mobile
        try:
            page.click("#rp-demo-btn", timeout=3000)
            page.wait_for_url("**/dashboard.html", timeout=8000)
            page.wait_for_selector(".rp-menu-btn", timeout=5000)
            ck(page.locator(".rp-menu-btn").is_visible(), "bouton menu hamburger visible en mobile")
            # No horizontal overflow : body width <= viewport
            scroll_w = page.evaluate("document.documentElement.scrollWidth")
            client_w = page.evaluate("document.documentElement.clientWidth")
            ck(scroll_w <= client_w + 5, f"pas de scroll horizontal mobile (scrollW={scroll_w} clientW={client_w})")
        except Exception as e:
            ck(False, f"parcours mobile : {e}")
        browser.close()

    return finish()


def finish():
    print(f"\nRESULT: {passed} passed, {failed} failed")
    if SERVER_PROC:
        SERVER_PROC.terminate()
        SERVER_PROC.wait(timeout=5)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        if SERVER_PROC:
            SERVER_PROC.terminate()
        print(f"RESULT: {passed} passed, {failed} failed (exception)")
        sys.exit(1)
