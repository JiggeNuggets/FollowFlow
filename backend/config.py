# config.py — Application settings loaded from .env
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "gmail_followup"

    # Auth
    SECRET_KEY: str = "change-this-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # OpenAI
    OPENAI_API_KEY: str = ""

    # Gmail OAuth2
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/gmail/callback"

    # Follow-up schedule
    FOLLOWUP_DAY_1: int = 3
    FOLLOWUP_DAY_2: int = 5
    FOLLOWUP_DAY_3: int = 7

    class Config:
        env_file = ".env"


settings = Settings()
