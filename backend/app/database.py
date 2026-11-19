"""Moteur SQLAlchemy, session, Base déclarative. Compatible SQLite (local) et Postgres (docker).

Le schéma est géré par Alembic (backend/alembic) — init_db() applique
`alembic upgrade head` (source de vérité). Fallback create_all uniquement si
Alembic n'est pas installé (env minimal sans alembic dans requirements) ; les
erreurs de migration réelles sont propagées (fail-fast).
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Applique les migrations Alembic (source de vérité du schéma).

    Fallback `Base.metadata.create_all` si Alembic n'est pas installé. Les
    erreurs de migration (autre qu'ImportError) sont propagées : on ne masque
    pas un vrai problème de schéma.
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        Base.metadata.create_all(bind=engine)
        return
    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
