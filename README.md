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
│   ├── alembic/                # migrations versionnées (0001_initial : 7 tables + 0002_audit_log)
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

## P2 — Sécurité applicative + durcissement Docker

Trois axes vérifiés en CI par `tests/test_frontend.py` (21 checks statiques) +
le job Docker (`compose config` + `build` + `nginx -t` dans l'image construite).

**Headers de sécurité nginx** — `frontend/nginx.conf` émet une CSP stricte
(`default-src 'none'`, `script-src 'self'`, `connect-src 'self'`,
`frame-ancestors 'none'`), `X-Frame-Options DENY`,
`X-Content-Type-Options nosniff`, `Referrer-Policy strict-origin-when-cross-origin`,
`Permissions-Policy` (désactive caméra/géo/micro/paiement), `Strict-Transport-Security`
(HSTS 1 an) et `server_tokens off`. Les routes internes `/docs` et `/openapi.json`
ne sont **pas** proxyées (OpenAPI masqué en production). `listen 8080` (port non
privilégié, compatible `nginx-unprivileged`).

**Frontend durci** — `esc()` (helper d'échappement HTML, ~309 occurrences) est
attribut-safe : il échappe `&`, `<`, `>`, `"` (`&quot;`) et `'` (`&#39;`), couvrant
les contextes texte et attributs (`data-cat="${esc(c)}"`). `apiFetch` intercepte
les `401` : purge le JWT `revpulse_jwt` du `localStorage` et redirige vers le login
(fini les appels en cascade avec un token mort). Aucun `eval()` ni `document.write()`
(vérifié statiquement sur `index.html` + `app-enhancements.js`).

**Conteneurs non-root + healthchecks** — `backend/Dockerfile` : `useradd -r -u 10001
app`, `USER app`, `HEALTHCHECK` sur `/api/health`. `frontend/Dockerfile` : image
`nginx-unprivileged`, `EXPOSE 8080`, `HEALTHCHECK` sur `/`. `docker-compose.yml` :
CORS strict (pas de `*`) par défaut, port `8080:8080`, `depends_on: backend
(condition: service_healthy)` — le frontend ne démarre qu'une fois l'API saine.

## P3 — Observabilité + scalabilité

Production-readiness : pagination, logging structuré, healthcheck approfondi.
Vérifié par `tests/test_p3.py` (22 checks) ajouté au job CI `test-api`.

**Pagination** — `backend/app/pagination.py` expose une dépendance `Page`
(`?skip=0&limit=100`, `limit` plafonné à 200, validation `ge`/`le` → 422 hors
bornes). Appliquée aux 4 endpoints liste : `/api/tenants`,
`/api/tenants/{tid}/members`, `/api/tenants/{tid}/approvals`,
`/api/tenants/{tid}/approvals/history`. Le total est exposé via l'en-tête
`X-Total-Count` (déclaré au navigateur par `expose_headers` CORS). Rétro-compatible :
sans paramètres, retourne jusqu'à 100 lignes (couvre le volume démo sans tronquer
les listes existantes ; tous les counts de `test_api.py` restent verts).

**Logging structuré + request-ID** — `backend/app/logging_config.py` :
`configure_logging(level, fmt)` branche un formateur JSON (prod) ou texte lisible
(dev/test), émettant `ts/level/logger/msg` + `request_id/user_id/tenant_id`
(contextvars) + les champs extra (`method/path/status/duration_ms`). Le middleware
`RequestLoggingMiddleware` (outermost) génère/propage un `X-Request-ID` (header
entrant sinon uuid4), logge chaque requête et renvoie l'ID en réponse — correlable
dans tout agrégateur de logs. `user_id_var`/`tenant_id_var` sont positionnés par
`get_current_user`/`get_tenant` pour que les logs métier incluent le contexte.
Config : `REVPULSE_LOG_LEVEL` (défaut `INFO`), `REVPULSE_LOG_FORMAT` (`json`|`text`).

**Healthcheck approfondi** — `/api/health` exécute un vrai `SELECT 1` sur la DB
(via la dépendance `get_db`) et renvoie `{"status":"ok"|"degraded", "db_ok": bool,
"detail": str, "db": <nom>, "env": <env>}`. Code `200` si DB up, `503` si down —
exploitable par les healthchecks Docker et les sondes Liveness/Readiness d'un
orchestrateur (Kubernetes).

## P4 — Audit log multi-tenant

Trace queryable et immuable des actions mutantes d'un tenant (qui a fait quoi,
quand) — la suite naturelle du logging P3 : là où P3 capture les requêtes dans
les logs, P4 persiste une trace structurée, tenant-scopée, consultable via API.
Vérifié par `tests/test_audit.py` (25 checks) ajouté au job CI `test-api`.

**Modèle `AuditLog`** (`app/models.py`) + migration `0002_audit_log` (`alembic/`) :
`id, tenant_id, actor_user_id, actor_name, action, target_type, target_id, detail
(JSON), request_id, created_at`. `tenant_id` est un **String indexé sans FK** — la
trace doit survivre à la suppression du tenant (une audit log outlive l'entité
auditée ; sinon la FK casserait au `DELETE`). `request_id` corrélationne avec les
logs structurés P3.

**Helper `log_audit()`** (`app/audit.py`) : appelé par les routers **avant le
`db.commit()`**, dans la même session/transaction → atomique avec la mutation (si
la mutation échoue avant commit, aucune ligne audit n'est écrite — cohérent :
rien ne s'est passé). Capturé sur les 12 actions mutantes : `tenant.create/rename/
delete/profile_update`, `member.invite/accept/role_change/remove`, `approval.approve/
reject`, `content.submit`, `linkedin.connect/disconnect/publish`. L'acteur est
l'utilisateur courant (via le membership) ; les actions système (callback OAuth
LinkedIn) ont `actor_user_id=null` + `actor_name="LinkedIn OAuth"`.

**Lecture** : `GET /api/tenants/{tid}/audit` — paginée (P3 : `?skip`/`?limit` +
`X-Total-Count`), plus récent d'abord. Accès **Owner uniquement**
(`workspace.manage`) : seul le propriétaire voit l'historique complet. La suppression
d'un tenant logge l'événement `tenant.delete` puis **garde** l'historique (non
reachable via API — `get_tenant` 404 — mais persistant pour forensics).

## P5 — Observabilité metrics + UI audit

Clôture la boucle observabilité : logs structurés (P3) + trace audit queryable (P4)
+ **metrics Prometheus** (P5) → triade complète logs/audit/metrics. Et rend l'audit P4
**utilisable in-app** via un panneau Owner dans le Studio. Vérifié par
`tests/test_metrics.py` (12 checks) + 2 checks statiques ajoutés à `test_frontend.py`.

**Prometheus metrics** (`app/metrics.py`) : middleware Starlette léger
(`prometheus-client`) exposant `GET /metrics` (texte, public — scraped par Prometheus).
  - `revpulse_http_requests_total{method,route,status}` (Counter)
  - `revpulse_http_request_duration_seconds{method,route}` (Histogram)
Le label `route` utilise le **route-template** (`/api/tenants/{tid}/audit`) et non le
path brut (`/api/tenants/tnt-demo/audit`) → **cardinalité contrôlée** (sinon chaque
tid/aid/user créerait une série temporelle — famine mémoire Prometheus). `/metrics`
est exclu du compteur (pas de boucle de scrapping). Le middleware est inséré entre
SlowAPI et RequestLogging (RequestLogging reste outermost → logge aussi `/metrics`).

**UI audit panel** (`frontend/public/app-enhancements.js`) : section « Historique des
actions » ajoutée au drawer Studio, **gated par rôle Owner** (la section est masquée
si `ts[0].role !== "Owner"` — `role` lu via `/api/tenants`). Fetch
`GET /api/tenants/{tid}/audit?limit=20` + rendu d'un tableau (action, acteur, date,
cible). Rend la trace P4 consultable directement dans l'app sans passer par l'API.

## P6 — E2E Playwright (vérification bout-en-bout)

Le SPA (index.html + app-enhancements.js) n'était jusqu'ici testé que statiquement
(headers/markup via `test_frontend`). P6 ajoute un **vrai navigateur** (chromium via
Playwright) qui exerce le cheminement utilisateur réel contre le backend. Vérifié par
`tests/test_e2e.py` (14 checks).

Scénario E2E : token démo injecté (localStorage `revpulse_jwt`) → FAB Studio visible →
drawer ouvert → `refresh()` tourne (`#rp-tid` peuplé via `/api/tenants`) → section audit
visible (Owner) + fetch `/audit` → compose un contenu (POST `/content`, banner OK) →
file d'approbation affiche le contenu (GET `/approvals`) → audit panel montre
`content.submit` → approve (POST `/approve`, banner OK) → audit panel montre
`approval.approve` → `/metrics` scrapé par le navigateur (counter + histogram).

Couvre l'intégration SPA↔backend complète : auth JWT, middleware (CORS / SlowAPI /
metrics / logging), routers (tenants / content / approvals / audit), StaticFiles (SPA
servie), et la logique autonome `app-enhancements.js` (Studio + panneau audit P5). CI :
étape `playwright install --with-deps chromium` + `python tests/test_e2e.py` (après
test_metrics, serveur encore up).

## P7 — Grafana dashboard + alert rules (observabilité visualisable)

Clôture la triade observabilité : logs structurés (P3) + audit queryable (P4) + metrics
Prometheus (P5) + E2E (P6) → **dashboard Grafana + alertes** (P7). Les metrics P5
deviennent visualisables (QPS, latence p95, taux 5xx) + actionnables (alertes
automatiques). Vérifié par `tests/test_observability.py` (38 checks statiques).

Configs sous `observability/` :
  - `prometheus.yml` : scrape le backend sur `/metrics` toutes les 15s (job
    `revpulse-backend`, target `backend:8000`), référence `alerts.yml`.
  - `alerts.yml` : 3 règles — `RevPulseBackendDown` (critical, `up == 0` pendant 1m),
    `RevPulseHighErrorRate` (warning, taux 5xx > 5% pendant 2m),
    `RevPulseHighLatencyP95` (warning, p95 > 500ms pendant 5m).
  - `grafana/provisioning/` : datasource Prometheus (uid `prometheus`, auto-chargée) +
    dashboard provider (charge les JSON du dossier monté).
  - `grafana/dashboards/revpulse-overview.json` : 6 panels (généré par
    `scripts/gen_dashboard.py`) — QPS total, taux d'erreur 5xx, répartition par status,
    QPS par route, latence p95 par route (`histogram_quantile`), top 5 routes par trafic.

`docker-compose.observability.yml` (overlay) : `docker compose -f docker-compose.yml -f
docker-compose.observability.yml up` → Prometheus (http://localhost:9090) + Grafana
(http://localhost:3000, admin/admin). Grafana provisionne automatiquement le datasource
+ le dashboard (aucune config manuelle).

## P8 — Sauvegardes Postgres + test de restauration (résilience)

Achève la capacité de récupération entamée en P7 (monitoring) : sauvegarde automatisée de
la base Postgres + restauration idempotente + test E2E bout-en-bout (backup → perte →
restore → verify). Vérifié par `tests/test_backups.py` (40 checks statiques).

Scripts sous `scripts/` :
  - `backup_db.sh` : `pg_dump --clean --if-exists` (dump idempotent) → gzip →
    `backups/revpulse_YYYYMMDD_HHMMSS.sql.gz`, rotation keep-N (`RETENTION_KEEP=10`).
    Fonctionne via `docker compose exec db pg_dump` (défaut) ou `pg_dump` local.
  - `restore_db.sh` : `gunzip` + `psql --ON_ERROR_STOP=1`, confirmation interactive
    requise sauf `--yes` (évite d'écraser la prod par accident).
  - `test_backup_restore.sh` : round-trip E2E sur un postgres éphémère (docker) —
    `alembic upgrade head` → seed (1 user + 1 tenant) → backup → `DROP SCHEMA public`
    (simule une perte) → restore → verify counts (tenants=1, users=1). Lancer via
    `make backup-test` (nécessite docker, non exécuté en CI).

Makefile : `make backup` / `make restore FILE=backups/<dump>.sql.gz` / `make backup-test`.
`backups/` est versionné (`.gitkeep`) mais les dumps `*.sql.gz` sont gitignorés.

Cron (prod) : `0 3 * * * cd /opt/revpulse && make backup >> backups/cron.log 2>&1`.

## P9 — Sécurité AppSec (durcissement)

Durcissement complémentaire : scanner de secrets en code committé, audit advisory des
dépendances, hachage bcrypt vérifié, config env-driven (fail-fast JWT secret en prod).
Vérifié par `tests/test_security.py` (29 checks statiques).

Scripts sous `scripts/` :
  - `scan_secrets.sh` : grep sur 7 patterns de PAT/clés réelles (GitHub `ghp_`/
    `github_pat_`, AWS `AKIA`, private keys, Stripe `sk_live_`, Slack `xox`, Google
    `AIza`) avec allowlist (.env.example, docker-compose, tests/, README, backups).
    Exit 1 si un secret est trouvé ; exécuté par `tests/test_security.py` (assert exit 0).
  - `audit_deps.sh` : `pip-audit` (backend/requirements.txt) + `npm audit` (frontend,
    skip gracieux si pas de package-lock.json). Advisory par défaut (exit 0) ;
    `--strict` pour CI gating.

Vérifié également : `backend/app/security.py` utilise bcrypt 4.2.0 directement
(`hashpw`/`checkpw` + `gensalt`, tronque à 72 bytes) ; `config.py` est env-driven via
pydantic `BaseSettings` (`env_prefix="REVPULSE_"`) avec fail-fast `validate_jwt_secret`
(≥ 32 chars + rejet des valeurs par défaut connues en production).

Makefile : `make audit-deps` (advisory) / `make audit-deps -- --strict` (gating).
nginx a déjà les 6 headers de sécurité (X-Frame, X-Content-Type, Referrer-Policy,
Permissions-Policy, CSP, HSTS) — couverts par `tests/test_frontend.py` (P6).

## Modules fonctionnels (P10-P82)

Le README documente ci-dessus le socle P1-P9 (sécurité, observabilité, audit,
backups, AppSec). Cette section documente les modules livrés ensuite (P10-P82),
regroupés par domaine fonctionnel. L'API expose **243 endpoints** répartis sur
**25 routers**, servie par FastAPI et entièrement browseable sur **`/docs`**
(OpenAPI/Swagger interactif). **11 pages frontend** + **~70 suites de tests**.

### CRM & Séquences (P31, P80)

| Module | Rôle | Tests |
|---|---|---|
| **CRM** (`/tenants/{id}/crm/*`, 39 endpoints) | Contacts, companies, deals, activities, pipeline kanban, segments dynamiques, scoring IA, export CSV **+ Excel (.xlsx)** (P85, 13 colonnes, en-tête stylé + freeze pane + autofilter) | `test_crm.py`, `test_scoring.py`, `test_segments_dyn.py`, `test_p85_crm_xlsx_export.py` |
| **Séquences** (`/tenants/{id}/crm/sequences/*`, 10 endpoints) | Drip campaigns multi-étapes (template + délai jours), enrollment, pause/active, stats par séquence | `test_sequences.py` |
| **Tags** (`/tenants/{id}/tags/*`) | Tagging hiérarchique coloré sur contacts et deals | `test_tags.py` |
| **Tasks** (`/tenants/{id}/tasks/*`) | Tâches assignées, due dates, priorités, follow-ups | `test_tasks.py` |
| **P80 — CRM responsive** | Tables wrappées `.rp-table-wrap` (scroll horizontal mobile 375px), vue mobile adaptée | `test_p31_business_e2e.py` |

Le CRM est le module cœur métier : il gère le pipeline commercial complet
(contacts → deals → activities) avec scoring IA et segments dynamiques. Les
séquences automatisent le drip outreach multi-étapes, et le module P80 a rendu
ces vues entièrement responsive (scroll horizontal des tables au lieu d'overflow).

### Analytics & Intelligence commerciale (P27-P29, P43)

| Module | Rôle | Tests |
|---|---|---|
| **Prospecting** (`/tenants/{id}/prospecting/*`, 9 endpoints) | Listes de prospects, enrichment, scoring automatique | `test_p27_prospecting.py` |
| **Sales Intelligence** (`/tenants/{id}/sales-intel/*`) | Signaux d'achat, firmographics, triggers de changement | `test_p28_sales_intel.py` |
| **Enrichment v2** (P29) | Connecteurs enrichment multi-sources (email, téléphone, firme) | `test_p29_v2_enrichment.py` |
| **Analytics v2** (`/tenants/{id}/analytics/*`, 9 endpoints) | 5 KPIs, tables pipeline/emails/sources/cohorts, export CSV **+ Excel (.xlsx)** contacts (P84, en-tête stylé + freeze pane + autofilter), funnel de conversion | `test_analytics.py`, `test_p43_analytics.py`, `test_p84_xlsx_export.py` |

L'analytics agrège les données CRM en tableaux de bord opérationnels : KPIs de
conversion, cohortes rétention, sources d'acquisition, et funnel détaillé.
L'export existe en deux formats — CSV (léger, machine) et Excel .xlsx (P84,
formaté : en-tête gras sur fond bleu, freeze pane A2, autofilter, largeurs
auto) — tous deux générés depuis les mêmes helpers `_EXPORT_COLS` +
`_contact_row` (source unique de vérité, cohérence garantie). Le .xlsx est
ouvrable directement dans Excel/LibreOffice/Google Sheets pour analyse
commerciale ou synchronisation vers un CRM tierce.

### IA & Agents (P24-P25, P41-P42)

| Module | Rôle | Tests |
|---|---|---|
| **AI** (`/ai/*`, 9 endpoints) | Génération de contenu, scoring IA, résumés (LLM intégré) | `test_p24b_ai.py` |
| **Agents** (`/agents/*`) | Agents autonomes multi-étapes (workflow IA orchestré) | `test_p25_agents.py` |
| **Coach** (P41) | Coaching commercial contextuel (suggestions de next-best-action) | `test_p41_coach.py` |
| **Agent runtime** (P42) | Orchestration et exécution d'agents avec état | `test_p42_agent.py` |

L'IA est intégrée à plusieurs niveaux : scoring automatique des prospects,
génération de contenu LinkedIn, résumés de deals, et coaching contextuel. Les
agents autonomes orchestrent des workflows multi-étapes (ex : enrichir un
prospect → scorer → rédiger un message → ajouter à une séquence).

### Contenu & Content Studio (P26)

| Module | Rôle | Tests |
|---|---|---|
| **Content** (`/content/*`) | Posts LinkedIn, templates réutilisables, calendrier éditorial | `test_p26_content.py` |
| **Content Studio** (`/content-studio/*`, 17 endpoints) | Authoring UI riche : éditeur, preview, gestion d'assets, programmation | — |

Le Content Studio est l'outil d'authoring pour la création de contenu LinkedIn :
éditeur riche, preview temps réel, bibliothèque d'assets, et programmation de
publication. Il s'appuie sur l'AI pour la génération et la variante A/B.

### Career & Recrutement (P34-P38)

Le module career est le plus volumineux (**54 endpoints**) : il transforme
RevPulse en plateforme de recrutement outbound pour candidats, en complément
du module CRM (qui reste orienté sales).

| Module | Rôle | Tests |
|---|---|---|
| **Career** (`/career/*`, 54 endpoints) | CV, profils candidats, matching offre↔candidat, candidatures, tracking | `test_p34_career.py` |
| **Job search** (P35) | Recherche multi-connecteurs (LinkedIn, Indeed, etc.) | `test_p35_job_search.py`, `test_p35_v2_connecteurs.py` |
| **Matching** (P36) | Score de compatibilité offre↔candidat (algorithme pondéré) | `test_p36_matching.py` |
| **CV adaptatif** (P37) | Génération de CV adapté à chaque offre (keywords matching) | `test_p37_cv_adaptatif.py` |
| **Candidatures** (P38) | Tracking candidatures + envoi email via OAuth (Gmail/Outlook) + attachments | `test_p38_candidatures.py`, `test_p38_v2_email_oauth.py`, `test_p38_v3_outlook_attachments.py` |

L'intégration email OAuth (P38v2) permet d'envoyer les candidatures
directement depuis la boîte Gmail/Outlook du candidat, sans stockage de
mot de passe — flux OAuth2 standard. Les attachments Outlook (P38v3)
gèrent les pièces jointes (CV, lettre de motivation).

### Billing & Stripe (P30, P44, P63)

| Module | Rôle | Tests |
|---|---|---|
| **Billing** (`/tenants/{id}/billing/*`) | Plans (Free/Pro/Business/Enterprise), usage tracking, change currency (EUR/USD), checkout, blocage downgrade | `test_p30_billing.py` |
| **P44 — Stripe réel** | Intégration Stripe Checkout (clés serveur), webhooks Stripe (payment succeeded/failed) | `test_p44_stripe_real.py` |
| **P63 — Stripe P0 hardening** | Vérification signature webhook Stripe, idempotency des events, secret management (non commité) | `test_p63_stripe_p0_hardening.py` |

Le billing gère 4 plans (Free/Pro/Business/Enterprise) avec change currency
live (EUR/USD), checkout via Stripe (mode mock si clé serveur absente — utile
pour la démo), et blocage automatique des features au downgrade. La page
`billing.html` permet de tester le flow complet en démo.

### SSO & Auth hardening (P45-P47, P62, P66-P68)

| Module | Rôle | Tests |
|---|---|---|
| **SSO** (`/sso/*`) | SAML/OIDC enterprise, i18n FR/EN, admin CRUD providers, admin UI de gestion | `test_p45_sso_i18n.py`, `test_p46_admin_sso_crud.py`, `test_p47_admin_sso_ui.py` |
| **P62 — 2FA TOTP** | Double authentification TOTP (RFC 6238), backup codes, recovery | `test_p62_2fa_totp.py` |
| **P66 — Forgot/Reset/Backup** | Flux mot de passe oublié, reset par token, codes de secours | `test_p66_auth_p0_forgot_reset_backup.py` |
| **P67 — Frontend auth API** | Frontend rebindé sur l'API auth réelle (login/register/me/logout) | `test_p67_frontend_auth_api.py` |
| **P68 — SSO email verify** | Vérification email obligatoire + binding SSO | `test_p68_auth_p0_sso_email_verify.py` |

Le SSO enterprise supporte SAML et OIDC, avec une admin UI pour configurer
les providers (client ID, secret, metadata URL). La 2FA TOTP est disponible
pour tous les utilisateurs (RFC 6238, compatible Google Authenticator/Authy).

### Audit log & Compliance (P48, P52-P57)

| Module | Rôle | Tests |
|---|---|---|
| **Audit log** (`/tenants/{id}/audit/*`, 9 endpoints) | Entries horodatées, filters (action/actor/since), pagination 25/page, export CSV, export PDF **signé HMAC-SHA256** (non-répudiation, compliance P53), retention purge automatique | `test_audit.py`, `test_p48_audit_log.py`, `test_p53_audit_compliance.py` |
| **P52/P54 — Audit UI** | Page `audit.html` : table, filters, pagination, exports CSV/PDF | `test_p52_audit_log_ui.py`, `test_p54_audit_ui_p53.py` |
| **P55 — Webhook alerts** | Évaluation d'alertes (seuils) + POST vers webhook configuré | `test_p55_audit_webhook_alerts.py` |
| **P56 — Webhook config UI** | UI de config webhook (URL, secret Fernet, events filtrés) | `test_p56_webhook_config_ui.py` |
| **P57 — Webhook Fernet + Helm cronjob** | Chiffrement Fernet du secret webhook + cronjob Helm pour retry queue | `test_p57_webhook_fernet_helm_cronjob.py`, `test_webhook_retry_cron.py` |

L'audit log est le pilier compliance : chaque action sensible est horodatée,
filtrable, exportable. L'export PDF est **signé HMAC-SHA256** (non-répudiation
— prouve que le document n'a pas été altéré depuis sa génération). La retention
purge automatiquement les entries > `retention_days` (configurable), et les
alertes webhook notifient en temps réel sur événements critiques.

### GDPR (P60, P61, P65, P69)

| Module | Rôle | Tests |
|---|---|---|
| **GDPR export/erase** (`/tenants/{id}/gdpr/*`) | Export complet données personnelles (JSON), effacement droit à l'oubli | `test_p60_gdpr_export_erase.py` |
| **P65 — GDPR P0 effective** | Vérifie que l'effacement est réel (cascade, hard delete — pas de soft delete résiduel) | `test_p65_gdpr_p0_effective.py` |
| **P61 — GDPR UI consent** | Bannière de consentement + page de préférences | `test_p61_gdpr_ui_consent.py` |
| **P69 — GDPR G4 purge** | Purge automatisée des données > retention (scheduler) | `test_p69_gdpr_g4_purge.py` |

Le module GDPR couvre les 4 garanties : export (portabilité), erase (droit à
l'oubli — vérifié réellement effectif en P65, pas un soft delete), consent
(bannière + préférences), et purge automatisée (P69). Le consentement est
tracker par utilisateur avec timestamp.

### Sécurité P0 (P10, P24a, P32, P64)

| Module | Rôle | Tests |
|---|---|---|
| **P10 — Security hardening** | Durcissement post-P9 (JWT strict, isolation tenant renforcée) | `test_p10_security.py` |
| **P24a — Security** | Hardening pré-intégration IA | `test_p24a_security.py` |
| **P32 — Security** | Vérifs cross-modules (isolation, IDOR, escalation) | `test_p32_security.py` |
| **P64 — SSRF/CORS P0** | Protection SSRF (webhooks outbound, connecteurs), CORS strict (origines allowlist) | `test_p64_security_p0_ssrf_cors.py` |

La sécurité P0 couvre les vecteurs les plus critiques : SSRF (un attaquant ne
peut pas forcer le serveur à appeler des IPs internes via les webhooks/imports),
CORS strict (pas de `*` en prod), isolation tenant absolue (pas de fuite
cross-tenant via IDOR), et JWT strict (fail-fast si secret < 32 chars).

### Notifications (P40)

| Module | Rôle | Tests |
|---|---|---|
| **Notifications** (`/tenants/{id}/notifications/*`) | In-app notifications, badge count, marquer lu, types (approval, mention, alert) | `test_notifications.py`, `test_p40_notifications.py` |

### Inbound webhooks

| Module | Rôle | Tests |
|---|---|---|
| **Inbound webhooks** (`/inbound/*`) | Réception webhooks externes (Stripe, LinkedIn callbacks) avec vérification de signature | `test_inbound_webhooks.py` |

### Déploiement VPS (P79)

Kit de déploiement production sur VPS Docker :

- `deploy/deploy.sh` — script idempotent (pull image, migrate, restart, healthcheck post-deploy, rollback si échec)
- `deploy/docker-compose.prod.yml` — stack production (backend + Postgres + nginx + job backups)
- `deploy/.env.example` — template config production (secrets à remplir)
- `deploy/README.md` — guide pas-à-pas (pré-requis VPS, DNS, TLS, premier déploiement)
- Healthcheck post-deploy automatique + rollback sur échec

Le script `deploy.sh` est idempotent : il peut être relancé sans risque
(re-détection de l'état, skip des migrations déjà appliquées, restart
uniquement si l'image a changé). Le rollback restaure l'image précédente
si le healthcheck échoue après déploiement.

### Tests E2E & Responsive (P31, P80-P82)

| Suite | Coverage | Assertions |
|---|---|---|
| `test_p31_business_e2e.py` | `index.html` (démo + dashboard SPA) + `crm.html` (API réelle) | 14 ✅ |
| `test_p82_e2e_extended.py` | `sequences` (CRUD create) + `analytics` (KPIs/tables) + `billing` (currency USD/checkout) + `audit` (filters/pagination) | 22 ✅ |
| `test_p31_frontend.py` | Structure HTML + 6 headers sécurité + assets + liens | 109 ✅ |
| **P81 — Audit responsive** | 24 pages scannées à 375px (0 overflow horizontal) | 24/24 ✅ |

Les tests E2E valident non seulement le load statique mais aussi les
**interactions CRUD** (création de séquence, changement de devise, application
de filtres audit) — ce qui a permis de détecter un bug responsive réel (P82 :
table steps `sequences.html` non wrappée, invisible au load statique car
`#seq-list` était vide ; révélée uniquement après création d'une séquence).
L'audit responsive P81 couvre les 24 pages à 375px (viewport mobile portrait).

## API

| Méthode | Route | Rôle | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | Inscription → JWT + user |
| POST | `/api/auth/login` | public | Login → JWT + user (répare le compte démo si besoin) |
| POST | `/api/auth/demo` | public | Accès démo 1 clic → JWT Alex Démo |
| GET | `/api/auth/me` | auth | Utilisateur courant |
| GET | `/api/tenants` | auth | Espaces de l'utilisateur (avec profil + nb membres + rôle) — **paginé** (`?skip`/`?limit`, `X-Total-Count`) |
| POST | `/api/tenants` | auth | Créer un espace |
| GET/PATCH/DELETE | `/api/tenants/{tid}` | membre/Owner | Détail, renommage, suppression |
| POST | `/api/tenants/{tid}/profile` | Owner | Profil LinkedIn/native |
| GET | `/api/tenants/{tid}/members` | membre | Liste des membres — **paginé** |
| POST | `/api/tenants/{tid}/members/invite` | manage | Invitation (token, quota sièges) |
| POST | `/api/tenants/{tid}/members/{mid}/accept` | manage | Accepter une invitation |
| PATCH/DELETE | `/api/tenants/{tid}/members/{mid}` | manage | Rôle, retrait |
| GET | `/api/tenants/{tid}/approvals` | membre | File d'approbation (status='pending') — **paginé** |
| POST | `/api/tenants/{tid}/approvals/{aid}/approve` | approvals.manage | Approuver |
| POST | `/api/tenants/{tid}/approvals/{aid}/reject` | approvals.manage | Refuser (sort de la file) |
| GET | `/api/tenants/{tid}/approvals/history` | membre | Approuvés + refusés (hors file) — **paginé** |
| GET | `/api/tenants/{tid}/audit` | Owner | Trace immuable des actions mutantes (paginée, P4) |
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
| GET | `/metrics` | public | Exposition Prometheus (counter + histogram, P5) |

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

- **Backend (49/49 checks, `tests/test_api.py`)** : auth (demo/login/register/me),
  multi-tenant (list/create/rename/delete/switch), members (invite/accept/role/remove +
  quota sièges), approvals (list/approve/reject/history), dashboard (34 modules),
  billing (plans/usage/change + blocage downgrade), content/authoring, LinkedIn mock
  (auth/connect/status/publish), RBAC (isolation 403), wrong password 401, rate-limit 429.
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
  8 tables (0001 + 0002_audit_log, round-trip up/down/up ✓) ; `tests/test_api.py` → 49/49 checks
  (25 scénarios : auth/RBAC/tenants/members/approvals/content/LinkedIn + invitation
  SMTP dry-run + rate-limit 429 + fail-fast prod) ; `make test-local` passe ;
  `make help` rend les 22 cibles ; `docker-compose.dev.yml`
  merge valide (`--reload` + volumes `./backend/app` et `./frontend/public`,
  db héritée) ; `.github/workflows/ci.yml` valide (jobs lint/frontend/test-api/docker).
- **P2 (sécurité)** : `tests/test_frontend.py` → 21/21 (headers nginx, CSP, non-root,
  esc attribut-safe, handler 401, no eval) ; job Docker → `nginx -t` dans l'image
  frontend construite.
- **P3 (observabilité)** : `tests/test_p3.py` → 22/22 (pagination skip/limit +
  `X-Total-Count` + cap 422, `X-Request-ID` echoé, health `db_ok` via `SELECT 1`).
- **P4 (audit)** : `tests/test_audit.py` → 25/25 (écritures audit sur approve/reject/
  invite avec `target_id`/`actor_name`/`request_id`, lecture Owner 200, isolation
  non-membre 403 + 401, pagination + cap 422) ; `alembic` `0002_audit_log` round-trip
  up/down/up ✓.
- **P5 (observabilité + UI)** : `tests/test_metrics.py` → 12/12 (`/metrics` format
  Prometheus, counter incrémenté, labels route-template `{tid}` sans explosion de
  cardinalité, status 200/401, `/metrics` exclu du compteur) ; `tests/test_frontend.py`
  → 23/23 (+2 : panneau audit `rp-audit` + gate rôle Owner) ; panneau « Historique des
  actions » dans le Studio (Owner only, fetch `/audit?limit=20`) ; `prometheus-client`
  ajouté à `requirements.txt` ; `ruff check backend/app` → 0 erreur. **Suite totale :
  10 (config) + 23 (frontend) + 49 (api) + 22 (p3) + 25 (audit) + 12 (metrics) =
  141 checks verts.**
- **P6 (E2E)** : `tests/test_e2e.py` → 14/14 (Playwright chromium : SPA chargée + FAB
  token-injecté + drawer Studio + audit Owner visible + compose POST `/content` + file
  `/approvals` + audit `content.submit` + approve POST `/approve` + audit
  `approval.approve` + `/metrics` scrapé par le navigateur) ; CI : `playwright==1.57.0`
  + `playwright install --with-deps chromium`. **Suite totale : 10 (config) + 23
  (frontend) + 49 (api) + 22 (p3) + 25 (audit) + 12 (metrics) + 14 (e2e) = 155 checks
  verts.**
- **P7 (observabilité visualisable)** : `tests/test_observability.py` → 38/38 (validation
  statique : `prometheus.yml` scrape `backend:8000/metrics`, `alerts.yml` 3 règles
  référençant les métriques P5 + filtre 5xx + `histogram_quantile`, Grafana
  datasource/dashboard provisioning, `revpulse-overview.json` 6 panels avec
  `rate`/`histogram_quantile`/filtre 5xx, `docker-compose.observability.yml`
  prometheus + grafana + mounts) ; `ruff check backend/app` → 0 erreur. **Suite totale :
  10 (config) + 23 (frontend) + 38 (observability) + 49 (api) + 22 (p3) + 25 (audit) +
  12 (metrics) + 14 (e2e) = 193 checks verts.**
- **P8 (sauvegardes + restauration)** : `tests/test_backups.py` → 40/40 (existence +
  exécutable + `bash -n` valide pour les 3 scripts, `backup_db.sh` `pg_dump --clean
  --if-exists` + gzip + rotation keep-N + vars, `restore_db.sh` psql + gunzip +
  `ON_ERROR_STOP` + confirmation `--yes`, `test_backup_restore.sh` round-trip docker +
  alembic + seed + `DROP SCHEMA` + verify, Makefile cibles `backup`/`restore`/
  `backup-test`, `backups/.gitignore` ignore `*.sql.gz`) ; `ruff` clean. **Suite totale :
  10 (config) + 23 (frontend) + 38 (observability) + 40 (backups) + 49 (api) + 22 (p3) +
  25 (audit) + 12 (metrics) + 14 (e2e) = 233 checks verts.**
- **P9 (sécurité AppSec)** : `tests/test_security.py` → 29/29 (scan_secrets.sh existe +
  exécutable + bash -n + 7 patterns ghp_/github_pat_/AKIA/PRIVATE KEY/sk_live_/xox/AIza
  + allowlist + s'exécute exit 0 code propre, audit_deps.sh pip-audit + npm audit + skip
  no-lockfile + --strict, security.py bcrypt hashpw/checkpw/gensalt, bcrypt in
  requirements, config.py BaseSettings env-driven, aucun secret ≥ 24 chars hardcoded,
  Makefile audit-deps) ; `ruff` clean. **Suite totale : 10 (config) + 23 (frontend) +
  38 (observability) + 40 (backups) + 29 (security) + 49 (api) + 22 (p3) + 25 (audit) +
  12 (metrics) + 14 (e2e) = 262 checks verts.**

## Compte démo

- **Login** : `demo@revpulse.io` / `demo1234` — ou bouton **« Essayer la démo »**.
- Deux espaces seedés : **RevPulse Demo** (Pro, profil LinkedIn Camille Laurent,
  4 membres dont 1 invitation en attente, 6 approbations, 34 modules) et
  **Acme Growth** (Enterprise, à configurer via l'onboarding).
