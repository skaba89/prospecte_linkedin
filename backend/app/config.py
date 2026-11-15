"""Configuration centralisée — via variables d'environnement ou .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REVPULSE_", extra="ignore")

    database_url: str = "sqlite:///./revpulse.db"
    jwt_secret: str = "revpulse-demo-secret-change-me"
    jwt_algo: str = "HS256"
    jwt_expiry_hours: int = 24
    cors_origins: str = "*"
    # Identifiants du compte démo (recréés idempotemment par le seed)
    demo_email: str = "demo@revpulse.io"
    demo_password: str = "demo1234"


settings = Settings()
