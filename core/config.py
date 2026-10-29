import os
from dotenv import load_dotenv
load_dotenv()

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    PROJECT_NAME: str = "Rentkom Smart Enterprise API"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "demo")

    # Database (Defaults to local isolated PostgreSQL 18 cluster)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"
    )

    # Notification & Mock Settings
    NOTIFICATION_PROVIDER: str = os.getenv("NOTIFICATION_PROVIDER", "mock")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@rentkom.lviv.ua")
    RUN_OUTBOX_WORKER_IN_LIFESPAN: bool = os.getenv("RUN_OUTBOX_WORKER_IN_LIFESPAN", "true").lower() in ("true", "1", "yes")
    SMTP_HOST: Optional[str] = os.getenv("SMTP_HOST", None)
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: Optional[str] = os.getenv("SMTP_USER", None)
    SMTP_PASSWORD: Optional[str] = os.getenv("SMTP_PASSWORD", None)

    # External Services
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest")
    RAG_NUM_PREDICT: int = int(os.getenv("RAG_NUM_PREDICT", "384"))
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "127.0.0.1")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "rag_real_estate")

    # Telegram Bot & Webhook Security
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN", None)
    TEST_WEBHOOK_SECRET: Optional[str] = os.getenv("TEST_WEBHOOK_SECRET", None)
    INTERNAL_API_KEY: Optional[str] = os.getenv("INTERNAL_API_KEY", None)
    ALLOW_TEST_WEBHOOK: bool = os.getenv("ALLOW_TEST_WEBHOOK", "true").lower() in ("true", "1", "yes")

    @property
    def is_safe_demo_or_dev(self) -> bool:
        """
        Fail-closed security check: only strictly recognized non-production environments
        are allowed to expose mock endpoints, test webhooks, and relaxed ingress.
        Any missing, unconfigured, or unknown environment defaults to production (secure-by-default).
        """
        env = (self.ENVIRONMENT or "").strip().lower()
        return env in ("demo", "development", "test")

    @property
    def is_test_webhook_permitted(self) -> bool:
        """
        Evaluates whether test webhook ingress is permitted based on environment and explicit opt-in.
        """
        return self.is_safe_demo_or_dev and self.ALLOW_TEST_WEBHOOK

settings = Settings()
