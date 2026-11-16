#!/usr/bin/env bash
# Sauvegarde Postgres : pg_dump --clean gzippé + chmod 600 + sha256 (intégrité)
# + rotation keep-N + chiffrement GPG optionnel + stockage externe optionnel (P10.16).
#
# Usage :
#   ./scripts/backup_db.sh                                       # via docker compose exec db
#   PGHOST=localhost PGUSER=revpulse PG_DB=revpulse ./scripts/backup_db.sh  # pg_dump local
#
# Env :
#   BACKUP_DIR                  (défaut ./backups)  — dossier de destination
#   RETENTION_KEEP              (défaut 10)         — garde les N backups les plus récents
#   COMPOSE_SERVICE             (défaut db)         — service postgres (docker compose)
#   PG_DB                       (défaut revpulse)   — nom de la base
#   PG_USER                     (défaut revpulse)   — rôle postgres
#   BACKUP_ENCRYPTION_PASSPHRASE                    — si set, chiffre le dump (gpg --symmetric → .sql.gz.gpg)
#   BACKUP_REMOTE_CMD           (ex: "rclone copy")  — si set, commande d'upload externe (reçoit le fichier final)
#
# --clean --if-exists : dump idempotent (restauration écrase proprement un schéma existant).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_KEEP="${RETENTION_KEEP:-10}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-db}"
PG_DB="${PG_DB:-revpulse}"
PG_USER="${PG_USER:-revpulse}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/revpulse_${TS}.sql.gz"

echo "→ Backup de la base '$PG_DB' → $OUT"

if command -v pg_dump >/dev/null 2>&1; then
  pg_dump -U "$PG_USER" -d "$PG_DB" --clean --if-exists | gzip > "$OUT"
elif command -v docker >/dev/null 2>&1 && docker compose ps --services 2>/dev/null | grep -qx "$COMPOSE_SERVICE"; then
  docker compose exec -T "$COMPOSE_SERVICE" pg_dump -U "$PG_USER" -d "$PG_DB" --clean --if-exists | gzip > "$OUT"
else
  echo "✗ pg_dump introuvable sur le PATH et stack docker non démarrée." >&2
  echo "  Démarre la stack (make up) ou installe postgres-client." >&2
  exit 2
fi

# P10.16 : permissions restrictives (0600) + checksum sha256 (intégrité).
chmod 600 "$OUT"
FINAL="$OUT"

# P10.16 : chiffrement GPG optionnel (si passphrase fournie) → .sql.gz.gpg, plaintext supprimé.
if [ -n "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  ENC="$OUT.gpg"
  if gpg --batch --yes --passphrase "$BACKUP_ENCRYPTION_PASSPHRASE" --symmetric "$OUT" 2>/dev/null \
        --output "$ENC"; then
    chmod 600 "$ENC"
    rm -f "$OUT"  # on ne conserve que la version chiffrée (at-rest encryption)
    FINAL="$ENC"
    echo "  chiffrement GPG : $ENC"
  else
    echo "  ⚠ chiffrement GPG échoué — backup plaintext conservé ($OUT)." >&2
  fi
fi

# Checksum du fichier final (chiffré ou non) — pour vérifier l'intégrité au restore.
sha256sum "$FINAL" > "$FINAL.sha256"
chmod 600 "$FINAL.sha256"

# P10.16 : stockage externe optionnel (rclone / aws s3 / scp …). La commande reçoit le fichier final.
if [ -n "${BACKUP_REMOTE_CMD:-}" ]; then
  echo "  upload distant : $BACKUP_REMOTE_CMD \"$FINAL\""
  if eval "$BACKUP_REMOTE_CMD \"\$FINAL\"" 2>/dev/null; then :; else
    echo "  ⚠ upload distant échoué (backup local conservé)." >&2
  fi
fi

# Rotation : garde les RETENTION_KEEP backups les plus récents (tous formats .sql.gz / .sql.gz.gpg)
# + leurs .sha256. Tri par mtime (newest first).
deleted=0
mapfile -t ALL < <(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'revpulse_*.sql.gz' -o -name 'revpulse_*.sql.gz.gpg' \) \
    -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
for f in "${ALL[@]:$RETENTION_KEEP}"; do
  [ -z "$f" ] && continue
  rm -f "$f" "$f.sha256"
  echo "  rotation (keep $RETENTION_KEEP): supprimé $(basename "$f")"
  deleted=$((deleted + 1))
done

echo "✓ Backup OK : $FINAL ($(du -h "$FINAL" | cut -f1), rotation: $deleted supprimé(s))"
echo "  intégrité : sha256sum -c $FINAL.sha256"
