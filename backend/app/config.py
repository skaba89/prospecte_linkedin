"""Configuration centralisée — via variables d'environnement ou .env.

Fail-fast sécurité : en production (REVPULSE_ENV=production), un JWT secret par
défaut ou trop court fait planter le démarrage (validate_jwt_secret). En
development/test, les secrets de démo sont tolérés pour préserver l'ergonomie
locale et la CI. La fonction validate_jwt_secret est extraite et testable
unitairement (tests/test_config.py) indépendamment de Settings.
"""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Secrets manifestement non-productionnels — rejetés en production (fail-fast).
INSECURE_SECRETS = {
    "",
    "change-me-in-production",
    "revpulse-demo-secret-change-me",
    "dev-secret-not-for-prod",
    "changeme",
    "secret",
}


def validate_jwt_secret(env: str, secret: str) -> None:
    """Fail-fast : en production, le secret JWT doit être ≥ 32 caractères et ne
    pas être une valeur par défaut connue. Lève ValueError sinon. Ne fait rien
    hors production (development/test) pour ne pas bloquer dev/CI."""
    if env == "production" and (not secret or secret in INSECURE_SECRETS or len(secret) < 32):
        raise ValueError(
            "REVPULSE_JWT_SECRET inadéquat en production : doit faire ≥ 32 caractères "
            "et ne pas être une valeur par défaut connue. Génère un secret avec : "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REVPULSE_", extra="ignore")

    # Environnement d'exécution : development | test | production.
    # Pilote le fail-fast du JWT secret (production exige un vrai secret).
    env: str = "development"

    database_url: str = "sqlite:///./revpulse.db"
    jwt_secret: str = "revpulse-demo-secret-change-me"
    jwt_algo: str = "HS256"
    jwt_expiry_hours: int = 24
    cors_origins: str = "*"
    # URL publique de l'app (utilisée pour les liens d'invitation e-mail).
    app_base_url: str = "http://localhost:8000"
    # Identifiants du compte démo (recréés idempotemment par le seed)
    demo_email: str = "demo@revpulse.io"
    demo_password: str = "demo1234"
    # LinkedIn OAuth 2.0 — laisser vide pour le mode mock (CI/dev sans App LinkedIn).
    # Renseigner CLIENT_ID/SECRET/REDIRECT_URI depuis https://www.linkedin.com/developers/apps
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/api/linkedin/callback"
    linkedin_mock: bool = False  # force le mock même si des creds sont présentes (tests)

    # P10.2 : chiffrement Fernet des tokens OAuth (rotation via *_PREVIOUS). Requis en prod.
    token_encryption_key: str = ""
    token_encryption_key_previous: str = ""  # ancienne clé (décryptage only, rotation)

    # Rate-limiting login (anti brute-force). Désactivable, configurable.
    rate_limit_enabled: bool = True
    login_rate_limit: str = "5/minute"  # syntaxe slowapi : "N/period" (second/minute/hour/day)

    # SMTP — envoi réel des e-mails d'invitation. Désactivé par défaut (dry-run = log).
    # En prod : renseigner host/port/user/password/from puis passer smtp_enabled=true.
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@revpulse.io"
    smtp_use_tls: bool = True

    # Stripe — P30 Commercialisation. Si stripe_secret_key non renseigné → mode mock (checkout
    # retourne une URL de test, webhook accepte les events sans vérif signature). Anti-fake : le
    # retour checkout porte simulated=True quand mock. En prod : renseigner secret + webhook_secret.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_currency: str = "eur"  # currency code Stripe (lowercase ISO 4217)

    # Logging structuré — JSON (prod) ou texte (dev/test). Niveau configurable.
    log_level: str = "INFO"
    log_format: str = "json"  # json | text

    @property
    def is_production(self) -> bool:
        """P10.4 : gate les routes mock + le compte démo (interdits en production)."""
        return self.env == "production"

    @model_validator(mode="after")
    def _fail_fast_secrets(self):
        validate_jwt_secret(self.env, self.jwt_secret)
        # P10.2 : en production, la clé de chiffrement Fernet des tokens OAuth est requise + valide.
        if self.is_production:
            if not self.token_encryption_key:
                raise ValueError(
                    "REVPULSE_TOKEN_ENCRYPTION_KEY requis en production "
                    "(Fernet key, base64-urlsafe 32 bytes : "
                    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").'
                )
            try:
                from cryptography.fernet import Fernet

                Fernet(self.token_encryption_key.encode())
            except Exception as exc:
                raise ValueError(
                    "REVPULSE_TOKEN_ENCRYPTION_KEY invalide (Fernet key base64-urlsafe 32 bytes attendue)."
                ) from exc
        return self


settings = Settings()
