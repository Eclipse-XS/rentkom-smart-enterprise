import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from core.database import get_db
from core.models import NotificationOutbox
from core.schemas import HealthResponse
from core.config import settings

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def check_health(db: Session = Depends(get_db)):
    """Performs deep health check across database, Qdrant, Ollama, and notification backlog."""
    # 1. Database check
    db_status = "unhealthy"
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)[:50]}"

    # 2. Qdrant check
    qdrant_status = "unhealthy"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/healthz")
            if resp.status_code == 200:
                qdrant_status = "healthy"
            else:
                qdrant_status = f"degraded (HTTP {resp.status_code})"
    except Exception:
        qdrant_status = "cold/offline"

    # 3. Ollama check
    ollama_status = "unhealthy"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                ollama_status = "healthy"
            else:
                ollama_status = f"degraded (HTTP {resp.status_code})"
    except Exception:
        ollama_status = "cold/offline"

    # 4. Outbox Pending Backlog
    pending_count = 0
    try:
        pending_count = db.query(NotificationOutbox).filter(
            NotificationOutbox.status == "PENDING"
        ).count()
    except Exception:
        pass

    overall_status = "healthy"
    if db_status != "healthy":
        overall_status = "unhealthy"
    elif qdrant_status != "healthy" or ollama_status != "healthy":
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        database=db_status,
        qdrant=qdrant_status,
        ollama=ollama_status,
        outbox_pending_count=pending_count
    )
