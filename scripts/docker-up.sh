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
  # P70-fix2 : génération en STDLIB only (os+base64+secrets) — aucun package 3rd-party requis
  # sur l'hôte (cryptography n'est PAS installé sur le Python hôte Windows/MINGW64 par défaut).
  # Fernet key = 32 bytes aléatoires, base64-urlsafe (= 44 chars avec padding '=').
  # Fallback openssl si python3 absent.
  if command -v python3 >/dev/null 2>&1; then
    JWT_SEC=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    FERNET=$(python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
    PG_PWD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  elif command -v openssl >/dev/null 2>&1; then
    JWT_SEC=$(openssl rand -base64 48 | tr -d '\n')
    FERNET=$(openssl rand 32 | base64 | tr '+/' '-_' | tr -d '\n')
    PG_PWD=$(openssl rand -hex 24)
  else
    echo "✗ python3 OU openssl requis pour générer les secrets. Installe-en un des deux."
    exit 1
  fi
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
# P70-fix : Stripe désactivé pour Docker local (pas de compte Stripe requis → mock mode).
# ⚠️ En prod RÉELLE : passer à true + renseigner STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET.
REVPULSE_STRIPE_ENABLED=false
REVPULSE_DEMO_EMAIL=demo@revpulse.io
REVPULSE_DEMO_PASSWORD=demo1234
# P71-fix : login démo activé pour Docker local (/api/auth/demo fonctionne en env=production).
# ⚠️ En prod RÉELLE : laisser false (défaut) — pas de login démo en prod (sécurité P10.4).
REVPULSE_DEMO_ENABLED=true
REVPULSE_RATE_LIMIT_ENABLED=true
REVPULSE_LOGIN_RATE_LIMIT=5/minute
REVPULSE_SMTP_ENABLED=false
REVPULSE_LINKEDIN_CLIENT_ID=
REVPULSE_LINKEDIN_CLIENT_SECRET=
REVPULSE_LINKEDIN_REDIRECT_URI=http://localhost:8080/api/linkedin/callback
REVPULSE_LINKEDIN_MOCK=true
EOF
  echo "✓ .env créé (secrets générés localement — ne PAS committer)."
  # P70-fix3 : si un volume pgdata existe déjà (d'un run précédent avec un ancien password),
  # le wipe — sinon Postgres skip l'init ("Database directory appears to contain a database")
  # et garde l'ANCIEN password → mismatch avec le nouveau .env → backend crash (auth fail).
  if docker volume inspect revpulse_pgdata >/dev/null 2>&1; then
    echo "→ Volume revpulse_pgdata existant détecté (ancien password Postgres possible)..."
    echo "  Wipe pour forcer la réinitialisation de Postgres avec le nouveau password..."
    docker compose down -v >/dev/null 2>&1 || true
    echo "✓ Volumes wipeés (Postgres va se réinitialiser avec le nouveau password du .env)."
  fi
fi

# ─── 1b. Pre-flight : valide les secrets du .env AVANT de lancer docker ────────
# P70-fix2 : échec claire + tôt si un secret est invalide (ex: Fernet key = fallback
# "REPLACE_WITH_FERNET_KEY" si la génération a raté sur un hôte sans cryptography).
echo "→ Pre-flight : validation des secrets .env..."
ENV_FILE=.env
JWT_CHK=$(grep -E "^REVPULSE_JWT_SECRET=" "$ENV_FILE" | cut -d= -f2-)
FERNET_CHK=$(grep -E "^REVPULSE_TOKEN_ENCRYPTION_KEY=" "$ENV_FILE" | cut -d= -f2-)
PG_CHK=$(grep -E "^POSTGRES_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
ERR=""
[ ${#JWT_CHK} -ge 32 ] || ERR="$ERR\n  ✗ REVPULSE_JWT_SECRET < 32 chars (actuel: ${#JWT_CHK} chars)"
[ "$JWT_CHK" != "change-me-in-production" ] || ERR="$ERR\n  ✗ REVPULSE_JWT_SECRET = défaut (change-me-in-production)"
# Fernet key = 44 chars base64-urlsafe (43 chars + '=' final)
echo "$FERNET_CHK" | grep -qE '^[A-Za-z0-9_-]{43}=$' || ERR="$ERR\n  ✗ REVPULSE_TOKEN_ENCRYPTION_KEY invalide (Fernet = 44 chars base64-urlsafe attendu). Valeur actuelle: '$FERNET_CHK'"
[ "$FERNET_CHK" != "REPLACE_WITH_FERNET_KEY" ] || ERR="$ERR\n  ✗ REVPULSE_TOKEN_ENCRYPTION_KEY = fallback (la génération a raté — regénère le .env)"
[ ${#PG_CHK} -ge 16 ] || ERR="$ERR\n  ✗ POSTGRES_PASSWORD < 16 chars"
if [ -n "$ERR" ]; then
  echo -e "✗ Secrets .env invalides :$ERR"
  echo "  → Supprime .env + relance ./scripts/docker-up.sh (il le régénérera avec des secrets valides)."
  echo "  → Ou corrige manuellement : voir .env.example pour le format."
  exit 1
fi
echo "✓ Secrets .env valides (JWT ≥32 chars + Fernet 44 chars + PG password ≥16 chars)."

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
