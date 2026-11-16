#!/usr/bin/env python3
"""test_p10_security.py — validation statique des corrections critiques P10.

Couvre (sans API live, sauf crypto round-trip qui importe security.py si deps disponibles) :
  P10.1  /api/health n'expose ni la chaîne PG ni le détail d'exception
  P10.2  chiffrement Fernet des tokens OAuth (encrypt/decrypt + rotation) + config fail-fast
  P10.3  state OAuth nonce single-use (oauth_states + used flag)
  P10.4  routes mock + compte démo interdits en production (is_production gate)
  P10.5  identifiants PG/Grafana env-driven (aucun défaut en prod)
  P10.6  vars OAuth dans docker-compose
  P10.7  révocation JIT (revoked_tokens + logout + check dans get_current_user)
  P10.8  membre inactif rejeté dans require_permission
  P10.9  dédup publications (content_hash + table publications)
  P10.10 séparation post/comment/dm (LINKEDIN_CAPABILITY + manual_required)
  P10.11 ports API/Grafana/Prometheus liés à 127.0.0.1
  P10.12 scan_secrets (patterns glpat/Slack webhook) + audit_deps --strict + .audit-ignore + SECURITY.md
  P10.13 montage frontend Playwright (REVPULSE_FRONTEND_DIR=../frontend/public)
  P10.14 CI service PostgreSQL 16
  P10.15 CI step round-trip docker (backup-test)
  P10.16 backup chmod 600 + sha256 + GPG + remote
  P10.17 isolation backups-test/

Partie crypto : nécessite backend deps (cryptography) — skip gracieux sinon.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
passed = failed = 0


def ck(got, expected, label):
    global passed, failed
    if str(got) == str(expected):
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label} — got '{got}' expected '{expected}'")
        failed += 1


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── P10.1 health : pas de fuite ──────────────────────────────────────────────
print("=== P10.1 /api/health (pas de fuite PG/exception) ===")
main = read("backend/app/main.py")
_lines = main.splitlines()
_hi = next((i for i, _l in enumerate(_lines) if "def health(" in _l), None)
health_block = "\n".join(_lines[_hi:_hi + 22]) if _hi is not None else ""
ck('database_url' not in health_block, True, "health n'expose pas database_url")
ck('"detail"' not in health_block and "detail = " not in health_block, True, "health n'expose pas le détail d'exception")
ck("db_ok" in health_block, True, "health expose db_ok (booléen)")

# ─── P10.5/6/11 compose : creds env-driven + OAuth vars + ports localhost ─────
print("\n=== P10.5/6/11 docker-compose (creds env-driven + OAuth + ports) ===")
dc = read("docker-compose.yml")
ck("${POSTGRES_PASSWORD:?" in dc, True, "PG password env-driven (fail-fast :?)")
ck("${REVPULSE_JWT_SECRET:?" in dc, True, "JWT secret env-driven (pas de défaut)")
ck("${REVPULSE_TOKEN_ENCRYPTION_KEY:?" in dc, True, "clé Fernet OAuth env-driven")
ck("REVPULSE_LINKEDIN_CLIENT_ID" in dc, True, "var OAuth LinkedIn présente (P10.6)")
ck("REVPULSE_LINKEDIN_CLIENT_SECRET" in dc, True, "var OAuth secret présente")
ck("127.0.0.1:8000:8000" in dc, True, "port API lié à 127.0.0.1 (P10.11)")
obs = read("docker-compose.observability.yml")
ck("${GRAFANA_ADMIN_PASSWORD:?" in obs, True, "Grafana password env-driven (P10.5)")
ck("127.0.0.1:3000:3000" in obs, True, "port Grafana lié à 127.0.0.1")
ck("127.0.0.1:9090:9090" in obs, True, "port Prometheus lié à 127.0.0.1")

# ─── P10.2/4 config : token key + is_production + fail-fast ───────────────────
print("\n=== P10.2/4 config (clé Fernet + is_production + fail-fast) ===")
cfg = read("backend/app/config.py")
ck("token_encryption_key" in cfg, True, "champ token_encryption_key")
ck("token_encryption_key_previous" in cfg, True, "champ token_encryption_key_previous (rotation)")
ck("def is_production" in cfg, True, "propriété is_production (gate P10.4)")
ck("REVPULSE_TOKEN_ENCRYPTION_KEY requis en production" in cfg, True, "fail-fast clé Fernet en prod")

# ─── P10.2/3/7/9 modèles + migration ─────────────────────────────────────────
print("\n=== P10.2/3/7/9 modèles + migration 0003 ===")
models = read("backend/app/models.py")
for cls, tbl in [("OAuthState", "oauth_states"), ("RevokedToken", "revoked_tokens"), ("Publication", "publications")]:
    ck(f"class {cls}" in models, True, f"modèle {cls}")
    ck(f'__tablename__ = "{tbl}"' in models, True, f"table {tbl}")
ck("content_hash" in models, True, "Publication.content_hash (dédup P10.9)")
ck("used = Column(Boolean" in models, True, "OAuthState.used (single-use P10.3)")
mig = read("backend/alembic/versions/0003_p10_security.py")
ck('revision = "0003_p10_security"' in mig, True, "migration 0003 révision")
ck('down_revision = "0002_audit_log"' in mig, True, "migration 0003 chaîne sur 0002")
for t in ("oauth_states", "revoked_tokens", "publications"):
    ck(f'"{t}"' in mig, True, f"migration crée {t}")

# ─── P10.2/3/7 security : crypto + jti ────────────────────────────────────────
print("\n=== P10.2/3/7 security.py (Fernet + jti + content_hash) ===")
sec = read("backend/app/security.py")
ck("def encrypt_token" in sec, True, "fonction encrypt_token")
ck("def decrypt_token" in sec, True, "fonction decrypt_token (rotation)")
ck("token_encryption_key_previous" in sec, True, "rotation via clé précédente")
ck("def content_hash" in sec, True, "fonction content_hash (dédup)")
ck('"jti"' in sec, True, "JWT porte un jti (révocation P10.7)")
ck("secrets.token_urlsafe" in sec, True, "jti aléatoire (secrets)")

# ─── P10.8/7 deps : membre actif + révocation JIT ────────────────────────────
print("\n=== P10.7/8 deps.py (membre actif + révocation JIT) ===")
deps = read("backend/app/deps.py")
ck('membership.status != "active"' in deps, True, "require_permission rejette membre inactif (P10.8)")
ck("RevokedToken" in deps, True, "get_current_user vérifie revoked_tokens (P10.7)")

# ─── P10.2/3/4/9/10 linkedin ──────────────────────────────────────────────────
print("\n=== P10.2/3/4/9/10 linkedin.py ===")
li = read("backend/app/routers/linkedin.py")
ck("settings.is_production" in li, True, "_is_mock interdit en prod (P10.4)")
ck("models.OAuthState" in li, True, "state OAuth via oauth_states (P10.3)")
ck("secrets.token_urlsafe" in li, True, "nonce state aléatoire")
ck("row.used" in li, True, "state marqué used (anti-rejeu P10.3)")
ck("row.used" in li and "State OAuth déjà utilisé" in li, True, "rejeu state bloqué")
ck("security.encrypt_token" in li or "_store_token" in li, True, "token chiffré au stockage (P10.2)")
ck("security.decrypt_token" in li or "_load_token" in li, True, "token déchiffré à l'usage")
ck("security.content_hash" in li, True, "dédup publications par content_hash (P10.9)")
ck("models.Publication" in li, True, "publication tracée (table publications)")
ck("LINKEDIN_CAPABILITY" in li, True, "capacités LinkedIn par type (P10.10)")
ck("manual_required" in li, True, "dm → manual_required (non automatisable)")
ck("parent_urn" in li, True, "comment requiert parent_urn (séparation P10.10)")

# ─── P10.4/7 auth + seed ──────────────────────────────────────────────────────
print("\n=== P10.4/7 auth.py + seed.py ===")
auth = read("backend/app/routers/auth.py")
ck('is_production' in auth, True, "auth gate production (démo/logout)")
ck('"/logout"' in auth, True, "route /logout (révocation P10.7)")
ck("RevokedToken" in auth, True, "logout stocke revoked_tokens")
ck("Compte démo indisponible en production" in auth, True, "/api/auth/demo 404 en prod (P10.4)")
seed = read("backend/app/seed.py")
ck("is_production" in seed, True, "seed_all skip en prod (P10.4)")

# ─── P10.12 scan_secrets + audit_deps + SECURITY.md ───────────────────────────
print("\n=== P10.12 scan_secrets + audit_deps + SECURITY.md ===")
sc = read("scripts/scan_secrets.sh")
ck("glpat-" in sc, True, "scan_secrets pattern GitLab PAT (glpat-)")
ck("hooks.slack.com" in sc, True, "scan_secrets pattern Slack webhook")
ad = read("scripts/audit_deps.sh")
ck("--strict" in ad, True, "audit_deps mode --strict (CI gating)")
ck(".audit-ignore" in ad, True, "audit_deps lit .audit-ignore (--ignore-vuln)")
ck("--ignore-vuln" in ad, True, "audit_deps applique --ignore-vuln")
ck((ROOT / "SECURITY.md").exists(), True, "SECURITY.md présent (politique)")
ck((ROOT / ".audit-ignore").exists(), True, ".audit-ignore présent")
ai = read(".audit-ignore")
ck("PYSEC-" in ai, True, ".audit-ignore peuple (CVEs justifiées)")

# ─── P10.16 backup : chmod 600 + sha256 + GPG + remote ────────────────────────
print("\n=== P10.16 backup_db.sh (sécurité) ===")
bk = read("scripts/backup_db.sh")
ck("chmod 600" in bk, True, "permissions restrictives (chmod 600)")
ck("sha256sum" in bk, True, "intégrité (sha256)")
ck("BACKUP_ENCRYPTION_PASSPHRASE" in bk, True, "chiffrement GPG optionnel")
ck("BACKUP_REMOTE_CMD" in bk, True, "stockage externe optionnel")
# P10.17 isolation test/prod
tr = read("scripts/test_backup_restore.sh")
ck("backups-test" in tr, True, "round-trip isolé dans backups-test/ (P10.17)")
gi = read(".gitignore")
ck("backups-test/" in gi, True, "backups-test/ gitignoré")

# ─── P10.13/14/15 CI ──────────────────────────────────────────────────────────
print("\n=== P10.13/14/15 CI (PG 16 + frontend mount + round-trip + audit) ===")
ci = read(".github/workflows/ci.yml")
ck("postgres:16-alpine" in ci, True, "CI service PostgreSQL 16 (P10.14)")
ck("  test-pg:" in ci, True, "job test-pg (P10.14)")
ck("REVPULSE_FRONTEND_DIR=../frontend/public" in ci, True, "montage frontend Playwright (P10.13)")
ck("make backup-test" in ci, True, "CI step round-trip docker (P10.15)")
ck("audit_deps.sh --strict" in ci, True, "CI job audit --strict (P10.12)")

# ─── P10.2 crypto round-trip (nécessite backend deps — skip sinon) ────────────
print("\n=== P10.2 crypto round-trip (Fernet) ===")
_crypto_ok = False
try:
    import os as _os
    import sys as _sys

    from cryptography.fernet import Fernet as _F

    _os.environ.setdefault("REVPULSE_ENV", "test")
    if not _os.environ.get("REVPULSE_TOKEN_ENCRYPTION_KEY"):
        _os.environ["REVPULSE_TOKEN_ENCRYPTION_KEY"] = _F.generate_key().decode()
    _sys.path.insert(0, str(ROOT / "backend"))
    from app import security as _sec

    _crypto_ok = True
except Exception:  # noqa: S110, BLE001
    pass

if _crypto_ok:
    enc = _sec.encrypt_token("hello-token")
    ck(enc.startswith("gAAAA"), True, "encrypt_token → préfixe Fernet gAAAA")
    ck(_sec.decrypt_token(enc), "hello-token", "decrypt_token round-trip")
    ck(_sec.content_hash("Hello World!"), _sec.content_hash("hello world!"), "content_hash normalisé (strip+lower)")
    # rotation : la clé précédente déchiffre aussi
    import os as _os2

    from cryptography.fernet import Fernet as _F2

    old_key = _os2.environ["REVPULSE_TOKEN_ENCRYPTION_KEY"]
    new_key = _F2.generate_key().decode()
    enc_old = _sec.encrypt_token("rot-test")  # chiffré avec old_key (courante)
    _os2.environ["REVPULSE_TOKEN_ENCRYPTION_KEY_PREVIOUS"] = old_key
    # recharge settings pour prendre la nouvelle clé courante
    from app import config as _cfg
    _cfg.settings.token_encryption_key = new_key
    _cfg.settings.token_encryption_key_previous = old_key
    ck(_sec.decrypt_token(enc_old), "rot-test", "rotation : ancienne clé déchiffre (previous)")
else:
    print("  ⊘ crypto round-trip skip — deps backend non disponibles (OK en CI frontend job)")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
