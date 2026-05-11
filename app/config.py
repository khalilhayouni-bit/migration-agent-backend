from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gemini_api_key: str
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    jwt_secret: str = "change-me-in-production"
    jwt_expiration_minutes: int = 480

    # Google OAuth SSO
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    allowed_email_domain: str = "spectrumgroupe.fr"
    frontend_origin: str = "http://localhost:3000"

    class Config:
        env_file = ".env"

settings = Settings()