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
    # LinkedIn OAuth 2.0 — laisser vide pour le mode mock (CI/dev sans App LinkedIn).
    # Renseigner CLIENT_ID/SECRET/REDIRECT_URI depuis https://www.linkedin.com/developers/apps
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/api/linkedin/callback"
    linkedin_mock: bool = False  # force le mock même si des creds sont présentes (tests)


settings = Settings()
