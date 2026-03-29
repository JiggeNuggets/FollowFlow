# config.py — Safe settings with fallbacks for every variable.
# The app will START even if nothing is configured.
# Missing keys simply disable the relevant feature (Gmail, OpenAI, PayPal).

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Auth ──────────────────────────────────────────────────
    # Used to sign JWT tokens. A default is provided so the app
    # starts on Render even before you add env vars.
    SECRET_KEY: str = "dev-secret-change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440          # 24 hours

    # ── Database ──────────────────────────────────────────────
    # SQLite by default — no external database needed.
    # Override with a full SQLAlchemy URL to switch databases later.
    DATABASE_URL: str = "sqlite:///./followflow.db"

    # ── OpenAI (optional) ─────────────────────────────────────
    # Leave blank → AI follow-ups fall back to static templates.
    OPENAI_API_KEY: str = ""

    # ── Gmail OAuth2 (optional) ───────────────────────────────
    # Leave blank → Gmail features are disabled gracefully.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/gmail/callback"

    # ── PayPal (optional) ─────────────────────────────────────
    # Get from https://developer.paypal.com → My Apps → REST API apps
    # Use sandbox credentials for testing, live for production.
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    # "sandbox" for testing, "live" for production
    PAYPAL_MODE: str = "sandbox"

    # ── Follow-up schedule ────────────────────────────────────
    FOLLOWUP_DAY_1: int = 3
    FOLLOWUP_DAY_2: int = 5
    FOLLOWUP_DAY_3: int = 7

    model_config = SettingsConfigDict(
        env_file=".env",          # reads backend/.env if it exists
        env_file_encoding="utf-8",
        extra="ignore",           # silently ignore unknown env vars
    )

    # ── Computed helpers (not env vars) ───────────────────────
    @property
    def paypal_base_url(self) -> str:
        """PayPal API base URL — sandbox or live."""
        if self.PAYPAL_MODE == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def gmail_configured(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def openai_configured(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def paypal_configured(self) -> bool:
        return bool(self.PAYPAL_CLIENT_ID and self.PAYPAL_CLIENT_SECRET)


# Single shared instance — import this everywhere
settings = Settings()
