# Runbook — Faire tourner le stack RevPulse en local (Docker)

Ce runbook explique comment démarrer, valider et administrer le stack RevPulse sur ta machine avec Docker.

## Prérequis

- **Docker Engine** 24+ + **docker compose v2** : https://docs.docker.com/engine/install/
  - Vérifier : `docker --version` + `docker compose version`
- **Ports libres** : 8080 (frontend nginx) + 8000 (API backend) + 5432 (Postgres, interne au compose).
- Aucune install Python/Node nécessaire — tout tourne dans les conteneurs.

## Démarrage rapide (1 commande)

```bash
./scripts/docker-up.sh
```

Le script :
1. Génère un `.env` avec des secrets aléatoires (JWT, Fernet, POSTGRES_PASSWORD) si absent.
2. Valide `docker-compose.yml`.
3. Build les images `backend` (FastAPI) + `frontend` (nginx-unprivileged).
4. Démarre `db` (Postgres 16) + `backend` + `frontend` en mode détaché.
5. Attend les healthchecks (~30-60s au 1er build).
6. Affiche les URLs + identifiants démo.

**URLs après démarrage** :
- 🌐 **Dashboard (frontend)** : http://localhost:8080
- 🔌 **API health** : http://localhost:8000/api/health
- 🔌 **API via nginx proxy** : http://localhost:8080/api/health
- 👤 **Compte démo** : `demo@revpulse.io` / `demo1234`

## Validation du stack

```bash
./scripts/docker-healthcheck.sh
```

Vérifie : conteneurs healthy + `/api/health` 200 + `db_ok:true` + frontend 200 + proxy nginx → backend + auth demo 200 + migrations alembic current=head.

Résultat attendu :
```
RESULT: 9 passed, 0 failed
```

## Commandes d'administration

| Action | Commande |
|---|---|
| Voir les logs (live) | `docker compose logs -f` |
| Logs backend seulement | `docker compose logs -f backend` |
| Statut des conteneurs | `docker compose ps` |
| Arrêter (garde le volume DB) | `./scripts/docker-up.sh --down` |
| Reset complet (perte DB) | `./scripts/docker-up.sh --reset` |
| Rebuild après modif code | `docker compose build && docker compose up -d` |
| Shell dans le backend | `docker compose exec backend bash` |
| PSQL direct | `docker compose exec db psql -U revpulse -d revpulse` |
| Lancer les migrations | `docker compose exec backend alembic upgrade head` |
| État migrations | `docker compose exec backend alembic current` |

## Dépannage

### Le backend ne démarre pas (crash au boot)

```bash
docker compose logs backend --tail 50
```

Causes fréquentes :
- **`REVPULSE_JWT_SECRET` absent ou < 32 chars** : fail-fast prod (le `.env` généré par `docker-up.sh` règle ça).
- **Migration alembic en échec** : `docker compose exec backend alembic upgrade head` pour voir l'erreur.
- **Postgres pas ready** : le compose a `depends_on: db: condition: service_healthy` — attendre ~10s.

### `db_ok: false` dans /api/health

Le backend ne reach pas Postgres :
```bash
docker compose exec backend python -c "import os; from sqlalchemy import create_engine,text; e=create_engine(os.environ['REVPULSE_DATABASE_URL']); print(e.connect().execute(text('select 1')).scalar())"
```
Si ça échoue : vérifier `POSTGRES_PASSWORD` identique dans `.env` + que `db` est healthy (`docker compose ps db`).

### Frontend 200 mais API 404 via nginx

Le resolver Docker (127.0.0.11) n'a pas résolu `backend` :
```bash
docker compose restart frontend
docker compose logs frontend --tail 20
```

### Port déjà utilisé (8080 ou 8000)

```bash
# Qui occupe le port ?
sudo lsof -i :8080
# Libérer ou changer le port dans docker-compose.yml (ports: "127.0.0.1:XXXX:8080")
```

### Reset complet (repartir de zéro)

```bash
./scripts/docker-up.sh --reset   # supprime conteneurs + volume pgdata
./scripts/docker-up.sh           # recrée tout
```

## Configuration avancée (.env)

Le `.env` (généré ou édité manuellement) pilote tous les services. Variables clés :

| Variable | Défaut | Rôle |
|---|---|---|
| `REVPULSE_ENV` | production | production/development/test (fail-fast JWT en prod) |
| `REVPULSE_JWT_SECRET` | généré (48 chars) | Signature JWT HS256 — OBLIGATOIRE ≥32 chars en prod |
| `REVPULSE_TOKEN_ENCRYPTION_KEY` | généré (Fernet) | Chiffrement tokens OAuth LinkedIn/Gmail/Outlook |
| `POSTGRES_PASSWORD` | généré | Mot de passe Postgres (fail-fast :? dans le compose) |
| `REVPULSE_SMTP_ENABLED` | false | true = envoi réel invitations e-mail (configurer host/user/pwd) |
| `REVPULSE_LINKEDIN_CLIENT_ID` | vide | LinkedIn OAuth réel (vide = mode mock pour dev) |
| `REVPULSE_LINKEDIN_MOCK` | true | Force mock même si creds présentes (dev/démo) |

### Activer LinkedIn réel

1. Créer une App sur https://www.linkedin.com/developers/apps
2. Ajouter `http://localhost:8080/api/linkedin/callback` dans les URLs de redirect autorisées.
3. Renseigner dans `.env` :
   ```
   REVPULSE_LINKEDIN_CLIENT_ID=<ton_client_id>
   REVPULSE_LINKEDIN_CLIENT_SECRET=<ton_client_secret>
   REVPULSE_LINKEDIN_MOCK=false
   ```
4. `./scripts/docker-up.sh --down && ./scripts/docker-up.sh --no-build`

### Activer l'envoi d'e-mails (SMTP)

```
REVPULSE_SMTP_ENABLED=true
REVPULSE_SMTP_HOST=smtp.gmail.com
REVPULSE_SMTP_PORT=587
REVPULSE_SMTP_USER=ton.adresse@gmail.com
REVPULSE_SMTP_PASSWORD=<mot de passe d'application>
REVPULSE_SMTP_FROM=ton.adresse@gmail.com
```

## Tests automatisés (CI locale)

Le repo inclut 7 suites de tests P63-P69 (audit P0 V1.0) + les tests fonctionnels P3+ :

```bash
# Lancer tous les tests (depuis la racine, venv backend requis)
python tests/test_p63_stripe_p0.py
python tests/test_p64_securite_p0.py
python tests/test_p65_gdpr_p0_effective.py
python tests/test_p66_auth_p0_forgot_reset_backup.py
python tests/test_p67_frontend_auth_api.py
python tests/test_p68_auth_p0_sso_email_verify.py
python tests/test_p69_gdpr_g4_purge.py

# Test statique du stack Docker (sans lancer Docker)
python tests/test_docker_stack_static.py
```

## Architecture du stack

```
┌─────────────────────────────────────────────────────────┐
│  Host (localhost)                                        │
│                                                          │
│  :8080 ──────────┐   :8000 ──────────┐                   │
│                  │                   │                   │
│  ┌───────────────▼─────────┐  ┌──────▼──────────────┐   │
│  │ frontend (nginx-unpriv) │  │ backend (FastAPI)   │   │
│  │  - sert index.html      │  │  - /api/* endpoints │   │
│  │  - proxy /api → backend │  │  - alembic migrations│  │
│  │  - headers sécurité     │  │  - JWT auth         │   │
│  └───────────┬─────────────┘  └──────────┬──────────┘   │
│              │                           │               │
│              └─────────── network ───────┤               │
│                                          │               │
│                              ┌───────────▼──────────┐    │
│                              │ db (Postgres 16)     │    │
│                              │  - volume pgdata     │    │
│                              └──────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

Le frontend nginx sert le `index.html` (dashboard dual-mode : adaptateur API actif en HTTP) + proxie `/api/*` vers le backend. Le backend FastAPI expose l'API + applique les migrations au startup (`init_db` → `alembic upgrade head`). La DB Postgres 16 persiste via le volume `pgdata`.

## État après audit P0 V1.0 (P63-P69)

Le stack est **opérationnel** : 17/17 blocages P0 résolus (Stripe durci + SSRF corrigés + GDPR effectif + Auth complète). Score maturité ~92/100. Voir le rapport d'audit pour le détail des 7 PRs mergées.
