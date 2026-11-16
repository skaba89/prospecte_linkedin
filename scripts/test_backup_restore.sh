#!/usr/bin/env bash
# Test bout-en-bout backup → restore (round-trip) sur un postgres éphémère.
#
# Cycle : docker postgres éphémère → alembic upgrade head → seed (1 user + 1 tenant)
#         → backup → DROP SCHEMA (simule une perte) → restore → vérifie les comptes.
#
# Prérequis : docker + deps backend (alembic, sqlalchemy, psycopg2-binary).
# CI : exécuté dans le job `docker` (P10.15) + localement : make backup-test.
# P10.17 : dump isolé dans ./backups-test/ (jamais mélangé aux backups réels ./backups/).
set -euo pipefail

PGPORT="${PGPORT:-55432}"
PGUSER="revpulse"
PGDB="revpulse"
PGPASS="revpulse"
CONTAINER="revpulse-backup-test"

cd "$(dirname "$0")/.."
mkdir -p ./backups-test

echo "=== 1/7 démarre postgres éphémère (port $PGPORT) ==="
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER="$PGUSER" -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB="$PGDB" \
  -p "$PGPORT:5432" postgres:16-alpine >/dev/null
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; rm -rf ./backups-test' EXIT

echo "=== 2/7 attend que postgres soit prêt ==="
ready=no
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U "$PGUSER" -d "$PGDB" >/dev/null 2>&1; then ready=yes; break; fi
  sleep 1
done
[ "$ready" = yes ] || { echo "✗ postgres éphémère non prêt."; exit 2; }

URL="postgresql+psycopg2://${PGUSER}:${PGPASS}@localhost:${PGPORT}/${PGDB}"

echo "=== 3/7 schéma (alembic upgrade head) ==="
( cd backend && REVPULSE_DATABASE_URL="$URL" alembic upgrade head )

echo "=== 4/7 seed (1 user + 1 tenant, FK owner_id) ==="
docker exec "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 --quiet \
  -c "INSERT INTO users (id, email, name, password_hash) VALUES ('usr-test', 't@t.io', 'Test', 'x');" \
  -c "INSERT INTO tenants (id, name, plan, owner_id) VALUES ('tnt-test', 'Test', 'Starter', 'usr-test');"
echo "  seed: 1 user, 1 tenant"

echo "=== 5/7 backup ==="
BACKUP_CONTAINER="$CONTAINER" PGUSER="$PGUSER" PG_DB="$PGDB" BACKUP_DIR=./backups-test \
  ./scripts/backup_db.sh
DUMP="$(ls -1t ./backups-test/revpulse_*.sql.gz | head -1)"
echo "  dump: $DUMP"

echo "=== 6/7 drop schéma public (simule une perte de données) ==="
docker exec "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 --quiet \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "=== 7/7 restore + verify ==="
BACKUP_CONTAINER="$CONTAINER" PGUSER="$PGUSER" PG_DB="$PGDB" \
  ./scripts/restore_db.sh "$DUMP" --yes

T="$(docker exec "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -tAc "SELECT count(*) FROM tenants;")"
U="$(docker exec "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -tAc "SELECT count(*) FROM users;")"
echo "  verify: tenants=$T, users=$U"
if [ "$T" = "1" ] && [ "$U" = "1" ]; then
  echo "✓ ROUND-TRIP OK : backup + restore préservent les données (tenants=1, users=1)"
else
  echo "✗ ROUND-TRIP FAIL : attendu tenants=1 users=1, got tenants=$T users=$U"
  exit 1
fi
