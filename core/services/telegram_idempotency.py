import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

class CachedResponse(BaseModel):
    route: str
    response_text: str
    reply_markup: Optional[Dict[str, Any]] = None
    sources: Optional[list] = None
    confidence_score: Optional[float] = None
    processed_at: float = 0.0
    is_duplicate: bool = False

class TelegramIdempotencyManager:
    """
    Enterprise-grade, PostgreSQL-backed and async-safe idempotency manager for Telegram updates.
    Prevents duplicate processing, handles concurrent workers via database row leases,
    caches responses, survives process restarts, and provides crash recovery.
    """
    def __init__(self, ttl_seconds: int = 3600, lease_timeout_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self.lease_timeout_seconds = lease_timeout_seconds
        self._in_memory_cache: Dict[str, CachedResponse] = {}
        self._in_progress: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def get_event_key(self, update: Dict[str, Any]) -> str:
        update_id = update.get("update_id")
        if update_id is not None:
            return f"tg_update:{update_id}"

        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id", "0")
        msg_id = message.get("message_id", "0")
        text_content = (message.get("text") or "").strip()
        return f"tg_fallback:{chat_id}:{msg_id}:{hash(text_content)}"

    def parse_update_id(self, update: Dict[str, Any]) -> Tuple[int, int, str]:
        """Extracts (update_id, chat_id, message_text) with deterministic integer update_id."""
        update_id = update.get("update_id")
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id") or 0
        text_content = (message.get("text") or "").strip()

        if update_id is None:
            # Deterministic synthetic positive update_id from chat_id + message_id or text
            msg_id = message.get("message_id")
            if msg_id is not None:
                update_id = abs(int(f"{chat_id}{msg_id}") % (2**60))
            else:
                update_id = abs(hash(f"{chat_id}:{text_content}")) % (2**60)
        else:
            update_id = int(update_id)

        return update_id, int(chat_id), text_content

    async def acquire_or_get_cached(
        self,
        event_key: str,
        db: Optional[Session] = None,
        update_data: Optional[Dict[str, Any]] = None,
        bot_id: str = "rentkom_bot"
    ) -> Tuple[bool, Optional[CachedResponse]]:
        """
        Acquires processing rights or returns cached response.
        If db is provided, uses PostgreSQL telegram_inbox table for cross-process locking and restart persistence.
        """
        # If DB session is provided, use PostgreSQL-backed atomic UPSERT with lease
        if db is not None:
            try:
                update_id, chat_id, text_content = self.parse_update_id(update_data or {})

                # 1. Check if already completed in DB
                check_res = db.execute(text("""
                    SELECT status, response_payload, locked_at, retry_count
                    FROM public.telegram_inbox
                    WHERE bot_id = :bot_id AND update_id = :update_id;
                """), {"bot_id": bot_id, "update_id": update_id}).fetchone()

                if check_res:
                    status, payload, locked_at, retry_count = check_res
                    if status == "COMPLETED" and payload:
                        logger.info(f"[IDEMPOTENCY-DB] Duplicate Telegram update {update_id} already COMPLETED. Returning cached response.")
                        dup = CachedResponse(**payload)
                        dup.is_duplicate = True
                        return False, dup

                    # If currently PROCESSING and lease hasn't expired (< 30s)
                    if status == "PROCESSING":
                        now_utc = datetime.now(timezone.utc)
                        # Normalize locked_at to timezone-aware UTC if needed
                        if locked_at.tzinfo is None:
                            locked_at = locked_at.replace(tzinfo=timezone.utc)
                        if now_utc - locked_at < timedelta(seconds=self.lease_timeout_seconds):
                            logger.info(f"[IDEMPOTENCY-DB] Telegram update {update_id} is in-progress by another worker (lease active). Suppressing duplicate.")
                            if payload:
                                dup = CachedResponse(**payload)
                                dup.is_duplicate = True
                                return False, dup
                            return False, CachedResponse(
                                route="In Progress",
                                response_text="Запит обробляється, зачекайте будь ласка...",
                                is_duplicate=True
                            )

                # 2. Atomic claim via UPSERT with lease recovery
                claim_res = db.execute(text("""
                    INSERT INTO public.telegram_inbox (
                        bot_id, update_id, chat_id, message_text, status, locked_at, retry_count, created_at
                    )
                    VALUES (
                        :bot_id, :update_id, :chat_id, :message_text, 'PROCESSING', NOW(), 0, NOW()
                    )
                    ON CONFLICT (bot_id, update_id) DO UPDATE
                    SET locked_at = NOW(),
                        retry_count = telegram_inbox.retry_count + 1,
                        status = 'PROCESSING'
                    WHERE telegram_inbox.status = 'FAILED'
                       OR (telegram_inbox.status = 'PROCESSING' AND telegram_inbox.locked_at < NOW() - INTERVAL '30 seconds')
                    RETURNING id, status;
                """), {
                    "bot_id": bot_id,
                    "update_id": update_id,
                    "chat_id": chat_id,
                    "message_text": text_content
                }).fetchone()

                db.commit()

                if claim_res:
                    logger.info(f"[IDEMPOTENCY-DB] Claimed processing lease for update {update_id} (row {claim_res[0]}).")
                    return True, None

            except Exception as e:
                logger.warning(f"[IDEMPOTENCY-DB] Database lock check failed ({e}). Falling back to memory guard.")
                db.rollback()

        # In-memory fallback
        async with self._lock:
            if event_key in self._in_memory_cache:
                cached = self._in_memory_cache[event_key]
                if time.time() - cached.processed_at < self.ttl_seconds:
                    logger.info(f"[IDEMPOTENCY-MEM] Duplicate event detected for {event_key}. Returning cached response.")
                    dup = cached.model_copy()
                    dup.is_duplicate = True
                    return False, dup
                else:
                    del self._in_memory_cache[event_key]

            if event_key in self._in_progress:
                event = self._in_progress[event_key]
                wait_needed = True
            else:
                event = asyncio.Event()
                self._in_progress[event_key] = event
                return True, None

        # Wait for concurrent task to finish (max 15s)
        try:
            await asyncio.wait_for(event.wait(), timeout=15.0)
            async with self._lock:
                cached = self._in_memory_cache.get(event_key)
                if cached:
                    dup = cached.model_copy()
                    dup.is_duplicate = True
                    return False, dup
        except asyncio.TimeoutError:
            logger.warning(f"[IDEMPOTENCY-MEM] Concurrent wait timed out for {event_key}.")

        return True, None

    async def finish_processing(
        self,
        event_key: str,
        response: CachedResponse,
        db: Optional[Session] = None,
        update_data: Optional[Dict[str, Any]] = None,
        bot_id: str = "rentkom_bot"
    ):
        """Records the completed response in PostgreSQL inbox and memory cache, unblocking parallel tasks."""
        # 1. Update PostgreSQL inbox
        if db is not None:
            try:
                update_id, _, _ = self.parse_update_id(update_data or {})
                db.execute(text("""
                    UPDATE public.telegram_inbox
                    SET status = 'COMPLETED',
                        response_payload = CAST(:payload AS jsonb),
                        completed_at = NOW()
                    WHERE bot_id = :bot_id AND update_id = :update_id;
                """), {
                    "payload": response.model_dump_json(),
                    "bot_id": bot_id,
                    "update_id": update_id
                })
                db.commit()
                logger.info(f"[IDEMPOTENCY-DB] Persisted COMPLETED state for Telegram update {update_id}.")
            except Exception as e:
                logger.warning(f"[IDEMPOTENCY-DB] Failed to record completion in DB: {e}")
                db.rollback()

        # 2. Update memory cache and unblock events
        async with self._lock:
            self._in_memory_cache[event_key] = response
            if event_key in self._in_progress:
                event = self._in_progress.pop(event_key)
                event.set()

    async def fail_processing(
        self,
        event_key: str,
        error_message: str,
        db: Optional[Session] = None,
        update_data: Optional[Dict[str, Any]] = None,
        bot_id: str = "rentkom_bot"
    ):
        """Marks processing as FAILED so lease can be retried."""
        if db is not None:
            try:
                update_id, _, _ = self.parse_update_id(update_data or {})
                db.execute(text("""
                    UPDATE public.telegram_inbox
                    SET status = 'FAILED'
                    WHERE bot_id = :bot_id AND update_id = :update_id;
                """), {"bot_id": bot_id, "update_id": update_id})
                db.commit()
            except Exception as e:
                db.rollback()

        async with self._lock:
            if event_key in self._in_progress:
                event = self._in_progress.pop(event_key)
                event.set()

    async def clear_cache(self):
        """Used for test isolation."""
        async with self._lock:
            self._in_memory_cache.clear()
            self._in_progress.clear()

# Global singleton
telegram_idempotency = TelegramIdempotencyManager()
