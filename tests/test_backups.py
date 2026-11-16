#!/usr/bin/env python3
"""test_backups.py — validation statique des scripts de sauvegarde/restauration Postgres (P8).

Vérifie (sans docker, sans runtime) :
  - backup_db.sh / restore_db.sh / test_backup_restore.sh existent + exécutables
  - syntaxe bash valide (bash -n) pour les 3 scripts
  - backup_db.sh : pg_dump --clean --if-existes + gzip + rotation keep-N + vars (BACKUP_DIR,
    RETENTION_KEEP, COMPOSE_SERVICE, PG_DB/PG_USER)
  - restore_db.sh : psql + gunzip + ON_ERROR_STOP + confirmation interactive (--yes)
  - test_backup_restore.sh : round-trip docker + alembic + seed + DROP SCHEMA + restore + verify
  - Makefile : cibles backup / restore / backup-test présentes
  - backups/.gitignore ignore *.sql.gz ; backups/.gitkeep présent

Le round-trip E2E (scripts/test_backup_restore.sh) nécessite docker → non exécuté ici ;
lancer localement via `make backup-test`.
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


# ─── existence + exécutable + syntaxe ─────────────────────────────────────────
print("=== existence + exécutable + bash -n ===")
for name in ("backup_db.sh", "restore_db.sh", "test_backup_restore.sh"):
    p = SCRIPTS / name
    ck(p.exists(), True, f"{name} existe")
    ck(p.stat().st_mode & 0o111 != 0, True, f"{name} exécutable")
    ck(bash_n_ok(p), True, f"{name} bash -n valide")

# ─── backup_db.sh ─────────────────────────────────────────────────────────────
print("\n=== backup_db.sh ===")
bk = read(SCRIPTS / "backup_db.sh")
ck("pg_dump" in bk, True, "utilise pg_dump")
ck("--clean" in bk, True, "pg_dump --clean (restauration idempotente)")
ck("--if-exists" in bk, True, "pg_dump --if-exists")
ck("gzip" in bk, True, "compression gzip")
ck("RETENTION_KEEP" in bk, True, "rotation keep-N (RETENTION_KEEP)")
ck("sort -rn" in bk, True, "rotation par mtime (sort -rn, P10.16)")
ck("BACKUP_DIR" in bk, True, "var BACKUP_DIR")
ck("COMPOSE_SERVICE" in bk, True, "var COMPOSE_SERVICE (fallback docker)")
ck("PG_DB" in bk and "PG_USER" in bk, True, "vars PG_DB / PG_USER")
ck("revpulse_${TS}.sql.gz" in bk, True, "nom de dump horodaté revpulse_TS.sql.gz")
ck("chmod 600" in bk, True, "P10.16 permissions restrictives (chmod 600)")
ck("sha256sum" in bk, True, "P10.16 intégrité (sha256)")
ck("BACKUP_ENCRYPTION_PASSPHRASE" in bk, True, "P10.16 chiffrement GPG optionnel")
ck("BACKUP_REMOTE_CMD" in bk, True, "P10.16 stockage externe optionnel")

# ─── restore_db.sh ────────────────────────────────────────────────────────────
print("\n=== restore_db.sh ===")
rs = read(SCRIPTS / "restore_db.sh")
ck("psql" in rs, True, "utilise psql")
ck("gunzip" in rs, True, "décompression gunzip")
ck("ON_ERROR_STOP" in rs, True, "psql ON_ERROR_STOP=1 (échec strict)")
ck("--yes" in rs, True, "flag --yes (bypass confirmation)")
ck("read -rp" in rs, True, "confirmation interactive (read -rp)")

# ─── test_backup_restore.sh ───────────────────────────────────────────────────
print("\n=== test_backup_restore.sh (round-trip E2E) ===")
tr = read(SCRIPTS / "test_backup_restore.sh")
ck("docker run" in tr, True, "démarre postgres éphémère (docker run)")
ck("pg_isready" in tr, True, "attend pg_isready")
ck("alembic upgrade head" in tr, True, "schéma via alembic upgrade head")
ck("INSERT INTO users" in tr, True, "seed users")
ck("INSERT INTO tenants" in tr, True, "seed tenants")
ck("DROP SCHEMA public" in tr, True, "simule perte (DROP SCHEMA)")
ck("backup_db.sh" in tr, True, "appelle backup_db.sh")
ck("restore_db.sh" in tr, True, "appelle restore_db.sh")
ck("SELECT count(*) FROM tenants" in tr, True, "verify count tenants")
ck("SELECT count(*) FROM users" in tr, True, "verify count users")

# ─── Makefile : cibles backup / restore / backup-test ─────────────────────────
print("\n=== Makefile ===")
mk = read(ROOT / "Makefile")
ck(any(line.startswith("backup:") for line in mk.splitlines()), True, "cible backup:")
ck(any(line.startswith("restore:") for line in mk.splitlines()), True, "cible restore:")
ck(any(line.startswith("backup-test:") for line in mk.splitlines()), True, "cible backup-test:")

# ─── backups/.gitignore + .gitkeep ────────────────────────────────────────────
print("\n=== backups/ (gitkeep + gitignore) ===")
ck((ROOT / "backups" / ".gitkeep").exists(), True, "backups/.gitkeep présent")
gi = read(ROOT / "backups" / ".gitignore")
ck("*.sql.gz" in gi, True, "backups/.gitignore ignore *.sql.gz")
ck(".gitkeep" in gi, True, "backups/.gitignore préserve .gitkeep")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
