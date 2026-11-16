# RevPulse stack — Makefile d'orchestration.
# Usage : `make help` pour la liste des cibles.

COMPOSE      := docker compose
COMPOSE_DEV  := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
BACKEND_URL  ?= http://localhost:8000
PY           := python3

.DEFAULT_GOAL := help
.PHONY: help build up down rebuild logs ps test test-api test-local lint fmt dev dev-up dev-down dev-logs clean db-shell backend-shell backend-logs health backup restore backup-test audit-deps

help: ## Affiche cette aide
	@awk 'BEGIN{FS=":.*##"; printf "\n\033[1mRevPulse stack\033[0m — make <target>\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nVariables: BACKEND_URL=%s (pour test-api)\n\n" "$(BACKEND_URL)"

# ---------- Stack production (docker compose) ----------
build: ## Build les images (backend + frontend)
	$(COMPOSE) build

up: ## Démarre la stack en arrière-plan (db + backend + frontend)
	$(COMPOSE) up -d --build

down: ## Arrête la stack
	$(COMPOSE) down

rebuild: ## Rebuild sans cache + redémarre
	$(COMPOSE) build --no-cache && $(COMPOSE) up -d

logs: ## Suit les logs de tous les services
	$(COMPOSE) logs -f --tail=100

ps: ## État des services
	$(COMPOSE) ps

health: ## Vérifie /api/health (nécessite stack up)
	@curl -sf $(BACKEND_URL)/api/health && echo " ✓ API OK" || (echo " ✗ API injoignable sur $(BACKEND_URL)"; exit 1)

# ---------- Tests & lint ----------
lint: ## Lint du backend (ruff)
	cd backend && ruff check app

fmt: ## Formatte + auto-fix le backend (ruff)
	cd backend && ruff check app --fix

test-api: ## Test l'API contre une stack démarrée (BACKEND_URL)
	BACKEND_URL=$(BACKEND_URL) $(PY) tests/test_api.py

# Test 100% local sans docker : démarre uvicorn (sqlite) en arrière-plan,
# joue tests/test_api.py, puis arrête uvicorn.
test-local: ## Test l'API en local sans docker (sqlite, démarre/arrête uvicorn)
	@(cd backend && REVPULSE_DATABASE_URL="sqlite:///./test.db" REVPULSE_FRONTEND_DIR=/nonexistent \
		$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning) & \
	echo $$! > /tmp/revpulse_test.pid; \
	sleep 5; \
	BACKEND_URL=http://127.0.0.1:8000 $(PY) tests/test_api.py; \
	status=$$?; \
	kill `cat /tmp/revpulse_test.pid` 2>/dev/null; rm -f backend/test.db /tmp/revpulse_test.pid; \
	exit $$status

test: lint ## Lint + (test-local si docker absent, sinon test-api)
	@if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then \
		echo "→ docker détecté : lancez 'make up' puis 'make test-api'"; \
	else \
		echo "→ docker absent : test local (sqlite)"; $(MAKE) test-local; \
	fi

# ---------- Mode dev (hot-reload) ----------
dev: ## Démarre en mode dev (hot-reload backend + frontend monté)
	$(COMPOSE_DEV) up --build

dev-up: ## Mode dev en arrière-plan
	$(COMPOSE_DEV) up -d --build

dev-down: ## Arrête le mode dev
	$(COMPOSE_DEV) down

dev-logs: ## Logs du mode dev
	$(COMPOSE_DEV) logs -f --tail=100

# ---------- Ops ----------
db-shell: ## Shell SQL dans Postgres
	$(COMPOSE) exec db psql -U revpulse -d revpulse

backend-shell: ## Shell dans le conteneur backend
	$(COMPOSE) exec backend bash

backend-logs: ## Logs du backend uniquement
	$(COMPOSE) logs -f --tail=200 backend

clean: ## ⚠️ Arrête + supprime les volumes (efface la BDD)
	$(COMPOSE) down -v
	@echo "Volumes supprimés. Relancez 'make up' pour re-seed."

# ---------- Backups Postgres (P8) ----------
backup: ## Sauvegarde la BDD (pg_dump --clean gzippé + rotation keep-10)
	./scripts/backup_db.sh

restore: ## Restaure un dump : make restore FILE=backups/revpulse_YYYYMMDD_HHMMSS.sql.gz
	@test -n "$(FILE)" || { echo "Usage: make restore FILE=backups/<dump>.sql.gz"; exit 1; }
	./scripts/restore_db.sh "$(FILE)" --yes

backup-test: ## Test E2E backup→restore sur un postgres éphémère (nécessite docker)
	./scripts/test_backup_restore.sh

# ---------- Sécurité AppSec (P9) ----------
audit-deps: ## Audit advisory des dépendances (pip-audit + npm audit ; --strict pour fail)
	./scripts/audit_deps.sh
