import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from core.api.v1.router import api_router
from core.database import Base, engine, SessionLocal
from core.services.outbox_service import process_outbox

logger = logging.getLogger(__name__)

async def outbox_background_worker(interval_seconds: float = 2.0):
    """
    Automatic transactional outbox background worker.
    Runs periodically during application lifespan to poll and deliver PENDING notifications.
    """
    logger.info("[OUTBOX-WORKER] Background notification outbox worker started.")
    while True:
        try:
            with SessionLocal() as db:
                result = process_outbox(db, batch_size=20)
                if result.processed_count > 0:
                    logger.info(
                        f"[OUTBOX-WORKER] Processed {result.processed_count} items: "
                        f"{result.success_count} succeeded, {result.failed_count} failed."
                    )
        except asyncio.CancelledError:
            logger.info("[OUTBOX-WORKER] Background outbox worker stopping...")
            break
        except Exception as e:
            logger.error(f"[OUTBOX-WORKER] Unexpected error in worker loop: {e}")
        await asyncio.sleep(interval_seconds)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: verify/create schema and launch background outbox worker if enabled
    Base.metadata.create_all(bind=engine)
    worker_task = None
    if settings.RUN_OUTBOX_WORKER_IN_LIFESPAN:
        worker_task = asyncio.create_task(outbox_background_worker(interval_seconds=2.0))
    yield
    # 2. Shutdown: gracefully stop outbox worker
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Централізоване ядро бізнес-правил, транзакційного outbox та аналітики для агентства нерухомості «Рентком»",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/")
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "docs_url": "/docs",
        "health_url": f"{settings.API_V1_STR}/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("core.main:app", host="0.0.0.0", port=8000, reload=True)
