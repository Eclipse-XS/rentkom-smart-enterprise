from fastapi import APIRouter
from core.api.v1.endpoints import requests, feedbacks, notifications, rag, analytics, health, telegram

api_router = APIRouter()

api_router.include_router(requests.router, prefix="/requests", tags=["Requests"])
api_router.include_router(feedbacks.router, prefix="/feedbacks", tags=["Feedbacks"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(rag.router, prefix="/rag", tags=["AI & Knowledge"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(telegram.router, prefix="/telegram", tags=["Telegram Gateway"])
api_router.include_router(health.router, tags=["Diagnostics"])
