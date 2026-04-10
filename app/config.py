from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    gemini_api_key: str
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    class Config:
        env_file = ".env"

settings = Settings()