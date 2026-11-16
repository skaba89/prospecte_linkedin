#!/usr/bin/env bash
# Sauvegarde la base Postgres RevPulse (pg_dump --clean, compressé gzip + rotation keep-N).
#
# Usage :
#   ./scripts/backup_db.sh                                       # via docker compose exec db
#   PGHOST=localhost PGUSER=revpulse PG_DB=revpulse ./scripts/backup_db.sh  # pg_dump local
#
# Env :
#   BACKUP_DIR      (défaut ./backups)   — dossier de destination
#   RETENTION_KEEP  (défaut 10)          — garde les N dumps les plus récents
#   COMPOSE_SERVICE (défaut db)          — service postgres (docker compose)
#   PG_DB           (défaut revpulse)    — nom de la base
#   PG_USER         (défaut revpulse)    — rôle postgres
#
# --clean --if-exists : le dump inclut DROP ... IF EXISTS avant chaque CREATE
#   → la restauration (restore_db.sh) est idempotente (écrase proprement un schéma existant).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_KEEP="${RETENTION_KEEP:-10}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-db}"
PG_DB="${PG_DB:-revpulse}"
PG_USER="${PG_USER:-revpulse}"

mkdir -p "$BACKUP_DIR"
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

# Rotation : garde uniquement les RETENTION_KEEP dumps les plus récents.
deleted=0
while IFS= read -r f; do
  rm -f "$f"
  echo "  rotation (keep $RETENTION_KEEP): supprimé $(basename "$f")"
  deleted=$((deleted + 1))
done < <(ls -1t "$BACKUP_DIR"/revpulse_*.sql.gz 2>/dev/null | tail -n +$((RETENTION_KEEP + 1)))

echo "✓ Backup OK : $OUT ($(du -h "$OUT" | cut -f1), rotation: $deleted supprimé(s))"
