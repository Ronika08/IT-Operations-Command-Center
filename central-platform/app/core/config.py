"""
Configuration management.

All environment-specific values (DB connection, service URLs, thresholds,
LLM API settings) come from environment variables, never hard-coded. This
is what lets the exact same image run in dev/test/staging/prod with only
the .env file changing - one of the fundamentals the project is meant to
demonstrate in practice, not just in theory.
"""
import os
from functools import lru_cache
from typing import List


class Settings:
    # --- Database ---
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/itopscc",
    )

    # --- Monitored services (name -> base URL) ---
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
    ORDERS_SERVICE_URL: str = os.getenv("ORDERS_SERVICE_URL", "http://localhost:8002")
    PAYMENTS_SERVICE_URL: str = os.getenv("PAYMENTS_SERVICE_URL", "http://localhost:8003")
    DB_PROXY_SERVICE_URL: str = os.getenv("DB_PROXY_SERVICE_URL", "http://localhost:8004")

    # --- Prometheus ---
    PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

    # --- Incident detection thresholds (non-functional requirement:
    #     configurable without a code change / redeploy) ---
    ERROR_RATE_THRESHOLD: float = float(os.getenv("ERROR_RATE_THRESHOLD", "0.10"))  # 10%
    LATENCY_P95_THRESHOLD_SECONDS: float = float(os.getenv("LATENCY_P95_THRESHOLD_SECONDS", "1.0"))
    AVAILABILITY_THRESHOLD: float = float(os.getenv("AVAILABILITY_THRESHOLD", "0.99"))  # 99%
    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

    # --- LLM (Google Gemini) for RCA explanation ---
    # CODE-QUALITY FIX: this used to be ANTHROPIC_API_KEY/ANTHROPIC_MODEL,
    # left over from an earlier design that was never actually used - the
    # real call in app/rca/llm_explainer.py has always targeted Gemini and
    # read GEMINI_API_KEY/GEMINI_MODEL via os.getenv() directly, bypassing
    # this Settings class entirely (see the audit's "dead config" finding).
    # Removed the unused Anthropic fields and centralized the Gemini
    # settings here instead, so there is exactly one source of truth for
    # environment configuration, as the rest of this file already does.
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    LLM_ENABLED: bool = os.getenv("LLM_ENABLED", "true").lower() == "true"

    # --- Auth (Priority 4) ---
    # JWT_SECRET_KEY MUST be overridden outside local development (see
    # .env.example). The fallback below is intentionally obviously-fake
    # so it can never be mistaken for a real secret.
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "dev-only-jwt-secret-change-me")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8h shift

    # --- Demo user seed passwords ---
    # Never hard-coded: each defaults to an obviously-fake placeholder ONLY
    # if the env var is absent, and main.py logs a warning whenever a
    # default is actually used (see seed_reference_data()). Real
    # deployments must set these via environment/secret manager.
    SEED_ADMIN_PASSWORD: str = os.getenv("SEED_ADMIN_PASSWORD", "changeme-admin")
    SEED_IT_SUPPORT_PASSWORD: str = os.getenv("SEED_IT_SUPPORT_PASSWORD", "changeme-itsupport")
    SEED_INCIDENT_MANAGER_PASSWORD: str = os.getenv("SEED_INCIDENT_MANAGER_PASSWORD", "changeme-incidentmgr")
    SEED_RCA_REVIEWER_PASSWORD: str = os.getenv("SEED_RCA_REVIEWER_PASSWORD", "changeme-rcareviewer")

    # --- CORS (frontend origin) ---
    CORS_ORIGINS: List[str] = os.getenv("CORS_ORIGINS", "*").split(",")

    @property
    def monitored_services(self) -> dict:
        return {
            "auth-service": self.AUTH_SERVICE_URL,
            "orders-service": self.ORDERS_SERVICE_URL,
            "payments-service": self.PAYMENTS_SERVICE_URL,
            "db-proxy-service": self.DB_PROXY_SERVICE_URL,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
