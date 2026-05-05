from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gemini_api_key: str
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    jwt_secret: str = "change-me-in-production"
    jwt_expiration_minutes: int = 480

    class Config:
        env_file = ".env"

settings = Settings()