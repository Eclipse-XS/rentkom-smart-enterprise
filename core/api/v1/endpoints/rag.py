import asyncio
import contextlib
import json
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.schemas import RAGQueryRequest, RAGQueryResponse
from core.services.rag_service import query_rag
from core.security import check_rag_rate_limit
from core.config import settings
from core.database import get_db
from core.models import SystemConfig

router = APIRouter()

async def _typing_loop(chat_id: int):
    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendChatAction",
                    json={"chat_id": chat_id, "action": "typing"},
                )
        except Exception:
            pass
        await asyncio.sleep(4)


@router.post("/ask", response_model=RAGQueryResponse, dependencies=[Depends(check_rag_rate_limit)])
async def ask_rag(query: RAGQueryRequest, db: Session = Depends(get_db)):
    """Executes grounded RAG retrieval and inference, applying guardrails and resilient fallback."""
    history = list(query.conversation_history or [])[-12:]
    config_row = None
    if query.session_id:
        config_row = db.query(SystemConfig).filter(SystemConfig.key == f"rag_history:{query.session_id}").first()
        if config_row:
            try:
                history = json.loads(config_row.value)[-12:]
            except (TypeError, ValueError):
                history = []

    typing_task = None
    if query.typing_chat_id and settings.TELEGRAM_BOT_TOKEN:
        typing_task = asyncio.create_task(_typing_loop(query.typing_chat_id))
    try:
        response = await query_rag(query.question, history)
    finally:
        if typing_task:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    if query.session_id:
        bounded = (history + [
            {"role": "user", "content": query.question},
            {"role": "assistant", "content": response.answer},
        ])[-12:]
        if config_row:
            config_row.value = json.dumps(bounded, ensure_ascii=False)
        else:
            db.add(SystemConfig(
                key=f"rag_history:{query.session_id}",
                value=json.dumps(bounded, ensure_ascii=False),
            ))
        db.commit()
    return response
