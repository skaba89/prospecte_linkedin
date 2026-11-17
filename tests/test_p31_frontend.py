#!/usr/bin/env python3
"""test_p31_frontend.py — validation statique du frontend Career (P31).

Vérifie (sans serveur) :
  - Les 7 pages Career + index + 2 assets partagés existent.
  - Chaque page contient le viewport meta (responsive), le link CSS + le script JS partagés.
  - Chaque page référence les bons endpoints API Career (P34-P38).
  - Le design system CSS contient les composants clés (sidebar, cards, kanban, badges, responsive breakpoints).
  - Le client JS partagé contient l'auth guard, l'API client, le nav rendering, les helpers (score, status badge).
  - Pas de bouton/fictif sans wiring : chaque action référencée appelle un endpoint réel.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend" / "public" / "career"
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if got == expected:
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


print("=== test_p31_frontend.py — P31 Frontend Career (validation statique) ===\n")

# ─── Pages + assets existent ──────────────────────────────────────────────────
PAGES = ["login.html", "dashboard.html", "profile.html", "cvs.html",
         "search.html", "matching.html", "applications.html", "index.html"]
ASSETS = ["assets/career.css", "assets/career.js"]
print("--- Fichiers présents ---")
for p in PAGES + ASSETS:
    ck((FE / p).exists(), True, f"career/{p} existe")

# ─── Viewport meta (responsive) sur chaque page ───────────────────────────────
print("\n--- Responsive : viewport meta ---")
for p in PAGES:
    html = (FE / p).read_text(encoding="utf-8")
    ck('name="viewport"' in html, True, f"career/{p} a viewport meta")

# ─── Shared CSS + JS inclus sur les pages protégées (pas login/index forcément) ─
print("\n--- Assets partagés inclus ---")
for p in ["dashboard.html", "profile.html", "cvs.html", "search.html", "matching.html", "applications.html", "login.html", "index.html"]:
    html = (FE / p).read_text(encoding="utf-8")
    ck("assets/career.css" in html, True, f"career/{p} link career.css")
    ck("assets/career.js" in html, True, f"career/{p} script career.js")

# ─── Endpoints API référencés par page (P34-P38) ─────────────────────────────
print("\n--- Endpoints API Career référencés (vrais wiring, pas de fictif) ---")
ENDPOINT_MAP = {
    "login.html": ["/api/auth/login", "/api/auth/register", "/api/auth/demo"],
    "dashboard.html": ["/api/career/profile", "/api/career/cv", "/api/career/applications",
                       "/api/career/jobs/connectors", "/api/career/matching/search"],
    "profile.html": ["/api/career/profile", "/api/career/referential/jobs"],
    "cvs.html": ["/api/career/cv", "/api/career/cv/text", "/api/career/cv/pdf", "/api/career/cv/docx",
                 "/api/career/cv/", "/activate", "/transferrable"],
    "search.html": ["/api/career/jobs/connectors", "/api/career/jobs/search", "/api/career/jobs/postings",
                    "/api/career/saved-searches"],
    "matching.html": ["/api/career/cv", "/api/career/matching/search", "/api/career/matching/"],
    "applications.html": ["/api/career/applications", "/api/career/cv"],
}
for page, endpoints in ENDPOINT_MAP.items():
    html = (FE / page).read_text(encoding="utf-8")
    for ep in endpoints:
        ck(ep in html, True, f"career/{page} référence {ep}")

# ─── Anti-probabilité disclaimer (P36 invariant) sur matching + dashboard ────
print("\n--- Invariant P36 : disclaimer anti-probabilité ---")
for p in ["matching.html", "dashboard.html"]:
    html = (FE / p).read_text(encoding="utf-8")
    has_disc = "probabilité de recrutement" in html or "pas une probabilité" in html or "compatibilité" in html
    ck(has_disc, True, f"career/{p} disclaimer anti-probabilité (score = compatibilité)")

# ─── Anti-invention cover letter (P37/P38 invariant) sur applications ────────
print("\n--- Invariant P37/P38 : anti-invention cover letter ---")
html = (FE / "applications.html").read_text(encoding="utf-8")
ck("anti-invention" in html.lower() or "rien n'est inventé" in html.lower() or "rien n\\'est inventé" in html.lower(),
   True, "applications.html mentionne anti-invention cover letter")

# ─── Design system CSS : composants clés ──────────────────────────────────────
print("\n--- Design system CSS (composants) ---")
css = (FE / "assets/career.css").read_text(encoding="utf-8")
for comp in [".rp-sidebar", ".rp-card", ".rp-kanban", ".rp-badge", ".rp-stat", ".rp-table",
             ".rp-btn-primary", ".rp-score", ".rp-toast", ".rp-empty", ".rp-auth-wrap"]:
    ck(comp in css, True, f"CSS contient {comp}")
# Responsive breakpoints
ck("@media (max-width: 980px)" in css, True, "CSS breakpoint 980px (sidebar → drawer)")
ck("@media (max-width: 640px)" in css, True, "CSS breakpoint 640px (mobile)")
ck("clamp(" in css, True, "CSS utilise clamp() (fluid typography)")

# ─── Client JS : auth guard + API client + nav + helpers ──────────────────────
print("\n--- Client JS partagé (career.js) ---")
js = (FE / "assets/career.js").read_text(encoding="utf-8")
for sym in ["requireAuth", "RP.api", "renderNav", "bootstrap", "isLoggedIn", "setSession",
            "clearSession", "statusBadge", "scoreHtml", "connectorStatusBadge", "formatDate",
            "revpulse_jwt", "Authorization", "Bearer"]:
    ck(sym in js, True, f"career.js contient {sym}")
# Logout endpoint
ck("/api/auth/logout" in js, True, "career.js appelle /api/auth/logout")

# ─── Aucune page fictive : chaque page protégée a requireAuth ─────────────────
print("\n--- Anti-page-fictive : guard d'auth sur pages protégées ---")
for p in ["dashboard.html", "profile.html", "cvs.html", "search.html", "matching.html", "applications.html"]:
    html = (FE / p).read_text(encoding="utf-8")
    ck("requireAuth" in html, True, f"career/{p} appelle RP.requireAuth() (guard)")

# ─── Login : tabs login/register + demo ───────────────────────────────────────
print("\n--- Login : tabs + demo ---")
html = (FE / "login.html").read_text(encoding="utf-8")
ck("data-tab=" in html, True, "login.html tabs login/register")
ck("rp-demo-btn" in html, True, "login.html bouton démo 1-clic")

# ─── JS syntax : extraire les inline scripts + node --check ───────────────────
print("\n--- JS syntax (node --check sur inline scripts + career.js) ---")
import shutil  # noqa: E402
import subprocess  # noqa: E402
node = shutil.which("node")
if not node:
    print("  ⚠ node non disponible — validation JS syntaxe ignorée (couvert par E2E Playwright).")
else:
    for p in PAGES:
        html = (FE / p).read_text(encoding="utf-8")
        blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
        if not blocks:
            continue
        tmp = ROOT / f".tmp_check_{p.replace('/', '_')}.js"
        tmp.write_text("\n".join(blocks), encoding="utf-8")
        r = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
        ck(r.returncode, 0, f"career/{p} inline JS syntaxe valide")
        tmp.unlink(missing_ok=True)
    # career.js
    r = subprocess.run([node, "--check", str(FE / "assets" / "career.js")], capture_output=True, text=True)
    ck(r.returncode, 0, "career/assets/career.js syntaxe valide")

print()
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
