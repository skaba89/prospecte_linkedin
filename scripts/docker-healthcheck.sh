#!/usr/bin/env bash
# scripts/docker-healthcheck.sh — Valide qu'un stack RevPulse Docker tourne correctement.
#
# Vérifie :
#   1. Conteneurs db + backend + frontend en statut "running" + "healthy".
#   2. API backend : GET /api/health → 200 + db_ok:true.
#   3. Frontend nginx : GET / → 200 (index.html servi).
#   4. Proxy nginx → backend : GET /api/health via :8080 → 200.
#   5. Auth flow : POST /api/auth/demo → 200 + token.
#   6. Migrations appliquées (alembic current = head).
#
# Usage : ./scripts/docker-healthcheck.sh
# Exit 0 si tout OK, 1 sinon.

set -uo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ck() { if [ "$1" = "0" ]; then echo "  ✓ $2"; PASS=$((PASS+1)); else echo "  ✗ $2"; FAIL=$((FAIL+1)); fi; }

echo "=== docker-healthcheck — stack RevPulse ==="

# 1. Conteneurs
echo "--- Conteneurs ---"
docker compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null | head -5
docker compose ps db --format json 2>/dev/null | grep -q '"Health":"healthy"' && ck 0 "db healthy" || ck 1 "db NOT healthy"
docker compose ps backend --format json 2>/dev/null | grep -q '"Health":"healthy"' && ck 0 "backend healthy" || ck 1 "backend NOT healthy"
docker compose ps frontend --format json 2>/dev/null | grep -q '"Health":"healthy"' && ck 0 "frontend healthy" || ck 1 "frontend NOT healthy"

# 2. API backend direct
echo "--- API backend (localhost:8000) ---"
H=$(curl -sf http://localhost:8000/api/health 2>/dev/null)
[ -n "$H" ] && echo "$H" | grep -q '"status":"ok"' && ck 0 "GET /api/health → 200 status:ok" || ck 1 "GET /api/health failed"
echo "$H" | grep -q '"db_ok":true' && ck 0 "db_ok:true (backend ↔ Postgres)" || ck 1 "db_ok:false (backend ne reach pas Postgres)"

# 3. Frontend direct
echo "--- Frontend (localhost:8080) ---"
curl -sf http://localhost:8080/ >/dev/null 2>&1 && ck 0 "GET / → 200 (index.html)" || ck 1 "GET / failed"

# 4. Proxy nginx → backend
echo "--- Proxy nginx → backend (localhost:8080/api/health) ---"
curl -sf http://localhost:8080/api/health >/dev/null 2>&1 && ck 0 "GET /api/health via nginx proxy → 200" || ck 1 "nginx proxy /api failed"

# 5. Auth flow (demo)
echo "--- Auth flow ---"
R=$(curl -sf -X POST http://localhost:8000/api/auth/demo -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
echo "$R" | grep -q '"token"' && ck 0 "POST /api/auth/demo → 200 + token" || ck 1 "demo login failed"

# 6. Migrations
echo "--- Migrations ---"
CUR=$(docker compose exec -T backend alembic current 2>/dev/null | grep -oE "^[0-9a-f]+_p[0-9]+.*" | head -1)
HEAD=$(docker compose exec -T backend alembic heads 2>/dev/null | grep -oE "[0-9a-f]+_p[0-9]+.*" | head -1)
[ -n "$CUR" ] && [ "$CUR" = "$HEAD" ] && ck 0 "alembic current = head ($CUR)" || ck 1 "alembic current≠head (cur=$CUR head=$HEAD)"

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
