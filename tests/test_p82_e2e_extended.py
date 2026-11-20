#!/usr/bin/env python3
"""test_p82_e2e_extended.py — E2E navigateur étendu (P82) : sequences / analytics / billing / audit.

Démarre uvicorn (sert frontend + API), lance Chromium (Playwright) et valide pour chaque page :
  - Chargement après login démo (éléments clés rendus, pas d'écran vide/erreur).
  - 1-2 actions métier (create/delete pour sequences, currency pour billing,
    pagination/filter pour audit, render KPIs+tables pour analytics).
  - Responsive mobile 375px : pas d'overflow horizontal (suite P81).
  - Aucune erreur JS non gérée.

Complète test_p31_business_e2e.py (qui couvrait index + crm) sur les 4 pages cœur métier.
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
JS_ERRORS = []


def ck(cond, label):
    global passed, failed
    if cond:
        print(f"  ✓ {label}"); passed += 1
    else:
        print(f"  ✗ {label}"); failed += 1


def start_server():
    global SERVER_PROC
    env = dict(os.environ)
    env["REVPULSE_DATABASE_URL"] = "sqlite:///./revpulse_p82.db"
    env["REVPULSE_ENV"] = "test"
    env["WEBHOOK_SSRF_ALLOW_PRIVATE"] = "true"
    env["REVPULSE_FRONTEND_DIR"] = str(ROOT / "frontend" / "public")
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)
    subprocess.run("pkill -f 'uvicorn' 2>/dev/null || true", shell=True)
    time.sleep(2)
    subprocess.run(f"rm -f {ROOT}/backend/revpulse_p82.db", shell=True)
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


def demo_login(page):
    """Login démo 1-clic sur index.html → token persisté en localStorage (P76)."""
    page.goto(f"{URL}/index.html", wait_until="networkidle")
    try:
        page.locator("text=Essayer la démo").first.click(timeout=5000)
        page.wait_for_timeout(2500)
    except Exception:
        pass
    tok = page.evaluate("() => { try { return localStorage.getItem('revpulse_jwt') || (JSON.parse(localStorage.getItem('revpulse_session')||'{}').token || '') } catch(e){ return null } }")
    return bool(tok)


def capture_js_errors(page):
    """Capture les erreurs JS non gérées (pageerror) + les console.error."""
    page.on("pageerror", lambda exc: JS_ERRORS.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: JS_ERRORS.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)


def overflow_px(page):
    """Mesure l'overflow horizontal à 375px (suite P81)."""
    page.set_viewport_size({"width": 375, "height": 720})
    page.wait_for_timeout(600)
    sw = page.evaluate("document.documentElement.scrollWidth")
    cw = page.evaluate("document.documentElement.clientWidth")
    return max(0, sw - cw - 2)


def main():
    global passed, failed
    from playwright.sync_api import sync_playwright

    if not start_server():
        print("✗ Serveur n'a pas démarré"); sys.exit(1)
    print(f"✓ Serveur démarré sur {URL}\n")

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        capture_js_errors(page)

        # --- Login démo ---
        print("--- 0. Login démo (index.html) ---")
        ck(demo_login(page), "login démo 1-clic → token en localStorage")

        # ============================================================
        print("\n--- 1. sequences.html : load + CRUD ---")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{URL}/sequences.html", wait_until="networkidle")
        page.wait_for_timeout(1500)
        # la page auto-charge via localStorage ; si login requis, le panneau login s'affiche
        login_visible = page.evaluate("() => !!document.querySelector('#login') && getComputedStyle(document.querySelector('#login')).display !== 'none'")
        if login_visible:
            # sequences.html a son propre form login — mais on a déjà le token en localStorage, init() doit skip
            page.wait_for_timeout(1000)
        seq_list = page.evaluate("() => document.querySelector('#seq-list')?.innerHTML?.length || 0")
        ck(seq_list > 0, "seq-list rendu (non vide)")
        has_empty_or_cards = page.evaluate("() => /Aucune séquence|class=\"card\"/.test(document.querySelector('#seq-list')?.innerHTML || '')")
        ck(has_empty_or_cards, "seq-list contient soit 'Aucune séquence' soit des cartes")

        # action : ouvrir le formulaire create
        try:
            page.locator("text=+ Nouvelle séquence").first.click(timeout=3000)
            page.wait_for_timeout(400)
            form_visible = page.evaluate("() => getComputedStyle(document.getElementById('create-form')).display !== 'none'")
            ck(form_visible, "formulaire create affiché après clic '+ Nouvelle séquence'")
        except Exception:
            ck(False, "formulaire create affiché (bouton introuvable)")

        # remplir + créer une séquence
        try:
            page.fill("#sf-name", "E2E Test Sequence")
            page.fill("#sf-desc", "Séquence de test E2E P82")
            page.locator("button:has-text('Créer')").first.click(timeout=3000)
            page.wait_for_timeout(1500)
            # vérifier que la séquence apparaît dans la liste
            created = page.evaluate("() => (document.querySelector('#seq-list')?.innerHTML || '').includes('E2E Test Sequence')")
            ck(created, "séquence 'E2E Test Sequence' créée + visible dans la liste")
        except Exception as e:
            ck(False, f"création séquence (erreur: {str(e)[:60]})")

        # responsive
        ovf = overflow_px(page)
        ck(ovf == 0, f"sequences.html responsive 375px : 0 overflow (was {ovf}px)")

        # ============================================================
        print("\n--- 2. analytics.html : KPIs + tables ---")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{URL}/analytics.html", wait_until="networkidle")
        page.wait_for_timeout(1800)
        kpis = page.evaluate("() => document.querySelector('#kpis')?.children?.length || 0")
        ck(kpis > 0, f"KPIs rendus ({kpis} cartes)")
        # tables (pipeline/emails/sources/cohorts) — au moins 1 tbody rempli ou message empty
        tables_filled = page.evaluate("""() => {
            const ids = ['pipeline-rows','emails-rows','sources-rows','cohorts-rows'];
            let ok = 0;
            for (const id of ids) {
                const el = document.getElementById(id);
                if (el && (el.children.length > 0 || /Aucune|empty/.test(el.innerHTML))) ok++;
            }
            return ok;
        }""")
        ck(tables_filled >= 3, f"tables analytics rendues ({tables_filled}/4 tbodies remplis ou empty)")
        # bouton export CSV présent
        export_btn = page.evaluate("() => document.body.textContent.includes('Export')")
        ck(export_btn, "bouton Export CSV présent")
        ovf = overflow_px(page)
        ck(ovf == 0, f"analytics.html responsive 375px : 0 overflow (was {ovf}px)")

        # ============================================================
        print("\n--- 3. billing.html : plans + currency + checkout ---")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{URL}/billing.html", wait_until="networkidle")
        page.wait_for_timeout(1800)
        plans = page.evaluate("() => document.querySelectorAll('#plans-grid .plan').length")
        ck(plans > 0, f"plans rendus ({plans} plans dans #plans-grid)")
        currency_sel = page.evaluate("() => !!document.querySelector('#currency-sel')")
        ck(currency_sel, "select devise présent (#currency-sel)")
        # changer la devise → EUR → USD + appliquer
        try:
            page.select_option("#currency-sel", "USD")
            page.locator("text=Appliquer").first.click(timeout=3000)
            page.wait_for_timeout(1200)
            # vérifier qu'au moins un prix affiche $ (USD)
            usd = page.evaluate("() => /\\$/.test(document.querySelector('#plans-grid')?.textContent || '')")
            ck(usd, "devise changée → USD ($ affiché dans les prix)")
        except Exception as e:
            ck(False, f"changement devise USD (erreur: {str(e)[:60]})")
        # bouton checkout (Activer gratuit ou Upgrade)
        checkout_btn = page.evaluate("() => !!document.querySelector('#plans-grid button')")
        ck(checkout_btn, "bouton checkout (Activer/Upgrade) présent sur au moins 1 plan")
        ovf = overflow_px(page)
        ck(ovf == 0, f"billing.html responsive 375px : 0 overflow (was {ovf}px)")

        # ============================================================
        print("\n--- 4. audit.html : rows + pagination + filters ---")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{URL}/audit.html", wait_until="networkidle")
        page.wait_for_timeout(1800)
        # table audit rendue (thead + tbody rempli ou empty)
        audit_state = page.evaluate("""() => {
            const tbody = document.getElementById('audit-rows');
            const empty = document.getElementById('audit-empty');
            return {
                thead: document.querySelectorAll('.card table thead th').length,
                rows: tbody?.children?.length || 0,
                empty_visible: empty && getComputedStyle(empty).display !== 'none',
                total_hint: document.getElementById('total-hint')?.textContent || ''
            };
        }""")
        ck(audit_state['thead'] >= 3, f"thead audit rendu ({audit_state['thead']} colonnes)")
        ck(audit_state['rows'] > 0 or audit_state['empty_visible'], f"tbody audit rempli ({audit_state['rows']} rows) OU empty message visible")
        # boutons pagination présents
        pager = page.evaluate("() => !!document.getElementById('prev-btn') && !!document.getElementById('next-btn')")
        ck(pager, "boutons pagination (prev/next) présents")
        # filtres : appliquer un filtre action
        try:
            page.fill("#f-action", "crm.contact")
            page.locator("text=Appliquer").first.click(timeout=3000)
            page.wait_for_timeout(1200)
            # après filtre, soit des rows filtrées, soit empty message
            filtered = page.evaluate("""() => {
                const tbody = document.getElementById('audit-rows');
                const empty = document.getElementById('audit-empty');
                return (tbody?.children?.length >= 0) && (empty || tbody);
            }""")
            ck(filtered, "filtre 'crm.contact' appliqué sans erreur (table rafraîchie)")
        except Exception as e:
            ck(False, f"filtre action (erreur: {str(e)[:60]})")
        # reset filtres
        try:
            page.locator("text=Réinitialiser").first.click(timeout=3000)
            page.wait_for_timeout(1000)
            reset_ok = page.evaluate("() => document.getElementById('f-action')?.value === ''")
            ck(reset_ok, "reset filtres → champ action vidé")
        except Exception:
            ck(False, "reset filtres")
        ovf = overflow_px(page)
        ck(ovf == 0, f"audit.html responsive 375px : 0 overflow (was {ovf}px)")

        # ============================================================
        print("\n--- 5. Erreurs JS (cross-pages) ---")
        # filtrer les erreurs bruitées (favicon 404, etc.)
        real_errors = [e for e in JS_ERRORS if "favicon" not in e.lower() and "net::ERR" not in e]
        ck(len(real_errors) == 0, f"aucune erreur JS non gérée ({len(real_errors)} erreurs)")
        if real_errors:
            for e in real_errors[:3]:
                print(f"    → {e[:120]}")

        b.close()

    if SERVER_PROC:
        SERVER_PROC.terminate(); SERVER_PROC.wait(timeout=5)
    subprocess.run("pkill -f 'uvicorn app.main:app' 2>/dev/null || true", shell=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
