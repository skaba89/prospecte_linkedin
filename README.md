# RevPulse — Stack modulaire dockerisée

Architecture **multi-service** du LinkedIn Command Center : backend API FastAPI + frontend statique (nginx) + Postgres, orchestrés par Docker Compose. Fonctionne **bout en bout** (auth JWT → multi-tenant → RBAC → dashboard data), vérifié localement.

```
┌─────────────────────────────────────────────────────────────┐
│  Navigateur (http://localhost:8080)                          │
│   └─ frontend (nginx) : sert index.html (dashboard dual-mode)│
│      └─ proxy /api/* ─────────────────┐                      │
└───────────────────────────────────────┼──────────────────────┘
                                        ▼
┌─────────────────────────────────────────────────────────────┐
│  backend (FastAPI :8000)  app/                                │
│   routers: auth · tenants · members · approvals · dashboard ·│
│            billing                                            │
│   security: JWT (HS256) + bcrypt · deps RBAC (require_perm)  │
│   seed: idempotent (Alex Démo / RevPulse Demo / 34 modules)  │
└───────────────┬─────────────────────────────────────────────┘
                ▼  SQLAlchemy 2.x (JSONB sous Postgres)
┌─────────────────────────────────────────────────────────────┐
│  db (Postgres 16)  — tables multi-tenant (tenant_id)         │
│   users · tenants · profiles · members · approvals ·         │
│   dashboard_modules                                          │
└─────────────────────────────────────────────────────────────┘
```

## Structure

```
revpulse-stack/
├── docker-compose.yml          # orchestration 3 services
├── .env.example                # variables (JWT secret, BDD, CORS…)
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini             # config Alembic (URL surchargée par env.py)
│   ├── alembic/                # migrations versionnées (0001_initial : 7 tables)
│   │   ├── env.py              # importe Base.metadata + settings.database_url
│   │   ├── script.py.mako
│   │   └── versions/
│   └── app/
│       ├── main.py             # FastAPI : CORS, rate-limit, routers, seed@startup
│       ├── config.py           # pydantic-settings + fail-fast JWT (prod)
│       ├── database.py         # engine SQLAlchemy + Base + get_db + init_db (alembic)
│       ├── models.py           # User, Tenant, Profile, Member(+expires_at), Approval, …
│       ├── schemas.py          # Pydantic (validation + sérialisation + InviteResult)
│       ├── security.py         # bcrypt direct + JWT
│       ├── extensions.py       # Limiter slowapi (anti brute-force)
│       ├── mailer.py           # SMTP invitation e-mail (dry-run si désactivé)
│       ├── deps.py             # get_current_user, get_tenant, require_permission (RBAC)
│       ├── seed.py             # ensure_demo() idempotent + 34 modules
│       ├── seed_data.json      # données de dashboard (188 ko, générées par GLM)
│       └── routers/
│           ├── auth.py         # /api/auth : register, login, demo, me
│           ├── tenants.py      # /api/tenants : CRUD, rename, delete, switch, profile
│           ├── members.py      # /api/tenants/{tid}/members : invite, accept, role, remove
│           ├── approvals.py    # /api/tenants/{tid}/approvals : list, approve
│           ├── dashboard.py    # /api/tenants/{tid}/dashboard/modules
│           └── billing.py      # /api/tenants/{tid}/billing : plans, usage, change
└── frontend/
    ├── Dockerfile              # nginx:alpine
    ├── nginx.conf              # static + proxy /api → backend:8000
    └── public/index.html       # dashboard dual-mode (430 ko)
```

## Lancer en Docker

```bash
cd revpulse-stack
cp .env.example .env          # adapter REVPULSE_JWT_SECRET
docker compose up --build
```

- Frontend : <http://localhost:8080>
- API : <http://localhost:8000/api/health>
- Docs API (Swagger) : <http://localhost:8000/docs>

Au démarrage, le backend crée les tables (`init_db`) et sème le compte démo
(`ensure_demo`). Cliquez **« Essayer la démo »** → le frontend appelle
`POST /api/auth/demo`, récupère un JWT, charge les tenants et les 34 modules,
et rend le command center.

## Lancer en local (sans Docker)

```bash
cd backend
pip install -r requirements.txt
# SQLite (par défaut) — ou pointer vers un Postgres local via REVPULSE_DATABASE_URL
REVPULSE_FRONTEND_DIR=../frontend/public python -m uvicorn app.main:app --port 8000
```
Ouvrir <http://localhost:8000/> (le backend sert alors API + frontend sur le même port).

## Makefile

Toutes les opérations courantes sont exposées via `make` (lancé depuis la racine
de la stack) :

| Cible | Rôle |
|---|---|
| `make help` | Liste toutes les cibles |
| `make up` | Démarre la stack en arrière-plan (build + db + backend + frontend) |
| `make down` / `make logs` / `make ps` | Arrêt / logs / état |
| `make rebuild` | Rebuild sans cache + redémarre |
| `make health` | Vérifie `/api/health` |
| `make lint` / `make fmt` | Lint (ruff) / auto-fix du backend |
| `make test-api` | Test l'API contre une stack démarrée (`BACKEND_URL=http://localhost:8000`) |
| `make test-local` | Test l'API **sans docker** : démarre uvicorn sqlite, joue `tests/test_api.py`, arrête |
| `make test` | Lint + test (auto : test-local si docker absent, sinon invite à faire `up` + `test-api`) |
| `make dev` | Mode développement (hot-reload, voir ci-dessous) |
| `make db-shell` | Shell SQL dans Postgres |
| `make backend-shell` / `make backend-logs` | Shell / logs du conteneur backend |
| `make clean` | ⚠️ Arrête + supprime les volumes (efface la BDD) |

## Développement (hot-reload)

`docker-compose.dev.yml` est un **override** fusionné avec le compose de base :

- **backend** : `uvicorn --reload` + `./backend/app` monté → une édition Python
  est rechargée automatiquement.
- **frontend** : `./frontend/public` monté en lecture seule → une édition de
  `index.html` est servie immédiatement par nginx.
- **db** : héritée du compose de base (Postgres + volume persistant).

```bash
make dev            # docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# ou en arrière-plan :
make dev-up
```
Frontend : <http://localhost:8080> · API : <http://localhost:8000/docs>

## CI (GitHub Actions)

`.github/workflows/ci.yml` — 3 jobs sur push/PR (déclenchés sur
`backend/**`, `frontend/**`, `tests/**`, compose, Makefile) :

1. **lint** — `ruff check backend/app` (Python 3.12).
2. **test-api** — installe les deps, joue `tests/test_config.py` (fail-fast JWT,
   10 cas), valide les migrations Alembic (up → down → up sur SQLite), démarre
   uvicorn **avec SQLite** (sans docker, schéma créé par `alembic upgrade head`),
   joue `tests/test_api.py` (49 checks / 25 scénarios : auth/RBAC/tenants/members/
   approvals + content/reject/history + LinkedIn mock + invitation SMTP dry-run +
   rate-limit 429), arrête uvicorn.
3. **docker** — `docker compose config -q` (valide base **+** dev merge) puis
   `docker compose build`.

Le job `test-api` ne dépend pas de Docker : il prouve que l'API passe ses tests
en isolation (SQLite), exactement comme `make test-local` en local.

### CD (publish d'images)

`.github/workflows/publish.yml` — **Continuous Delivery** : à chaque tag `v*`
(ou via `workflow_dispatch` manuel), build des images Docker et push sur
**ghcr.io** (GitHub Container Registry). Aucun secret à créer : utilise le
`GITHUB_TOKEN` automatique (permission `packages: write`).

- `ghcr.io/<owner>/revpulse-api:<tag>` — API FastAPI (port 8000), context `./backend`
- `ghcr.io/<owner>/revpulse-frontend:<tag>` — frontend nginx (port 80), context `./frontend`

Tags publiés : `<version>` (ex. `v1.0.0`) + `latest`. Cache Buildx via `type=gha`.
Déclenchement : `git tag v1.0.0 && git push origin v1.0.0` ou onglet Actions →
« Publish (CD) » → Run workflow.

## P1 — Hardening (sécurité + production)

Quatre renforcements production, tous vérifiés en CI (`ruff` + `test_config` +
`test_api` 49/49 + migrations up/down/up).

**Alembic (migrations versionnées)** — `backend/alembic/` + `alembic.ini` +
`env.py` (importe `Base.metadata` + `settings.database_url`). La migration
initiale `0001_initial` crée les 7 tables (users, tenants, profiles,
members + `expires_at`, approvals, dashboard_modules, linkedin_connections).
`init_db()` applique `alembic upgrade head` au démarrage (source de vérité du
schéma) ; fallback `create_all` si Alembic absent. Production : `alembic
upgrade head` avant le boot. CI valide le round-trip up → down → up.

**JWT secret fail-fast** — `validate_jwt_secret()` (`config.py`) + `model_validator` :
en `REVPULSE_ENV=production`, un secret par défaut (`change-me-in-production`,
`revpulse-demo-secret-change-me`, …), vide ou `< 32` caractères fait **planter
le démarrage** avec un message clair et la commande pour générer un secret.
`development`/`test` tolèrent les secrets de démo (dev/CI non bloqués). Testé
par `tests/test_config.py` (10 cas). Dockerfile/compose imposent
`REVPULSE_ENV=production` → un boot sans vrai secret crash (voulu).

**Rate-limit login (anti brute-force)** — `slowapi` Limiter (`extensions.py`),
décorateurs `@limiter.limit` sur `/api/auth/login`, `/register`, `/demo`.
Défaut `5/minute` par IP, configurable (`REVPULSE_LOGIN_RATE_LIMIT`) et
désactivable (`REVPULSE_RATE_LIMIT_ENABLED`). Handler 429 intégré. Testé :
6ᵉ login erroné → 429.

**Invitation e-mail SMTP** — `mailer.py` (smtplib stdlib) envoie un vrai
e-mail d'invitation (lien + rôle + tenant + expiration 7 jours). Colonne
`members.expires_at` (migration). `smtp_enabled=false` → dry-run (log du lien,
dev/test/CI). Échec SMTP → `email_sent=false` (l'invitation reste créée, l'inviter
peut relancer). Config SMTP via `REVPULSE_SMTP_*`. `POST /members/invite`
renvoie `InviteResult` (`MemberOut` + `email_sent`). Lien d'expiration vérifié
dans `accept` (410 Gone si expirée).

## API

| Méthode | Route | Rôle | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | Inscription → JWT + user |
| POST | `/api/auth/login` | public | Login → JWT + user (répare le compte démo si besoin) |
| POST | `/api/auth/demo` | public | Accès démo 1 clic → JWT Alex Démo |
| GET | `/api/auth/me` | auth | Utilisateur courant |
| GET | `/api/tenants` | auth | Espaces de l'utilisateur (avec profil + nb membres + rôle) |
| POST | `/api/tenants` | auth | Créer un espace |
| GET/PATCH/DELETE | `/api/tenants/{tid}` | membre/Owner | Détail, renommage, suppression |
| POST | `/api/tenants/{tid}/profile` | Owner | Profil LinkedIn/native |
| GET | `/api/tenants/{tid}/members` | membre | Liste des membres |
| POST | `/api/tenants/{tid}/members/invite` | manage | Invitation (token, quota sièges) |
| POST | `/api/tenants/{tid}/members/{mid}/accept` | manage | Accepter une invitation |
| PATCH/DELETE | `/api/tenants/{tid}/members/{mid}` | manage | Rôle, retrait |
| GET | `/api/tenants/{tid}/approvals` | membre | File d'approbation (status='pending') |
| POST | `/api/tenants/{tid}/approvals/{aid}/approve` | approvals.manage | Approuver |
| POST | `/api/tenants/{tid}/approvals/{aid}/reject` | approvals.manage | Refuser (sort de la file) |
| GET | `/api/tenants/{tid}/approvals/history` | membre | Approuvés + refusés (hors file) |
| POST | `/api/tenants/{tid}/content` | content.publish | Composer → soumet en file (authoring) |
| GET | `/api/tenants/{tid}/linkedin/auth` | workspace.manage | URL d'autorisation OAuth (ou mock) |
| GET | `/api/tenants/{tid}/linkedin/status` | membre | État de la connexion LinkedIn |
| GET | `/api/linkedin/callback` | (public, state signé) | Callback OAuth (échange code → token) |
| POST | `/api/tenants/{tid}/linkedin/disconnect` | workspace.manage | Supprimer la connexion |
| POST | `/api/tenants/{tid}/linkedin/publish?aid=` | content.publish | Publier un contenu approuvé sur LinkedIn |
| GET | `/api/tenants/{tid}/dashboard/modules` | membre | 34 modules de données (JSON) |
| GET | `/api/tenants/{tid}/dashboard/modules/{key}` | membre | Un module |
| GET | `/api/tenants/{tid}/billing` | membre | Plan, sièges, usage |
| PATCH | `/api/tenants/{tid}/billing` | billing.manage | Changement de plan (quota vérifié) |

### RBAC
Rôles : `Owner · Admin · Editor · Contributor · Viewer`. Permissions par rôle
(`members.manage`, `billing.manage`, `workspace.manage`, `approvals.manage`,
`content.publish`) appliquées via `require_permission(...)` dans `deps.py`.
Un utilisateur non-membre d'un tenant reçoit **403** sur toutes ses routes
(isolation par espace).

## Frontend dual-mode

`index.html` est le **même** fichier que le dashboard autonome
(`linkedin_command_center.html`). Un adaptateur interne s'active selon le
protocole :

- `file://` (double-clic) → **mode standalone** : données inline + localStorage
  (comportement d'origine, inchangé).
- `http(s)://` (nginx / uvicorn) → **mode API** : auth, multi-tenant, membres,
  approbations, facturation, modules → appels `fetch` vers `/api/*` avec JWT.
  Les mutations persistent côté serveur ; le cache mémoire se rafraîchit après
  chaque action.

Aucune réécriture du rendu : l'adaptateur ne remplace que la **couche stockage**
(`loadDB`/`saveDB`/`loadSess`/…), le **boot**, l'**auth** et les **mutations**.
Les 20 vues consomment `D` et `loadDB()` de façon synchrone, inchangées.

## LinkedIn — connexion réelle (test avec ton compte)

Le router `linkedin.py` implémente l'OAuth 2.0 LinkedIn complet (auth → callback →
token → profil → publication). Deux modes :

- **Mock** (par défaut si `REVPULSE_LINKEDIN_CLIENT_ID` est vide) : simule la
  connexion et la publication — tout le flow UI est testable sans App LinkedIn.
  C'est ce que vérifie la CI (`test_api.py` sections 20-24).
- **Réel** : renseigne tes creds LinkedIn App dans `.env` → le bouton
  « Connecter LinkedIn » du **Studio** déclenche le vrai OAuth.

### Créer l'App LinkedIn (toi seul peux le faire)
1. Va sur <https://www.linkedin.com/developers/apps> → **Créer une application**.
2. Renseigne nom, logo, page LinkedIn rattachée. Coche **« Sign In with LinkedIn
   using OpenID Connect »** (fournit `openid profile`).
3. Onglet **Auth** : ajoute une **URL de redirection autorisée** =
   `http://localhost:8000/api/linkedin/callback` (ou ton domaine prod).
4. Récupère le **Client ID** et **Client Secret** → copie-les dans `.env` :
   ```
   REVPULSE_LINKEDIN_CLIENT_ID=86xxxxxxxxxx
   REVPULSE_LINKEDIN_CLIENT_SECRET=xxxxxxxxxxxx
   REVPULSE_LINKEDIN_REDIRECT_URI=http://localhost:8000/api/linkedin/callback
   REVPULSE_LINKEDIN_MOCK=false
   ```
5. Onglet **Products** : ajoute **« Share on LinkedIn »** (scope `w_member_social`).
   ⚠️ La **publication** réelle (`POST /linkedin/publish`) requiert l'accès
   **Marketing API** approuvé par LinkedIn (review). La **connexion + profil**
   (`openid profile`) fonctionnent immédiatement, sans review.
6. `make up` puis <http://localhost:8080> → « Essayer la démo » → bouton ✍️ Studio
   → « Connecter LinkedIn » → consentement LinkedIn → retour auto au dashboard.

### Studio (UI d'authoring — P0)
Bouton flottant **✍️** en bas à droite (mode servi uniquement) : composer un
contenu (post/DM/commentaire + corps) → soumet à la file → **Approuver** /
**Refuser** → **Publier sur LinkedIn** sur les contenus approuvés. Suit le tenant
actif automatiquement. Le module (`frontend/public/app-enhancements.js`) est
chargé en `defer` et n'altère pas le rendu existant.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `REVPULSE_ENV` | `development` | Environnement (`development`/`test`/`production`) — pilote le fail-fast JWT |
| `REVPULSE_DATABASE_URL` | `sqlite:///./revpulse.db` | SQLAlchemy URL (sqlite ou postgresql+psycopg2) |
| `REVPULSE_JWT_SECRET` | `change-me…` | Secret HS256 — **≥ 32 chars obligatoire en prod** (fail-fast sinon) |
| `REVPULSE_JWT_EXPIRY_HOURS` | `24` | Durée de validité du JWT |
| `REVPULSE_CORS_ORIGINS` | `*` | Origines CORS (séparées par `,`) |
| `REVPULSE_APP_BASE_URL` | `http://localhost:8000` | URL publique (liens d'invitation e-mail) |
| `REVPULSE_RATE_LIMIT_ENABLED` | `true` | Active le rate-limiting (slowapi) |
| `REVPULSE_LOGIN_RATE_LIMIT` | `5/minute` | Limite anti brute-force sur login/register/demo |
| `REVPULSE_SMTP_ENABLED` | `false` | Active l'envoi réel des invitations (sinon dry-run = log) |
| `REVPULSE_SMTP_HOST`/`_PORT`/`_USER`/`_PASSWORD`/`_FROM`/`_USE_TLS` | — | Config SMTP (port 587, TLS on par défaut) |
| `REVPULSE_DEMO_EMAIL` / `_PASSWORD` | `demo@revpulse.io` / `demo1234` | Compte démo (seed) |
| `REVPULSE_FRONTEND_DIR` | `./frontend_public` | Monte le frontend statique sur `/` (local) |

## Vérifications effectuées

- **Backend (26/26 checks)** : auth (demo/login/register/me), multi-tenant
  (list/create/rename/delete/switch), members (invite/accept/role/remove +
  quota sièges), approvals (list/approve décrément), dashboard (34 modules),
  billing (plans/usage/change + blocage downgrade), RBAC (isolation 403),
  wrong password 401 — voir `scripts/revpulse_api_test.py`.
- **Intégration navigateur (zéro erreur)** : login démo via API → token JWT →
  dashboard rendu avec données API (nav counts, 6 KPIs overview) → notif
  badge 7 → approve via panneau (mutation API, badge 7→6, file 6→5) →
  members modal (4 membres depuis l'API).
- **Docker** : `docker-compose.yml` validé par parsing YAML (3 services,
  dépendances, healthcheck Postgres, volume). nginx.conf conforme aux docs
  nginx (`proxy_pass` + `try_files`). *Docker n'étant pas installé dans le
  sandbox de développement, `docker compose up` n'a pas été exécuté ici — les
  images sont buildables et le compose est structurellement valide.*
- **Industrialisation (P1)** : `ruff check backend/app` → 0 erreur ;
  `tests/test_config.py` → 10/10 (fail-fast JWT) ; `alembic upgrade head` →
  7 tables (round-trip up/down/up ✓) ; `tests/test_api.py` → 49/49 checks
  (25 scénarios : auth/RBAC/tenants/members/approvals/content/LinkedIn + invitation
  SMTP dry-run + rate-limit 429 + fail-fast prod) ; `make test-local` passe ;
  `make help` rend les 22 cibles ; `docker-compose.dev.yml`
  merge valide (`--reload` + volumes `./backend/app` et `./frontend/public`,
  db héritée) ; `.github/workflows/ci.yml` valide (jobs lint/test-api/docker).

## Compte démo

- **Login** : `demo@revpulse.io` / `demo1234` — ou bouton **« Essayer la démo »**.
- Deux espaces seedés : **RevPulse Demo** (Pro, profil LinkedIn Camille Laurent,
  4 membres dont 1 invitation en attente, 6 approbations, 34 modules) et
  **Acme Growth** (Enterprise, à configurer via l'onboarding).
