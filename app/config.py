from pydantic import model_validator
from pydantic_settings import BaseSettings

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    gemini_api_key: str
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expiration_minutes: int = 480

    # Environment: "development" allows the default JWT secret; any other
    # value (e.g. "staging", "production") requires JWT_SECRET to be set.
    environment: str = "development"

    # Comma-separated list of email addresses that should have the 'admin' role.
    # Roles are re-synced on every login, so adding/removing an email here
    # takes effect the next time that user logs in.
    admin_emails: str = ""

    # Google OAuth SSO
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    allowed_email_domain: str = "spectrumgroupe.fr"
    frontend_origin: str = "http://localhost:3000"

    class Config:
        env_file = ".env"

    @model_validator(mode="after")
    def _enforce_jwt_secret(self) -> "Settings":
        if self.environment.lower() != "development" and self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET must be set to a non-default value when ENVIRONMENT is not 'development'."
            )
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            # Loud dev warning so it's not silently shipped to prod
            print("[Config] WARNING: using default JWT_SECRET — only safe for local development.")
        return self


    def admin_emails_set(self) -> set[str]:
        """Parsed lowercase set of admin emails."""
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


settings = Settings()