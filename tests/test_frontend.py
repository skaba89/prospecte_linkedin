#!/usr/bin/env python3
"""test_frontend.py — vérifications de sécurité statiques (P2 hardening frontend).

Script autonome (aucune dépendance hors stdlib) : `python tests/test_frontend.py`.
Vérifie que le durcissement P2 est bien en place et non régressé :
  - nginx : headers de sécurité + server_tokens off + port 8080 + /docs masqué
  - frontend JS : pas d'eval/document.write, esc() défini et attribut-safe,
    handler 401 dans apiFetch (purge token + retour login)
  - Dockerfiles : non-root + healthcheck ; frontend sur nginx-unprivileged:8080
  - compose : CORS strict (pas de '*'), port 8080:8080, frontend attend backend healthy

Exit 0 si tout passe, 1 sinon. Intégré au CI (.github/workflows/ci.yml job `frontend`).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# ─── nginx.conf ──────────────────────────────────────────────────────────────
@check("nginx: server_tokens off")
def _():
    assert "server_tokens off" in read("frontend/nginx.conf")


@check("nginx: X-Frame-Options DENY")
def _():
    assert 'X-Frame-Options "DENY"' in read("frontend/nginx.conf")


@check("nginx: X-Content-Type-Options nosniff")
def _():
    assert 'X-Content-Type-Options "nosniff"' in read("frontend/nginx.conf")


@check("nginx: Referrer-Policy présent")
def _():
    assert "Referrer-Policy" in read("frontend/nginx.conf")


@check("nginx: Permissions-Policy présent")
def _():
    assert "Permissions-Policy" in read("frontend/nginx.conf")


@check("nginx: CSP avec connect-src 'self' + frame-ancestors 'none'")
def _():
    c = read("frontend/nginx.conf")
    assert "Content-Security-Policy" in c
    assert "connect-src 'self'" in c
    assert "frame-ancestors 'none'" in c


@check("nginx: HSTS présent")
def _():
    assert "Strict-Transport-Security" in read("frontend/nginx.conf")


@check("nginx: listen 8080 (port non privilégié)")
def _():
    assert re.search(r"listen\s+8080", read("frontend/nginx.conf"))


@check("nginx: /docs et /openapi.json NON proxyés")
def _():
    c = read("frontend/nginx.conf")
    assert "proxy_pass http://backend:8000/docs" not in c
    assert "proxy_pass http://backend:8000/openapi.json" not in c


# ─── frontend JS ─────────────────────────────────────────────────────────────
@check("index.html: pas d'eval()")
def _():
    assert not re.search(r"\beval\s*\(", read("frontend/public/index.html"))


@check("index.html: pas de document.write")
def _():
    assert "document.write" not in read("frontend/public/index.html")


@check("index.html: esc() défini et attribut-safe (échappe \" et ')")
def _():
    c = read("frontend/public/index.html")
    m = re.search(r"const esc\s*=", c)
    assert m, "const esc introuvable"
    # ligne de définition complète
    line = c[m.start(): c.find("\n", m.start())]
    assert "&quot;" in line, "esc n'échappe pas les guillemets doubles"
    assert "&#39;" in line, "esc n'échappe pas les guillemets simples"


@check("index.html: esc() utilisé (≥ 100 occurrences)")
def _():
    assert len(re.findall(r"esc\(", read("frontend/public/index.html"))) >= 100


@check("index.html: apiFetch gère le 401 (purge token + retour login)")
def _():
    c = read("frontend/public/index.html")
    assert re.search(r"status\s*===\s*401", c), "branche 401 manquante dans apiFetch"
    assert "apiSetTok(null)" in c


@check("app-enhancements.js: pas d'eval() ni document.write")
def _():
    c = read("frontend/public/app-enhancements.js")
    assert not re.search(r"\beval\s*\(", c)
    assert "document.write" not in c


@check("app-enhancements.js: esc() défini (échappement données serveur)")
def _():
    c = read("frontend/public/app-enhancements.js")
    assert re.search(r"function esc\s*\(", c) or "esc=" in c


# ─── Dockerfiles ─────────────────────────────────────────────────────────────
@check("backend/Dockerfile: USER non-root + healthcheck")
def _():
    c = read("backend/Dockerfile")
    assert re.search(r"^USER\s+app", c, re.M)
    assert "HEALTHCHECK" in c


@check("frontend/Dockerfile: nginx-unprivileged + EXPOSE 8080 + healthcheck")
def _():
    c = read("frontend/Dockerfile")
    assert "nginxinc/nginx-unprivileged" in c
    assert "EXPOSE 8080" in c
    assert "HEALTHCHECK" in c


# ─── docker-compose.yml ──────────────────────────────────────────────────────
@check("compose: CORS strict (pas de '*') en défaut")
def _():
    c = read("docker-compose.yml")
    assert 'REVPULSE_CORS_ORIGINS: ${REVPULSE_CORS_ORIGINS:-http://localhost:8080}' in c
    assert 'REVPULSE_CORS_ORIGINS: "*"' not in c


@check("compose: frontend port 8080:8080")
def _():
    assert '"8080:8080"' in read("docker-compose.yml")


@check("compose: frontend attend backend healthy (service_healthy)")
def _():
    c = read("docker-compose.yml")
    # Le bloc frontend dépend du backend avec condition: service_healthy
    fidx = c.find("frontend:")
    assert fidx != -1
    fblock = c[fidx:]
    assert "condition: service_healthy" in fblock


def main():
    fails = 0
    for name, fn in CHECKS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {name}  ({e})")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  ERR   {name}  ({type(e).__name__}: {e})")
    print(f"\n{len(CHECKS) - fails}/{len(CHECKS)} checks OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
