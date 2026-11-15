FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Dépendances (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif + migrations Alembic (init_db applique `alembic upgrade head`)
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/

# En docker : backend API-only (le frontend est servi par nginx).
# La BDD par défaut pointe vers le service `db` (Postgres) du compose.
# ⚠️ REVPULSE_ENV=production → fail-fast : REVPULSE_JWT_SECRET DOIT être surchargé
#    (≥ 32 chars, non défaut) au démarrage, sinon uvicorn crash avec un message clair.
ENV REVPULSE_DATABASE_URL="postgresql+psycopg2://revpulse:revpulse@db:5432/revpulse" \
    REVPULSE_FRONTEND_DIR="/nonexistent" \
    REVPULSE_ENV="production" \
    REVPULSE_JWT_SECRET="change-me-in-production"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
