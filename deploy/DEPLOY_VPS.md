# RevPulse — Déploiement VPS + test réel (toutes fonctionnalités)

Guide complet pour déployer RevPulse sur un VPS avec **toutes les fonctionnalités en réel** : LinkedIn OAuth, Stripe billing, SMTP, génération IA.

> **Prérequis sandbox/local satisfait** : 19 PRs mergées (sha 52fc9ca), stack Docker fonctionnelle, 34 modules dashboard, RBAC, audit log, onboarding, invitations — tout validé en mock.

---

## 0. Ce qu'il te faut

| Élément | Pourquoi | Où l'obtenir |
|---|---|---|
| **VPS Ubuntu 22.04+** (2 vCPU, 4 Go RAM, 40 Go SSD) | Héberger la stack | Hetzner (~5€/mois), OVH, Scaleway, DigitalOcean |
| **Domaine** pointant vers le VPS (A record) | HTTPS + callback LinkedIn OAuth | Gandi, Namecheap, Cloudflare |
| **App LinkedIn Developer** | OAuth réel (login + partage LinkedIn) | https://www.linkedin.com/developers/apps |
| **Compte Stripe** (test mode OK) | Billing réel (checkout, abonnements) | https://dashboard.stripe.com |
| **SMTP** (test ou prod) | Envoi des e-mails d'invitation | Mailtrap (test gratuit), Brevo, Mailgun, SES |
| **Clé provider IA** | Génération de posts LinkedIn | OpenAI / Anthropic / GLM (config par tenant dans l'UI) |

---

## 1. Préparer le VPS

```bash
# SSH vers le VPS
ssh root@TON_VPS_IP

# MAJ + utils de base
apt update && apt upgrade -y
apt install -y git curl ufw fail2ban

# Firewall : ouvrir SSH (22), HTTP (80), HTTPS (443)
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# fail2ban (anti brute-force SSH)
systemctl enable --now fail2ban

# Créer un user non-root (recommandé)
adduser revpulse && usermod -aG sudo revpulse
su - revpulse
```

## 2. Configurer le DNS

Chez ton registrar, ajoute un **A record** :
```
revpulse.exemple.com.   IN   A   TON_VPS_IP
```
Vérifie la propagation :
```bash
dig +short revpulse.exemple.com   # doit retourner TON_VPS_IP
```

## 3. Cloner + configurer

```bash
git clone https://github.com/skaba89/prospecte_linkedin.git /opt/revpulse
cd /opt/revpulse
cp deploy/.env.prod.template .env
nano .env   # édite TOUTES les valeurs (voir §4-7 ci-dessous)
```

## 4. Configurer LinkedIn OAuth (réel)

1. Va sur https://www.linkedin.com/developers/apps → **Create app**.
2. Nom : `RevPulse`, Page LinkedIn : rattachée à une page que tu possèdes.
3. **Produits** à ajouter :
   - `Sign In with LinkedIn using OpenID Connect` (login + profil)
   - `Share on LinkedIn` (publication)
4. **Auth** → **Authorized redirect URLs** : ajoute
   ```
   https://revpulse.exemple.com/api/linkedin/callback
   ```
5. Récupère **Client ID** + **Client Secret** (onglet Auth).
6. Dans `.env` :
   ```
   REVPULSE_LINKEDIN_CLIENT_ID=86xxxxxxxxx
   REVPULSE_LINKEDIN_CLIENT_SECRET=xxxxxxxxxx
   REVPULSE_LINKEDIN_REDIRECT_URI=https://revpulse.exemple.com/api/linkedin/callback
   REVPULSE_LINKEDIN_MOCK=false
   ```

> Docs LinkedIn : https://learn.microsoft.com/fr-fr/linkedin/shared/authentication/authorization-code-flow

## 5. Configurer Stripe (billing réel)

1. https://dashboard.stripe.com → **Test mode** (toggle en haut à droite).
2. Récupère les clés : **Developers → API keys** :
   - `pk_test_...` (Publishable key)
   - `sk_test_...` (Secret key)
3. **Webhook** : Developers → Webhooks → **Add endpoint** :
   - URL : `https://revpulse.exemple.com/api/billing/webhook`
   - Events : `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`
   - Récupère le **Signing secret** : `whsec_...`
4. Dans `.env` :
   ```
   REVPULSE_STRIPE_ENABLED=true
   REVPULSE_STRIPE_SECRET_KEY=sk_test_xxxxxxxxx
   REVPULSE_STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxx
   REVPULSE_STRIPE_PUBLISHABLE_KEY=pk_test_xxxxxxxxx
   ```

> Pour la prod réelle (paiements réels) : désactive Test mode + utilise les clés `sk_live_...`.

## 6. Configurer SMTP (e-mails d'invitation)

**Option test (Mailtrap — recommandé pour démarrer)** :
1. https://mailtrap.io → compte gratuit.
2. Inboxes → My Inbox → SMTP Settings → récupère host/port/user/password.
3. Dans `.env` :
   ```
   REVPULSE_SMTP_ENABLED=true
   REVPULSE_SMTP_HOST=smtp.mailtrap.io
   REVPULSE_SMTP_PORT=587
   REVPULSE_SMTP_USER=xxxxxxxxxxxxxx
   REVPULSE_SMTP_PASSWORD=xxxxxxxxxxxxxxxx
   REVPULSE_SMTP_FROM=no-reply@revpulse.exemple.com
   REVPULSE_SMTP_USE_TLS=true
   ```

**Option prod** : Brevo (https://www.brevo.com), Mailgun, Amazon SES — même structure.

## 7. Générer les secrets JWT + Fernet

```bash
python3 -c "import secrets; print('REVPULSE_JWT_SECRET='+secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print('REVPULSE_TOKEN_ENCRYPTION_KEY='+Fernet.generate_key().decode())"
python3 -c "import secrets; print('POSTGRES_PASSWORD='+secrets.token_urlsafe(32))"
```
Colle ces valeurs dans `.env`.

## 8. Déployer

```bash
bash deploy/deploy.sh
```

Le script :
1. Installe Docker si manquant.
2. Clone/pull le repo.
3. Vérifie que `.env` est complété (pas de placeholder).
4. Vérifie que le DNS pointe vers le VPS.
5. Build + démarre la stack (Caddy + frontend + backend + Postgres).
6. Applique les migrations Alembic.
7. Healthcheck (Caddy obtient le cert Let's Encrypt — ~30s).

## 9. Test réel — parcours complet

Une fois déployé, ouvre `https://revpulse.exemple.com` :

1. **Crée un compte réel** → onboarding (workspace + profil LinkedIn OAuth réel).
2. **Connecte LinkedIn** (vrai OAuth — pas mock) → import profil + abonnés.
3. **Configure le provider IA** : Paramètres → IA providers → ajoute clé OpenAI/GLM.
4. **Génère un post** : CV Studio → topic → GLM génère le contenu (réel).
5. **Invite un membre** : Team → invite par e-mail → SMTP envoie le vrai e-mail d'invitation.
6. **Billing** : Paramètres → upgrade plan → Stripe Checkout (test mode → carte `4242 4242 4242 4242`).
7. **Audit log** : audit.html → vérifie que toutes les actions sont tracées.

## 10. Opérations courantes

```bash
cd /opt/revpulse

# Logs en temps réel
docker compose -f deploy/docker-compose.prod.yml logs -f

# Redémarrer
docker compose -f deploy/docker-compose.prod.yml restart

# MAJ (git pull + rebuild)
git pull && docker compose -f deploy/docker-compose.prod.yml up -d --build

# Backup DB
docker compose -f deploy/docker-compose.prod.yml exec db pg_dump -U revpulse revpulse > backup_$(date +%F).sql

# Stop / start
docker compose -f deploy/docker-compose.prod.yml down
docker compose -f deploy/docker-compose.prod.yml up -d

# Observabilité (Prometheus + Grafana + Loki)
docker compose -f docker-compose.observability.yml up -d
```

## 11. Sécurité prod

- ✅ `REVPULSE_DEMO_ENABLED=false` (pas de login démo en prod).
- ✅ `REVPULSE_LINKEDIN_MOCK=false` (vrai OAuth).
- ✅ `REVPULSE_STRIPE_ENABLED=true` (fail-fast anti free-plan-bypass).
- ✅ HTTPS auto (Caddy + Let's Encrypt).
- ✅ Headers de sécurité (HSTS, CSP, X-Frame-Options).
- ✅ Rate limiting login (5/min anti brute-force).
- ✅ fail2ban + ufw sur le VPS.
- ✅ JWT secret + Fernet key ≥ 32 chars (fail-fast sinon).
- ⚠️ Sauvegarde régulière de la DB (pg_dump cron).
- ⚠️ Veille à renew le domaine + surveiller les alertes Let's Encrypt (ACME_EMAIL).

---

## Checklist finale

- [ ] VPS provisionné + SSH + ufw + fail2ban
- [ ] Domaine + A record → VPS IP (vérifié avec `dig`)
- [ ] App LinkedIn créée + redirect URL + Client ID/Secret dans `.env`
- [ ] Stripe test mode + clés + webhook dans `.env`
- [ ] SMTP (Mailtrap ou provider) dans `.env`
- [ ] JWT secret + Fernet key + Postgres password générés dans `.env`
- [ ] `DOMAIN` + `ACME_EMAIL` renseignés
- [ ] `bash deploy/deploy.sh` → stack up + healthcheck OK
- [ ] Compte créé + LinkedIn OAuth réel fonctionne
- [ ] Provider IA configuré + génération de post réelle
- [ ] Invitation membre → e-mail reçu
- [ ] Stripe Checkout → abonnement actif
- [ ] Audit log → toutes les actions tracées

Une fois cette checklist validée, l'app tourne en réel avec **toutes les fonctionnalités** (la seule qui était bloquée en local — LinkedIn OAuth — fonctionne maintenant grâce au domaine public + HTTPS).
