#!/usr/bin/env python3
"""test_security.py — validation statique AppSec (P9) : secrets + deps + hachage.

Vérifie (sans runtime externe) :
  - scan_secrets.sh existe + exécutable + bash -n valide + 7 patterns (ghp_/github_pat_/
    AKIA/PRIVATE KEY/sk_live_/xox/AIza) + allowlist + s'exécute exit 0 (code propre)
  - audit_deps.sh existe + exécutable + bash -n valide + pip-audit + npm audit + skip
    si pas de package-lock.json
  - backend/app/security.py : bcrypt importé + hash_password (bcrypt.hashpw) +
    verify_password (bcrypt.checkpw)
  - bcrypt présent dans backend/requirements.txt
  - config.py lit les secrets depuis l'env (os.getenv) — pas de JWT secret hardcoded
  - Makefile : cible audit-deps

Le scan E2E (scripts/audit_deps.sh) nécessite pip-audit/npm → non exécuté ici ; lancer
via `make audit-deps`.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def bash_n_ok(path):
    r = subprocess.run(["bash", "-n", str(path)], check=False, capture_output=True, text=True)
    return r.returncode == 0


def read(path):
    return Path(path).read_text(encoding="utf-8")


# ─── scan_secrets.sh ──────────────────────────────────────────────────────────
print("=== scan_secrets.sh ===")
sc = SCRIPTS / "scan_secrets.sh"
ck(sc.exists(), True, "scan_secrets.sh existe")
ck(sc.stat().st_mode & 0o111 != 0, True, "scan_secrets.sh exécutable")
ck(bash_n_ok(sc), True, "scan_secrets.sh bash -n valide")
src = read(sc)
ck("ghp_[A-Za-z0-9]{36}" in src, True, "pattern GitHub PAT ghp_")
ck("github_pat_" in src, True, "pattern GitHub fine-grained PAT")
ck("AKIA[0-9A-Z]{16}" in src, True, "pattern AWS access key AKIA")
ck("PRIVATE KEY" in src, True, "pattern private key header")
ck("sk_live_" in src, True, "pattern Stripe sk_live_")
ck("xox[baprs]" in src, True, "pattern Slack xox")
ck("AIza" in src, True, "pattern Google API key AIza")
ck(".env.example" in src, True, "allowlist .env.example")
ck("./tests/*" in src, True, "allowlist tests/")
ck("docker-compose" in src, True, "allowlist docker-compose")
# exécution réelle : le code committé doit être propre (exit 0)
r = subprocess.run(["bash", str(sc)], check=False, capture_output=True, text=True)
ck(r.returncode, 0, "scan_secrets.sh exit 0 (code committé propre)")

# ─── audit_deps.sh ────────────────────────────────────────────────────────────
print("\n=== audit_deps.sh ===")
ad = SCRIPTS / "audit_deps.sh"
ck(ad.exists(), True, "audit_deps.sh existe")
ck(ad.stat().st_mode & 0o111 != 0, True, "audit_deps.sh exécutable")
ck(bash_n_ok(ad), True, "audit_deps.sh bash -n valide")
asrc = read(ad)
ck("pip-audit" in asrc, True, "référence pip-audit")
ck("npm audit" in asrc, True, "référence npm audit")
ck("package-lock.json" in asrc, True, "skip gracieux si pas de package-lock.json")
ck("--strict" in asrc, True, "flag --strict (CI gating optionnel)")

# ─── hachage bcrypt (backend/app/security.py) ─────────────────────────────────
print("\n=== hachage bcrypt (security.py) ===")
sec = read(ROOT / "backend/app/security.py")
ck("import bcrypt" in sec, True, "bcrypt importé")
ck("bcrypt.hashpw" in sec, True, "hash_password utilise bcrypt.hashpw")
ck("bcrypt.gensalt" in sec, True, "bcrypt.gensalt (salt aléatoire)")
ck("bcrypt.checkpw" in sec, True, "verify_password utilise bcrypt.checkpw")
reqs = read(ROOT / "backend/requirements.txt")
ck("bcrypt" in reqs, True, "bcrypt présent dans requirements.txt")

# ─── pas de secret hardcoded (config env-driven) ──────────────────────────────
print("\n=== pas de secret hardcoded ===")
cfg = read(ROOT / "backend/app/config.py")
ck("BaseSettings" in cfg or "os.getenv" in cfg or "os.environ" in cfg, True, "config.py env-driven (BaseSettings / os.getenv)")
# grep : aucune affectation littérale ≥ 24 chars sur une var *_secret/*_password/*_token/*_key
r = subprocess.run(
    ["grep", "-rEnIi",
     r"(jwt_?secret|api_?key|secret_?key|password|access_?token)\s*=\s*[\"'][^\"']{24,}[\"']",
     str(ROOT / "backend/app")],
    check=False, capture_output=True, text=True,
)
ck(r.stdout.strip(), "", "aucun secret ≥ 24 chars hardcoded dans backend/app/")

# ─── Makefile : cible audit-deps ──────────────────────────────────────────────
print("\n=== Makefile ===")
mk = read(ROOT / "Makefile")
ck(any(line.startswith("audit-deps:") for line in mk.splitlines()), True, "cible audit-deps:")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
