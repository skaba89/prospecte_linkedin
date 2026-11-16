#!/usr/bin/env bash
# Restaure la base Postgres RevPulse depuis un dump (gunzip + psql).
#
# Usage : ./scripts/restore_db.sh <dump.sql.gz> [--yes]
#
# Sécurité : confirmation interactive requise sauf --yes (évite d'écraser la prod
# par accident). Le dump doit avoir été produit avec --clean --if-exists
# (cf. backup_db.sh) pour une restauration idempotente (DROP + CREATE).
set -euo pipefail

DUMP="${1:-}"
FORCE="${2:-}"

[ -z "$DUMP" ] && { echo "Usage: $0 <dump.sql.gz> [--yes]"; exit 1; }
[ ! -f "$DUMP" ] && { echo "✗ Dump introuvable : $DUMP"; exit 1; }

PG_DB="${PG_DB:-revpulse}"
PG_USER="${PG_USER:-revpulse}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-db}"

echo "⚠️  Restauration de '$DUMP' dans la base '$PG_DB'."
echo "    Les données existantes seront écrasées (DROP + CREATE via --clean)."
if [ "$FORCE" != "--yes" ]; then
  read -rp "Continuer ? [tape 'yes'] : " ans
  [ "$ans" = "yes" ] || { echo "Abandon."; exit 1; }
fi

echo "→ Restauration en cours..."
if [ -n "${BACKUP_CONTAINER:-}" ]; then
  gunzip -c "$DUMP" | docker exec -i "$BACKUP_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 --quiet
elif command -v psql >/dev/null 2>&1; then
  gunzip -c "$DUMP" | psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 --quiet
elif command -v docker >/dev/null 2>&1 && docker compose ps --services 2>/dev/null | grep -qx "$COMPOSE_SERVICE"; then
  gunzip -c "$DUMP" | docker compose exec -T "$COMPOSE_SERVICE" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 --quiet
else
  echo "✗ psql introuvable sur le PATH et stack docker non démarrée." >&2
  exit 2
fi
echo "✓ Restauration OK depuis $DUMP"
