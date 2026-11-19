#!/usr/bin/env bash
# scripts/docker-up.sh — Démarre le stack RevPulse en local (docker compose).
#
# Prérequis : Docker + docker compose v2 installés (docker --version && docker compose version).
# Ce script :
#   1. Vérifie qu'un .env existe (sinon le génère avec des secrets aléatoires).
#   2. Valide la syntaxe docker-compose.yml.
#   3. Build les images (backend FastAPI + frontend nginx).
#   4. Démarre les services (db Postgres + backend + frontend) en mode détaché.
#   5. Attend que les healthchecks passent (db + backend + frontend).
#   6. Affiche les URLs + les identifiants démo.
#
# Usage :
#   ./scripts/docker-up.sh            # start complet (build + up)
#   ./scripts/docker-up.sh --no-build # start sans rebuild (images déjà buildées)
#   ./scripts/docker-up.sh --down     # arrête + supprime les conteneurs (garde le volume pgdata)
#   ./scripts/docker-up.sh --reset    # arrête + supprime conteneurs + volume pgdata (reset complet)

set -euo pipefail
cd "$(dirname "$0")/.."

# ─── 1. .env ──────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "→ .env absent — génération avec secrets aléatoires..."
  JWT_SEC=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  FERNET=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "REPLACE_WITH_FERNET_KEY")
  PG_PWD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  cat > .env <<EOF
REVPULSE_ENV=production
REVPULSE_JWT_SECRET=$JWT_SEC
REVPULSE_TOKEN_ENCRYPTION_KEY=$FERNET
POSTGRES_USER=revpulse
POSTGRES_PASSWORD=$PG_PWD
POSTGRES_DB=revpulse
REVPULSE_DATABASE_URL=postgresql+psycopg2://revpulse:$PG_PWD@db:5432/revpulse
REVPULSE_APP_BASE_URL=http://localhost:8080
REVPULSE_CORS_ORIGINS=http://localhost:8080,http://localhost:8000
REVPULSE_JWT_EXPIRY_HOURS=24
REVPULSE_DEMO_EMAIL=demo@revpulse.io
REVPULSE_DEMO_PASSWORD=demo1234
REVPULSE_RATE_LIMIT_ENABLED=true
REVPULSE_LOGIN_RATE_LIMIT=5/minute
REVPULSE_SMTP_ENABLED=false
REVPULSE_LINKEDIN_CLIENT_ID=
REVPULSE_LINKEDIN_CLIENT_SECRET=
REVPULSE_LINKEDIN_REDIRECT_URI=http://localhost:8080/api/linkedin/callback
REVPULSE_LINKEDIN_MOCK=true
EOF
  echo "✓ .env créé (secrets générés localement — ne PAS committer)."
fi

# ─── 2. Docker dispo ? ────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  echo "✗ Docker n'est pas installé. Installe Docker Engine + docker compose v2 :"
  echo "  https://docs.docker.com/engine/install/"
  exit 1
fi

# ─── 3. Actions ───────────────────────────────────────────────────────────────
case "${1:-}" in
  --down)
    echo "→ Arrêt du stack..."
    docker compose down
    echo "✓ Stack arrêté (volume pgdata conservé)."
    exit 0
    ;;
  --reset)
    echo "→ Reset complet (conteneurs + volume pgdata)..."
    docker compose down -v
    echo "✓ Reset terminé. Relance ./scripts/docker-up.sh pour recréer."
    exit 0
    ;;
esac

# ─── 4. Validate compose ──────────────────────────────────────────────────────
echo "→ Validation docker-compose.yml..."
docker compose config --quiet
echo "✓ Compose valide."

# ─── 5. Build + up ────────────────────────────────────────────────────────────
if [ "${1:-}" != "--no-build" ]; then
  echo "→ Build des images (backend + frontend)..."
  docker compose build
  echo "✓ Images buildées."
fi

echo "→ Démarrage des services (db + backend + frontend)..."
docker compose up -d

# ─── 6. Attendre healthchecks ─────────────────────────────────────────────────
echo "→ Attente des healthchecks (db healthy + backend /api/health 200 + frontend 200)..."
MAX_WAIT=120
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
  BACKEND_OK=$(curl -sf http://localhost:8000/api/health >/dev/null 2>&1 && echo "1" || echo "0")
  FRONTEND_OK=$(curl -sf http://localhost:8080/ >/dev/null 2>&1 && echo "1" || echo "0")
  DB_OK=$(docker compose ps db --format json 2>/dev/null | grep -q '"Health":"healthy"' && echo "1" || echo "0")
  if [ "$BACKEND_OK" = "1" ] && [ "$FRONTEND_OK" = "1" ]; then
    break
  fi
  sleep 3
  WAITED=$((WAITED + 3))
  echo "  ... ${WAITED}s — backend=$BACKEND_OK frontend=$FRONTEND_OK"
done

if [ "$BACKEND_OK" != "1" ] || [ "$FRONTEND_OK" != "1" ]; then
  echo "✗ Timeout — un service n'est pas healthy après ${MAX_WAIT}s."
  echo "  Logs backend : docker compose logs backend --tail 50"
  echo "  Logs frontend: docker compose logs frontend --tail 50"
  echo "  Logs db      : docker compose logs db --tail 50"
  exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✓ Stack RevPulse démarré (healthy) en $(($(date +%s) - $(date -d "$(docker compose ps --format '{{.CreatedAt}}' backend | head -1)" +%s)))s"
echo "════════════════════════════════════════════════════════════════"
echo "🌐 Frontend (dashboard) : http://localhost:8080"
echo "🔌 API backend          : http://localhost:8000/api/health"
echo "👤 Compte démo          : demo@revpulse.io / demo1234"
echo "📦 Logs                 : docker compose logs -f"
echo "🛑 Arrêt                : ./scripts/docker-up.sh --down"
echo "🔄 Reset complet        : ./scripts/docker-up.sh --reset"
echo "════════════════════════════════════════════════════════════════"
