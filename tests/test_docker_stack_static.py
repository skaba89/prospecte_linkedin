#!/usr/bin/env python3
"""test_docker_stack_static.py — Validation statique du stack Docker RevPulse (sans daemon Docker).

Vérifie tout ce qui est validable SANS lancer Docker (le sandbox n'a pas de daemon) :
  A. docker-compose.yml : syntaxe YAML valide + services attendus (db/backend/frontend) + fail-fast secrets.
  B. Dockerfile backend : base image, COPY paths, CMD uvicorn, healthcheck /api/health, user non-root.
  C. Dockerfile frontend : nginx-unprivileged, COPY nginx.conf + public/, EXPOSE 8080, healthcheck.
  D. nginx.conf : headers sécurité (CSP/XFO/HSTS), proxy /api → backend, resolver Docker.
  E. requirements.txt : deps critiques présentes (fastapi/uvicorn/sqlalchemy/psycopg2/alembic/slowapi/bcrypt/pyotp).
  F. .env : généré avec secrets non-défaut (JWT ≥32 chars + Fernet + POSTGRES_PASSWORD).
  G. scripts/docker-up.sh + docker-healthcheck.sh : présents + exécutables + logique correcte.
  H. docs/LOCAL_DOCKER_RUNBOOK.md : présent + sections clés (prérequis/démarrage/dépannage).

Ce test tourne dans la CI (step "Static checks") sans Docker. Pour valider le stack RÉELLEMENT,
lancer `./scripts/docker-up.sh && ./scripts/docker-healthcheck.sh` sur une machine avec Docker.
"""
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(cond, label):
    global passed, failed
    print(f"  {'✓' if cond else '✗'} {label}")
    if cond:
        passed += 1
    else:
        failed += 1


def main():
    print("=== test_docker_stack_static.py — stack Docker RevPulse (validation statique) ===\n")

    # ═══ A. docker-compose.yml ═══
    print("--- A. docker-compose.yml ---")
    compose = ROOT / "docker-compose.yml"
    ck(compose.exists(), "docker-compose.yml existe")
    try:
        cfg = yaml.safe_load(compose.read_text())
        services = list(cfg.get("services", {}).keys())
        ck({"db", "backend", "frontend"}.issubset(set(services)),
           f"services db/backend/frontend présents (trouvé: {services})")
        # Fail-fast secrets : POSTGRES_PASSWORD (service db) + REVPULSE_JWT_SECRET + REVPULSE_TOKEN_ENCRYPTION_KEY (backend)
        db_env = cfg["services"]["db"].get("environment", {})
        ck("${POSTGRES_PASSWORD:?POSTGRES_PASSWORD requis" in str(db_env.get("POSTGRES_PASSWORD", "")),
           "POSTGRES_PASSWORD fail-fast :? dans service db (aucun défaut)")
        backend_env = cfg["services"]["backend"].get("environment", {})
        ck("REVPULSE_JWT_SECRET:?REVPULSE_JWT_SECRET requis" in str(backend_env.get("REVPULSE_JWT_SECRET", "")),
           "REVPULSE_JWT_SECRET fail-fast :? (≥32 chars prod)")
        ck("REVPULSE_TOKEN_ENCRYPTION_KEY:?REVPULSE_TOKEN_ENCRYPTION_KEY requis" in str(backend_env.get("REVPULSE_TOKEN_ENCRYPTION_KEY", "")),
           "REVPULSE_TOKEN_ENCRYPTION_KEY fail-fast :?")
        # Healthcheck db
        ck(cfg["services"]["db"].get("healthcheck", {}).get("test") is not None,
           "db healthcheck pg_isready présent")
        # depends_on backend → db healthy + frontend → backend healthy
        ck(cfg["services"]["backend"].get("depends_on", {}).get("db", {}).get("condition") == "service_healthy",
           "backend depends_on db: service_healthy")
        ck(cfg["services"]["frontend"].get("depends_on", {}).get("backend", {}).get("condition") == "service_healthy",
           "frontend depends_on backend: service_healthy")
        # Port binding localhost
        ports_b = cfg["services"]["backend"].get("ports", [])
        ck(any("127.0.0.1:8000" in str(p) for p in ports_b), "backend port 127.0.0.1:8000 (localhost only)")
        ports_f = cfg["services"]["frontend"].get("ports", [])
        ck(any("8080" in str(p) for p in ports_f), "frontend port 8080")
        # Volume pgdata
        ck("pgdata" in cfg.get("volumes", {}), "volume pgdata déclaré")
    except Exception as e:
        ck(False, f"compose YAML invalide: {e}")

    # ═══ B. Dockerfile backend ═══
    print("\n--- B. Dockerfile backend ---")
    df_b = (ROOT / "backend" / "Dockerfile").read_text()
    ck("FROM python:3.12-slim" in df_b, "base image python:3.12-slim")
    ck("COPY requirements.txt" in df_b and "RUN pip install --no-cache-dir -r requirements.txt" in df_b,
       "COPY requirements + pip install")
    ck("COPY app/ ./app/" in df_b and "COPY alembic.ini" in df_b and "COPY alembic/" in df_b,
       "COPY app + alembic.ini + alembic/")
    ck("uvicorn" in df_b and "app.main:app" in df_b, "CMD uvicorn app.main:app")
    ck("/api/health" in df_b and "HEALTHCHECK" in df_b, "healthcheck /api/health")
    ck("USER" in df_b or "non-root" in df_b.lower(), "user non-root (durcissement)")

    # ═══ C. Dockerfile frontend ═══
    print("\n--- C. Dockerfile frontend ---")
    df_f = (ROOT / "frontend" / "Dockerfile").read_text()
    ck("nginxinc/nginx-unprivileged" in df_f, "base nginx-unprivileged (non-root)")
    ck("COPY nginx.conf" in df_f and "COPY public/" in df_f, "COPY nginx.conf + public/")
    ck("EXPOSE 8080" in df_f, "EXPOSE 8080 (port non privilégié)")
    ck("HEALTHCHECK" in df_f and "wget" in df_f, "healthcheck wget /")

    # ═══ D. nginx.conf ═══
    print("\n--- D. nginx.conf (sécurité + proxy) ---")
    nginx = (ROOT / "frontend" / "nginx.conf").read_text()
    ck("X-Frame-Options" in nginx and "DENY" in nginx, "X-Frame-Options DENY (anti clickjacking)")
    ck("X-Content-Type-Options" in nginx and "nosniff" in nginx, "X-Content-Type-Options nosniff")
    ck("Content-Security-Policy" in nginx, "CSP présent")
    ck("Strict-Transport-Security" in nginx, "HSTS présent")
    ck("proxy_pass" in nginx and "/api" in nginx, "proxy /api → backend")
    ck("resolver 127.0.0.11" in nginx, "resolver Docker 127.0.0.11 (runtime resolution)")

    # ═══ E. requirements.txt ═══
    print("\n--- E. requirements.txt (deps critiques) ---")
    reqs = (ROOT / "backend" / "requirements.txt").read_text()
    for dep in ["fastapi", "uvicorn", "sqlalchemy", "psycopg2", "alembic", "slowapi", "bcrypt", "PyJWT", "cryptography", "pyotp"]:
        ck(dep.lower() in reqs.lower(), f"dep {dep} présente")

    # ═══ F. .env (secrets non-défaut) ═══
    print("\n--- F. .env (secrets générés) ---")
    env = ROOT / ".env"
    if env.exists():
        env_content = env.read_text()
        jwt_match = re.search(r"REVPULSE_JWT_SECRET=(\S+)", env_content)
        ck(jwt_match is not None and len(jwt_match.group(1)) >= 32 and jwt_match.group(1) != "change-me-in-production",
           f"REVPULSE_JWT_SECRET ≥32 chars + non-défaut ({len(jwt_match.group(1)) if jwt_match else 0} chars)")
        fernet_match = re.search(r"REVPULSE_TOKEN_ENCRYPTION_KEY=(\S+)", env_content)
        # Fernet key = base64-urlsafe de 32 bytes = 44 chars (lettre/chiffre/_- + '=' final)
        fk = fernet_match.group(1) if fernet_match else ""
        ck(bool(re.fullmatch(r"[A-Za-z0-9_-]{43}=", fk)),
           "REVPULSE_TOKEN_ENCRYPTION_KEY Fernet (base64-urlsafe 32 bytes = 44 chars)")
        pg_match = re.search(r"POSTGRES_PASSWORD=(\S+)", env_content)
        ck(pg_match is not None and len(pg_match.group(1)) >= 16, "POSTGRES_PASSWORD ≥16 chars")
    else:
        # .env est gitigné (secrets) — absent en CI, généré par ./scripts/docker-up.sh en local.
        # SKIP (pas de failure) — les secrets sont validés via le .env.example + le script docker-up.sh.
        print("  ℹ .env absent (gitigné — généré par ./scripts/docker-up.sh en local). SKIP section F (non bloquant).")

    # ═══ G. scripts docker-up + healthcheck ═══
    print("\n--- G. scripts helper ---")
    up = ROOT / "scripts" / "docker-up.sh"
    hc = ROOT / "scripts" / "docker-healthcheck.sh"
    ck(up.exists(), "scripts/docker-up.sh existe")
    ck(os.access(up, os.X_OK), "docker-up.sh exécutable")
    up_src = up.read_text()
    ck(".env" in up_src and "docker compose up -d" in up_src and "docker compose build" in up_src,
       "docker-up.sh : génère .env + build + up")
    ck("--down" in up_src and "--reset" in up_src, "docker-up.sh : flags --down + --reset")
    ck("/api/health" in up_src and "8080" in up_src, "docker-up.sh : healthcheck + URL frontend")
    ck(hc.exists(), "scripts/docker-healthcheck.sh existe")
    ck(os.access(hc, os.X_OK), "docker-healthcheck.sh exécutable")
    hc_src = hc.read_text()
    ck("db_ok" in hc_src and "alembic current" in hc_src and "api/auth/demo" in hc_src,
       "docker-healthcheck.sh : db_ok + alembic current + auth demo")

    # ═══ H. runbook ═══
    print("\n--- H. runbook ---")
    rb = ROOT / "docs" / "LOCAL_DOCKER_RUNBOOK.md"
    ck(rb.exists(), "docs/LOCAL_DOCKER_RUNBOOK.md existe")
    rb_src = rb.read_text()
    for section in ["Prérequis", "Démarrage rapide", "Validation du stack", "Dépannage", "Configuration avancée"]:
        ck(section in rb_src, f"runbook section '{section}'")
    ck("docker-up.sh" in rb_src and "docker-healthcheck.sh" in rb_src, "runbook référence les scripts")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
