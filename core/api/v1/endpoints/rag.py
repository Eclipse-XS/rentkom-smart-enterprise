import asyncio
import contextlib
import json
import hashlib
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.schemas import RAGQueryRequest, RAGQueryResponse
from core.services.rag_service import query_rag
from core.security import check_rag_rate_limit, require_internal_or_admin_auth, AuthenticatedActor
from core.config import settings
from core.database import get_db
from core.models import SystemConfig

router = APIRouter()
MAX_HISTORY_MESSAGES = 12
HISTORY_RETENTION_HOURS = 24


def _history_key(actor: AuthenticatedActor, session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"rag_history:{actor.role}:{digest}"


def _is_history_expired(updated_at) -> bool:
    if not updated_at:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at < datetime.now(timezone.utc) - timedelta(hours=HISTORY_RETENTION_HOURS)

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
async def ask_rag(
    query: RAGQueryRequest,
    db: Session = Depends(get_db),
    actor: AuthenticatedActor = Depends(require_internal_or_admin_auth),
):
    """Executes grounded RAG retrieval and inference, applying guardrails and resilient fallback."""
    history = list(query.conversation_history or [])[-MAX_HISTORY_MESSAGES:]
    config_row = None
    if query.session_id:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=HISTORY_RETENTION_HOURS)
        db.query(SystemConfig).filter(
            SystemConfig.key.like("rag_history:%"),
            SystemConfig.updated_at < cutoff,
        ).delete(synchronize_session=False)
        history_key = _history_key(actor, query.session_id)
        config_row = db.query(SystemConfig).filter(SystemConfig.key == history_key).first()
        if config_row:
            if _is_history_expired(config_row.updated_at):
                history = []
                db.delete(config_row)
                db.flush()
                config_row = None
            else:
                try:
                    history = json.loads(config_row.value)[-MAX_HISTORY_MESSAGES:]
                except (TypeError, ValueError):
                    history = []

    if query.typing_chat_id is not None:
        if not query.session_id or str(query.typing_chat_id) != query.session_id:
            raise HTTPException(
                status_code=400,
                detail="typing_chat_id must match the authenticated Telegram session_id",
            )

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

    if query.session_id and response.mode != "rejected":
        bounded = (history + [
            {"role": "user", "content": query.question},
            {"role": "assistant", "content": response.answer[:4000]},
        ])[-MAX_HISTORY_MESSAGES:]
        if config_row:
            config_row.value = json.dumps(bounded, ensure_ascii=False)
        else:
            db.add(SystemConfig(
                key=history_key,
                value=json.dumps(bounded, ensure_ascii=False),
            ))
        db.commit()
    return response
