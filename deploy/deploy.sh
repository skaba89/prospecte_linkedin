#!/bin/bash
# =============================================================================
# RevPulse — déploiement 1 commande sur VPS Ubuntu 22.04+
# Usage :
#   1. SSH vers ton VPS : ssh root@TON_VPS
#   2. git clone https://github.com/skaba89/prospecte_linkedin.git /opt/revpulse
#   3. cd /opt/revpulse
#   4. cp deploy/.env.prod.template .env && nano .env  (éditer DOMAIN, clés, etc.)
#   5. bash deploy/deploy.sh
#
# Ou one-liner (clone + prompt .env + deploy) :
#   curl -fsSL https://raw.githubusercontent.com/skaba89/prospecte_linkedin/main/deploy/deploy.sh | bash
# =============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/revpulse}"
REPO_URL="${REPO_URL:-https://github.com/skaba89/prospecte_linkedin.git}"

echo "🚀 RevPulse — déploiement VPS"
echo "   APP_DIR: $APP_DIR"
echo ""

# --- 1. Vérif OS ---
if ! grep -qiE "ubuntu 2[0-9]|debian 1[1-2]" /etc/os-release 2>/dev/null; then
  echo "⚠️  OS non testé (Ubuntu 22.04+/Debian 11+ recommandé). Continuer ? [y/N]"
  read -r ans; [ "${ans:-}" != "y" ] && exit 1
fi

# --- 2. Docker + compose ---
if ! command -v docker &>/dev/null; then
  echo "📦 Installation Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  echo "✅ Docker présent : $(docker --version)"
fi

# --- 3. Clone / pull repo ---
if [ -d "$APP_DIR/.git" ]; then
  echo "📥 Mise à jour du repo..."
  git -C "$APP_DIR" pull --ff-only
else
  echo "📥 Clone du repo..."
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# --- 4. Vérif kit deploy présent ---
for f in deploy/docker-compose.prod.yml deploy/Caddyfile deploy/.env.prod.template; do
  if [ ! -f "$f" ]; then
    echo "❌ Fichier manquant : $f"
    echo "   Le kit deploy/ n'est pas dans le repo. Vérifie que tu es sur la branche main + que le PR #74 est mergé."
    exit 1
  fi
done

# --- 5. .env ---
if [ ! -f .env ]; then
  echo "📝 Création de .env depuis .env.prod.template..."
  cp deploy/.env.prod.template .env
  echo ""
  echo "⚠️  .env créé avec des valeurs PLACEHOLDER. Tu DOIS l'éditer avant de continuer :"
  echo "   nano $APP_DIR/.env"
  echo ""
  echo "   Variables OBLIGATOIRES à renseigner :"
  echo "   - DOMAIN (ton domaine pointant vers ce VPS)"
  echo "   - ACME_EMAIL (email pour Let's Encrypt)"
  echo "   - POSTGRES_PASSWORD (mot de passe fort)"
  echo "   - REVPULSE_JWT_SECRET (≥32 chars)"
  echo "   - REVPULSE_TOKEN_ENCRYPTION_KEY (Fernet key)"
  echo "   - REVPULSE_LINKEDIN_CLIENT_ID + SECRET (app LinkedIn)"
  echo "   - REVPULSE_SMTP_* (serveur mail)"
  echo "   - REVPULSE_STRIPE_* (clés Stripe test/live)"
  echo ""
  echo "   Relance ensuite : bash deploy/deploy.sh"
  exit 0
fi

# --- 6. Vérif .env complété (pas de PLACEHOLDER) ---
if grep -qiE "CHANGE_ME|CHANGE_|revpulse\.exemple\.com" .env; then
  echo "❌ .env contient encore des placeholders (CHANGE_ME / exemple.com)."
  echo "   Édite-le : nano $APP_DIR/.env"
  exit 1
fi

# --- 7. Vérif DNS (le domaine doit pointer vers ce VPS) ---
DOMAIN=$(grep -E "^DOMAIN=" .env | cut -d= -f2 | tr -d '"')
VPS_IP=$(curl -fsSL ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
DOMAIN_IP=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' || echo "?")
echo "🌐 Domaine: $DOMAIN"
echo "   IP VPS: $VPS_IP"
echo "   IP DNS du domaine: $DOMAIN_IP"
if [ "$DOMAIN_IP" != "$VPS_IP" ]; then
  echo "⚠️  Le DNS de $DOMAIN ne pointe pas vers ce VPS ($DOMAIN_IP ≠ $VPS_IP)."
  echo "   Caddy ne pourra pas obtenir le certificat Let's Encrypt."
  echo "   Continuer quand même ? [y/N]"
  read -r ans; [ "${ans:-}" != "y" ] && exit 1
fi

# --- 8. Build + up ---
echo ""
echo "🔨 Build + démarrage de la stack..."
docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build

# --- 9. Migrations DB ---
echo ""
echo "🗄️  Application des migrations Alembic..."
sleep 5
docker compose -f deploy/docker-compose.prod.yml exec -T backend alembic upgrade head

# --- 10. Healthcheck ---
echo ""
echo "🩺 Healthcheck..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsSL "https://$DOMAIN/api/health" 2>/dev/null | grep -q '"status":"ok"'; then
    echo "✅ Backend healthy : https://$DOMAIN/api/health"
    break
  fi
  echo "   tentative $i/10 (Caddy peut mettre ~30s à obtenir le cert)..."
  sleep 10
done

echo ""
echo "🎉 RevPulse déployé : https://$DOMAIN"
echo ""
echo "Prochaines étapes :"
echo "  1. Crée ton compte : https://$DOMAIN (lien 'Créer un compte')"
echo "  2. Configure le provider IA : Paramètres → IA providers → ajouter clé GLM/OpenAI"
echo "  3. Webhook Stripe : https://$DOMAIN/api/billing/webhook (events: checkout.session.completed, customer.subscription.*)"
echo "  4. App LinkedIn : vérifie que l'URL de redirection = https://$DOMAIN/api/linkedin/callback"
echo ""
echo "Logs : docker compose -f deploy/docker-compose.prod.yml logs -f"
echo "Stop : docker compose -f deploy/docker-compose.prod.yml down"
