import os
from typing import Optional, List
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    PROJECT_NAME: str = "Rentkom Smart Enterprise API"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "demo")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/lab4_real_estate"
    )
    TEST_DATABASE_URL: Optional[str] = os.getenv("TEST_DATABASE_URL", None)

    # Notification & Mock Settings
    NOTIFICATION_PROVIDER: str = os.getenv("NOTIFICATION_PROVIDER", "mock")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@rentkom.lviv.ua")
    RUN_OUTBOX_WORKER_IN_LIFESPAN: bool = os.getenv("RUN_OUTBOX_WORKER_IN_LIFESPAN", "true").lower() in ("true", "1", "yes")
    SMTP_HOST: Optional[str] = os.getenv("SMTP_HOST", None)
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: Optional[str] = os.getenv("SMTP_USER", None)
    SMTP_PASSWORD: Optional[str] = os.getenv("SMTP_PASSWORD", None)
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
    SMTP_TIMEOUT_SECONDS: float = float(os.getenv("SMTP_TIMEOUT_SECONDS", "10.0"))
    TELEGRAM_TIMEOUT_SECONDS: float = float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "10.0"))

    # External Services
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large:latest")
    RAG_NUM_PREDICT: int = int(os.getenv("RAG_NUM_PREDICT", "384"))
    RAG_MIN_SIMILARITY: float = float(os.getenv("RAG_MIN_SIMILARITY", "0.55"))
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "127.0.0.1")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "rag_real_estate_active")

    # Security & Authentication
    ADMIN_API_KEY: Optional[str] = os.getenv("ADMIN_API_KEY", None)
    INTERNAL_API_KEY: Optional[str] = os.getenv("INTERNAL_API_KEY", None)
    TEST_WEBHOOK_SECRET: Optional[str] = os.getenv("TEST_WEBHOOK_SECRET", None)
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN", None)
    TELEGRAM_SECRET_TOKEN: Optional[str] = os.getenv("TELEGRAM_SECRET_TOKEN", None)
    TELEGRAM_ADMIN_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_ADMIN_CHAT_ID", None)
    TELEGRAM_ADMIN_USER_ID: Optional[str] = os.getenv("TELEGRAM_ADMIN_USER_ID", None)
    LR3_FEEDBACK_FORM_URL: str = os.getenv(
        "LR3_FEEDBACK_FORM_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLSd6DMgilLM6j9GtPlkBCDEy8Era5QTevu-vNNxsSwyTsNR-HA/viewform",
    )
    LR4_CONSULTATION_FORM_URL: str = os.getenv(
        "LR4_CONSULTATION_FORM_URL",
        "https://docs.google.com/forms/d/e/1FAIpQLScjfHylsJMnU9nmxP9XCoiZx1HPIWGqUydvBMteWDxuJcgQGw/viewform",
    )
    LR3_SHEET_URL: Optional[str] = os.getenv("LR3_SHEET_URL", None)
    LR4_SHEET_URL: Optional[str] = os.getenv("LR4_SHEET_URL", None)
    GRAFANA_URL: Optional[str] = os.getenv("GRAFANA_URL", None)
    N8N_URL: Optional[str] = os.getenv("N8N_URL", None)
    FASTAPI_DOCS_URL: Optional[str] = os.getenv("FASTAPI_DOCS_URL", None)
    QDRANT_DASHBOARD_URL: Optional[str] = os.getenv("QDRANT_DASHBOARD_URL", None)
    ALLOW_TEST_WEBHOOK: bool = os.getenv("ALLOW_TEST_WEBHOOK", "true").lower() in ("true", "1", "yes")

    # Agency Contact & Support Settings (Configurable fallbacks)
    AGENCY_CONTACT_PHONE: str = os.getenv("AGENCY_CONTACT_PHONE", "+38 (032) 240-00-00")
    AGENCY_CONTACT_EMAIL: str = os.getenv("AGENCY_CONTACT_EMAIL", "support@rentkom.lviv.ua")

    # Public Endpoint Rate Limiting
    RATE_LIMIT_ENABLED: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))
    RATE_LIMIT_RAG_REQUESTS_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_RAG_REQUESTS_PER_MINUTE", "10"))

    # Trusted Proxies for X-Forwarded-For resolution
    TRUSTED_PROXIES: List[str] = [
        p.strip() for p in os.getenv("TRUSTED_PROXIES", "").split(",") if p.strip()
    ]

    # CORS configuration
    CORS_ORIGINS: List[str] = [
        origin.strip() for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"
        ).split(",") if origin.strip()
    ]

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

    def validate_runtime_security(self) -> None:
        """
        Startup integrity check: fails closed in production / connected mode if mandatory secrets
        are missing or use static example/default values.
        """
        if not self.is_safe_demo_or_dev:
            errors = []
            insecure_placeholders = {
                "rentkom-demo-internal-key-2026",
                "rentkom-demo-admin-key-2026",
                "your_secure_random_internal_api_key_32_bytes_hex",
                "your_secure_random_admin_api_key_32_bytes_hex",
                "admin",
                "postgres",
            }
            if (
                not self.INTERNAL_API_KEY
                or self.INTERNAL_API_KEY.startswith("rentkom-demo")
                or self.INTERNAL_API_KEY in insecure_placeholders
            ):
                errors.append("INTERNAL_API_KEY must be configured with a non-demo secret in production/connected mode.")
            if (
                not self.ADMIN_API_KEY
                or self.ADMIN_API_KEY.startswith("rentkom-demo")
                or self.ADMIN_API_KEY in insecure_placeholders
            ):
                errors.append("ADMIN_API_KEY must be configured with a non-demo secret in production/connected mode.")

            valid_providers = ("mock", "smtp", "telegram")
            if self.NOTIFICATION_PROVIDER not in valid_providers:
                errors.append(f"Invalid NOTIFICATION_PROVIDER '{self.NOTIFICATION_PROVIDER}'. Supported: {valid_providers}")

            env_lower = (self.ENVIRONMENT or "").strip().lower()
            if env_lower == "production":
                if self.NOTIFICATION_PROVIDER == "mock":
                    errors.append("NOTIFICATION_PROVIDER cannot be 'mock' in production mode.")
                if self.NOTIFICATION_PROVIDER == "telegram" and not self.TELEGRAM_BOT_TOKEN:
                    errors.append("TELEGRAM_BOT_TOKEN must be configured when NOTIFICATION_PROVIDER is 'telegram'.")
                if self.NOTIFICATION_PROVIDER == "smtp" and not self.SMTP_HOST:
                    errors.append("SMTP_HOST must be configured when NOTIFICATION_PROVIDER is 'smtp'.")
                try:
                    parsed_db = urlparse(self.DATABASE_URL)
                    if parsed_db.password in ("postgres", "admin", "") and not self.DATABASE_URL.startswith("sqlite"):
                        errors.append("DATABASE_URL cannot use default/empty password ('postgres'/'admin') in production mode.")
                except Exception:
                    pass
                if self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_BOT_TOKEN.startswith("demo"):
                    errors.append("Demo TELEGRAM_BOT_TOKEN cannot be used in production mode.")

            if errors:
                raise RuntimeError("Security Startup Failure: " + " | ".join(errors))


settings = Settings()
