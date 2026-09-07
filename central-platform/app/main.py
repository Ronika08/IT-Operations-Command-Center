"""
Central Platform - main entrypoint.

Wires together: DB init + seed (services, SLA rules, demo users), the
auth/incidents/services/sla/conversion/audit routers, and a background
asyncio task that polls all monitored services on a fixed interval
(settings.POLL_INTERVAL_SECONDS), ingesting real logs, detecting real
recoveries, and opening real incidents when real thresholds are breached.

Run standalone:
    uvicorn app.main:app --reload --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.security import hash_password
from app.models.db import SLARule, Service, Severity, User, UserRole
from app.routers import audit, auth, conversion, incidents, services, sla
from app.sla.engine import DEFAULT_SLA_MINUTES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("central-platform")
settings = get_settings()

# Username -> (role, configured password, settings attr name) for the
# startup seed. Passwords always come from Settings (i.e. environment
# variables), never hard-coded - see core/config.py's SEED_* fields and
# .env.example. A default is only used when the env var is genuinely
# absent, and that fact is logged loudly below so it's never mistaken
# for a real production secret.
_DEMO_USERS = [
    ("admin", UserRole.ADMIN, "SEED_ADMIN_PASSWORD"),
    ("it_support", UserRole.IT_SUPPORT, "SEED_IT_SUPPORT_PASSWORD"),
    ("incident_manager", UserRole.INCIDENT_MANAGER, "SEED_INCIDENT_MANAGER_PASSWORD"),
    ("rca_reviewer", UserRole.RCA_REVIEWER, "SEED_RCA_REVIEWER_PASSWORD"),
]


def seed_reference_data():
    """Idempotently seeds the 4 monitored services, default SLA rules,
    and one demo user per role. Safe to run on every startup - checks
    for existing rows first."""
    db = SessionLocal()
    try:
        if db.query(Service).count() == 0:
            for name, url in settings.monitored_services.items():
                db.add(Service(name=name, base_url=url, description=f"{name} (auto-seeded)"))
            db.commit()
            logger.info("Seeded %d services", len(settings.monitored_services))

        if db.query(SLARule).count() == 0:
            for severity, minutes in DEFAULT_SLA_MINUTES.items():
                db.add(SLARule(
                    severity=Severity(severity.value),
                    response_minutes=minutes["response"],
                    resolution_minutes=minutes["resolution"],
                    description=f"Default {severity.value} SLA policy",
                ))
            db.commit()
            logger.info("Seeded default SLA rules")

        if db.query(User).count() == 0:
            for username, role, settings_attr in _DEMO_USERS:
                password = getattr(settings, settings_attr)
                if password.startswith("changeme-"):
                    logger.warning(
                        "Seeding demo user '%s' with the DEFAULT placeholder password "
                        "(env var %s not set). Fine for local/interview demo use only - "
                        "set %s before any shared/real deployment.",
                        username, settings_attr, settings_attr,
                    )
                db.add(User(username=username, password_hash=hash_password(password), role=role))
            db.commit()
            logger.info("Seeded %d demo users (one per role)", len(_DEMO_USERS))
    finally:
        db.close()


async def poll_loop():
    """Background task: polls every service every POLL_INTERVAL_SECONDS.
    Wrapped in try/except so one bad poll (a service being down, which is
    exactly when we most need this to keep running) never kills the loop."""
    from app.ingestion.collector import poll_all_services

    while True:
        db = SessionLocal()
        try:
            opened = poll_all_services(db)
            if opened:
                logger.warning("Opened %d new incident(s): %s", len(opened), [i.title for i in opened])
        except Exception:
            logger.exception("poll_loop iteration failed")
        finally:
            db.close()
        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_reference_data()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title="IT Operations Command Center - Central Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(incidents.router)
app.include_router(services.router)
app.include_router(sla.router)
app.include_router(conversion.router)
app.include_router(audit.router)


@app.get("/health")
def health():
    return {"service": "central-platform", "status": "healthy"}
