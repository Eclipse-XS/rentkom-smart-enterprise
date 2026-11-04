import time
import hashlib
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from core.database import get_db
from core.services.rag_service import query_rag
from core.services.telegram_idempotency import telegram_idempotency, CachedResponse
import httpx
from core.config import settings
from core.security import (
    constant_time_equals,
    get_effective_internal_key,
    get_effective_test_secret,
    _extract_bearer,
)
from core.schemas import (
    TelegramInboxGuardRequest,
    TelegramInboxGuardResponse,
    TelegramInboxCompleteRequest,
    TelegramMockDispatchRequest,
    TelegramMockDispatchResponse,
    TelegramRealSendRequest
)

logger = logging.getLogger(__name__)

router = APIRouter()


class TelegramUpdateMock(BaseModel):
    update_id: Optional[int] = None
    message: Dict[str, Any]


class TelegramResponseMock(BaseModel):
    route: str
    response_text: str
    reply_markup: Optional[Dict[str, Any]] = None
    sources: Optional[list] = None
    confidence_score: Optional[float] = None
    is_duplicate: bool = False


START_MENU_TEXT = """Вітаємо у чат-боті агентства нерухомості «Рентком» (м. Львів)!
Оберіть потрібний розділ меню або введіть будь-яке запитання щодо нерухомості."""

BUY_PROCESS_TEXT = """🏢 Процес купівлі/продажу нерухомості у «Рентком»:
1. Первинна консультація та формування вимог.
2. Підбір об'єктів та організація переглядів.
3. 6-етапна юридична перевірка правовстановлюючих документів.
4. Укладення попереднього договору та нотаріальне посвідчення угоди.
5. Акт приймання-передачі та розрахунок."""

RENT_SERVICES_TEXT = """🔑 Послуги для орендодавців:
- Професійна фотозйомка та реклама об'єкта.
- Перевірка платоспроможності орендарів.
- Підготовка типового договору оренди.
- Супровід передачі об'єкта та складання опису майна."""

FALLBACK_TEXT = "Вибачте, сервіс консультацій тимчасово недоступний. Будь ласка, зверніться до нашого чергового рієлтора за телефоном +38 (032) 240-00-00."


async def process_telegram_update(update: TelegramUpdateMock, db: Optional[Session] = None) -> TelegramResponseMock:
    """
    Central, idempotent message router for Telegram updates (LR2 + LR6).
    Enforces PostgreSQL-backed Inbox deduplication before any side effects or routing.
    """
    update_dict = update.model_dump()
    event_key = telegram_idempotency.get_event_key(update_dict)

    # 1. Acquire lease or retrieve cached completed response
    is_new, cached = await telegram_idempotency.acquire_or_get_cached(
        event_key, db=db, update_data=update_dict
    )

    if not is_new and cached:
        logger.info(f"[IDEMPOTENCY] Returning cached response for event {event_key}")
        return TelegramResponseMock(
            route=cached.route,
            response_text=cached.response_text,
            reply_markup=cached.reply_markup,
            sources=cached.sources,
            confidence_score=cached.confidence_score,
            is_duplicate=True
        )

    message = update.message or {}
    text_content = (message.get("text") or "").strip()

    try:
        # 1. Start Menu Route
        if text_content.lower() == "/start":
            res = TelegramResponseMock(
                route="Start Menu",
                response_text=START_MENU_TEXT,
                reply_markup={
                    "keyboard": [
                        [{"text": "🏢 Процес купівлі/продажу"}, {"text": "🔑 Послуги для орендодавців"}],
                        [{"text": "📋 Залишити заявку"}, {"text": "📞 Контакти"}]
                    ],
                    "resize_keyboard": True
                }
            )

        # 2. Buying Process Route
        elif "процес купівлі" in text_content.lower() or "купівл" in text_content.lower():
            res = TelegramResponseMock(
                route="Купівля/Продаж",
                response_text=BUY_PROCESS_TEXT,
                reply_markup={"inline_keyboard": [[{"text": "Замовити консультацію", "callback_data": "consult_buy"}]]}
            )

        # 3. Landlord Services Route
        elif "орендодавц" in text_content.lower() or "оренд" in text_content.lower():
            res = TelegramResponseMock(
                route="Орендодавцям",
                response_text=RENT_SERVICES_TEXT,
                reply_markup={"inline_keyboard": [[{"text": "Здати об'єкт", "callback_data": "rent_out"}]]}
            )

        # 4. AI RAG Query Route (LR6 Integration)
        else:
            try:
                rag_out = await query_rag(text_content)
                res = TelegramResponseMock(
                    route="AI RAG Agent",
                    response_text=rag_out.answer,
                    sources=rag_out.sources,
                    confidence_score=rag_out.confidence_score
                )
            except Exception as rag_err:
                logger.warning(f"RAG Core unreachable or timed out: {rag_err}")
                res = TelegramResponseMock(
                    route="Fallback Service",
                    response_text=FALLBACK_TEXT,
                    confidence_score=0.0
                )

        # Persist completion state in DB Inbox & memory
        await telegram_idempotency.finish_processing(
            event_key,
            CachedResponse(
                route=res.route,
                response_text=res.response_text,
                reply_markup=res.reply_markup,
                sources=res.sources,
                confidence_score=res.confidence_score,
                processed_at=time.time(),
                is_duplicate=False
            ),
            db=db,
            update_data=update_dict
        )
        return res

    except Exception as e:
        logger.error(f"Unexpected error in process_telegram_update: {e}")
        await telegram_idempotency.fail_processing(
            event_key, str(e), db=db, update_data=update_dict
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook-mock", response_model=TelegramResponseMock)
async def telegram_webhook_mock(update: TelegramUpdateMock, db: Session = Depends(get_db)):
    """Mock testing endpoint for Unified Telegram Router (Demo/Test environment only)."""
    if not settings.is_test_webhook_permitted:
        raise HTTPException(
            status_code=403,
            detail="Mock Telegram webhook is disabled in production mode or when ALLOW_TEST_WEBHOOK is false."
        )
    return await process_telegram_update(update, db=db)


@router.post("/webhook", response_model=TelegramResponseMock)
async def telegram_webhook(
    update: TelegramUpdateMock,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
):
    """Production ingress endpoint for Unified Telegram Router with webhook secret verification."""
    if not settings.is_safe_demo_or_dev:
        tg_secret = settings.TELEGRAM_SECRET_TOKEN
        internal_key = get_effective_internal_key()
        bearer = _extract_bearer(authorization)
        internal_candidate = x_internal_token or bearer

        is_valid_tg = bool(tg_secret and constant_time_equals(x_telegram_bot_api_secret_token, tg_secret))
        is_valid_internal = bool(internal_key and constant_time_equals(internal_candidate, internal_key))

        if not (tg_secret or internal_key):
            raise HTTPException(
                status_code=500,
                detail="Server security misconfiguration: TELEGRAM_SECRET_TOKEN or INTERNAL_API_KEY not configured."
            )
        if not (is_valid_tg or is_valid_internal):
            if not (x_telegram_bot_api_secret_token or internal_candidate):
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: missing X-Telegram-Bot-Api-Secret-Token or internal service token."
                )
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid Telegram webhook secret token."
            )
    elif settings.TELEGRAM_SECRET_TOKEN and x_telegram_bot_api_secret_token is not None:
        if not constant_time_equals(x_telegram_bot_api_secret_token, settings.TELEGRAM_SECRET_TOKEN):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid X-Telegram-Bot-Api-Secret-Token."
            )

    return await process_telegram_update(update, db=db)


@router.post("/inbox/guard", response_model=TelegramInboxGuardResponse)
async def acquire_telegram_inbox_guard(
    guard_req: TelegramInboxGuardRequest,
    x_test_token: Optional[str] = Header(None, alias="X-Test-Token"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
):
    """
    Authoritative single-point gatekeeper for all Telegram updates (LR2 + LR6).
    Enforces deduplication in PostgreSQL public.telegram_inbox before any downstream n8n execution.
    Protects test webhooks against unauthorized execution or production abuse.
    Supports trusted internal service authentication in connected/production mode.
    """
    test_secret = get_effective_test_secret()
    internal_key = get_effective_internal_key()

    if guard_req.is_test:
        if not settings.is_test_webhook_permitted:
            raise HTTPException(
                status_code=403,
                detail="Test webhook ingress is disabled in production/connected mode or unpermitted environment."
            )
        if not test_secret:
            raise HTTPException(
                status_code=500,
                detail="Server security misconfiguration: TEST_WEBHOOK_SECRET is not configured."
            )
        if not x_test_token:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: missing X-Test-Token HTTP header."
            )
        if not constant_time_equals(x_test_token, test_secret):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid X-Test-Token HTTP header."
            )
    else:
        if not internal_key:
            raise HTTPException(
                status_code=500,
                detail="Server security misconfiguration: INTERNAL_API_KEY is not configured."
            )
        bearer = _extract_bearer(authorization)
        token = x_internal_token or bearer
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: missing internal service authorization."
            )
        if not constant_time_equals(token, internal_key):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid internal service authorization."
            )

    # 2. Extract message parameters
    msg = guard_req.message or (guard_req.body.get("message") if guard_req.body else None) or {}
    chat_id = guard_req.chat_id or (msg.get("chat") or {}).get("id") or 12345
    text_content = guard_req.text or msg.get("text") or (guard_req.body.get("text") if guard_req.body else "") or ""

    # 3. Form standard update dict for idempotency manager
    update_data = {
        "update_id": guard_req.update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text_content
        }
    }

    update_id, clean_chat_id, clean_text = telegram_idempotency.parse_update_id(update_data)
    event_key = f"tg_update:{update_id}"

    # 4. Acquire lease or retrieve cached completed response
    is_new, cached = await telegram_idempotency.acquire_or_get_cached(
        event_key, db=db, update_data=update_data, bot_id=guard_req.bot_id
    )

    if not is_new:
        logger.info(f"[TELEGRAM-GUARD] Update {update_id} already exists or is processing. Action: DROP_DUPLICATE")
        return TelegramInboxGuardResponse(
            action="DROP_DUPLICATE",
            status="DUPLICATE",
            update_id=update_id,
            chat_id=clean_chat_id,
            text=clean_text,
            is_test=guard_req.is_test,
            cached_response=cached.model_dump() if cached else None
        )

    logger.info(f"[TELEGRAM-GUARD] Update {update_id} acquired lease. Action: EXECUTE")
    return TelegramInboxGuardResponse(
        action="EXECUTE",
        status="NEW",
        update_id=update_id,
        chat_id=clean_chat_id,
        text=clean_text,
        is_test=guard_req.is_test,
        cached_response=None
    )


@router.post("/inbox/complete")
async def complete_telegram_inbox(
    req: TelegramInboxCompleteRequest,
    x_test_token: Optional[str] = Header(None, alias="X-Test-Token"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
):
    """
    Marks the Telegram update as COMPLETED in public.telegram_inbox and records response payload.
    Enforces internal/test authorization in connected/production mode.
    """
    test_secret = get_effective_test_secret()
    internal_key = get_effective_internal_key()
    bearer = _extract_bearer(authorization)
    internal_token = x_internal_token or bearer

    if not settings.is_safe_demo_or_dev:
        if not internal_key:
            raise HTTPException(
                status_code=500,
                detail="Server security misconfiguration: INTERNAL_API_KEY is not configured."
            )
        if not internal_token:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: missing internal service authorization."
            )
        if not constant_time_equals(internal_token, internal_key):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid internal service authorization."
            )
    else:
        if not (x_test_token or internal_token):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: missing X-Test-Token or X-Internal-Token HTTP header."
            )
        if not (
            (x_test_token and test_secret and constant_time_equals(x_test_token, test_secret))
            or (internal_token and internal_key and constant_time_equals(internal_token, internal_key))
        ):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid token provided to /inbox/complete."
            )

    event_key = f"tg_update:{req.update_id}"
    cached = CachedResponse(
        route=req.route,
        response_text=req.response_text,
        reply_markup=req.reply_markup,
        sources=req.sources,
        confidence_score=req.confidence_score,
        processed_at=time.time(),
        is_duplicate=False
    )
    await telegram_idempotency.finish_processing(
        event_key,
        cached,
        db=db,
        update_data={"update_id": req.update_id, "message": {"chat": {"id": 0}, "text": ""}},
        bot_id=req.bot_id
    )
    return {"status": "SUCCESS", "update_id": req.update_id}


@router.post("/mock-dispatcher", response_model=TelegramMockDispatchResponse)
@router.post("/dispatch-mock", response_model=TelegramMockDispatchResponse, include_in_schema=False)
async def dispatch_telegram_mock(
    request: Request,
    dispatch_req: TelegramMockDispatchRequest,
    x_test_token: Optional[str] = Header(None, alias="X-Test-Token"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    if not settings.is_test_webhook_permitted:
        raise HTTPException(
            status_code=403,
            detail="Mock Telegram Dispatcher is disabled in production mode or when ALLOW_TEST_WEBHOOK is false."
        )
    bearer = _extract_bearer(authorization)
    internal_token = x_internal_token or bearer

    test_secret = get_effective_test_secret()
    internal_key = get_effective_internal_key()

    is_valid_test = bool(x_test_token and test_secret and constant_time_equals(x_test_token, test_secret))
    is_valid_internal = bool(internal_token and internal_key and constant_time_equals(internal_token, internal_key))

    if not (x_test_token or internal_token):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: missing X-Test-Token or X-Internal-Token HTTP header."
        )
    if not (is_valid_test or is_valid_internal):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid X-Test-Token or X-Internal-Token HTTP header."
        )
    logger.info(f"[TELEGRAM-DISPATCHER] Dispatched mock message to chat {dispatch_req.chat_id} (text len={len(dispatch_req.text)})")

    # Deterministic SHA-256 offset for message ID (no process-randomized hash)
    msg_id_offset = int(hashlib.sha256(dispatch_req.text.encode("utf-8")).hexdigest()[:6], 16) % 900
    return TelegramMockDispatchResponse(
        ok=True,
        result={
            "message_id": 99900 + msg_id_offset,
            "date": int(time.time()),
            "chat": {
                "id": dispatch_req.chat_id,
                "type": "private",
                "first_name": "RentkomDemoUser"
            },
            "text": dispatch_req.text
        },
        delivery_mode="INTEGRATION_TESTED"
    )


@router.post("/send-message")
async def send_telegram_real_message(
    send_req: TelegramRealSendRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization")
):
    """
    Authoritative production Telegram message dispatcher for Connected Mode.
    Security:
      - Test Webhooks are strictly forbidden from routing to real Telegram (HTTP 403).
      - Requires trusted internal service authentication (X-Internal-Token or Bearer token).
      - Requires real TELEGRAM_BOT_TOKEN credentials configured in environment.
    """
    # 1. Enforce separation: test webhook traffic can NEVER route to real Telegram
    if send_req.is_test:
        raise HTTPException(
            status_code=403,
            detail="Test webhook ingress cannot route or dispatch messages to real Telegram."
        )

    # 2. Authenticate internal caller
    internal_key = get_effective_internal_key()
    if not internal_key:
        raise HTTPException(
            status_code=500,
            detail="Server security misconfiguration: INTERNAL_API_KEY is not configured."
        )
    bearer = _extract_bearer(authorization)
    token = x_internal_token or bearer
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: missing internal service authorization."
        )
    if not constant_time_equals(token, internal_key):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid internal service authorization."
        )

    # 3. Verify real bot credentials
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(
            status_code=422,
            detail="Telegram Bot credentials not configured (TELEGRAM_BOT_TOKEN is missing)."
        )

    # 4. Dispatch to official Telegram Bot API
    payload = {
        "chat_id": send_req.chat_id,
        "text": send_req.text
    }
    if send_req.reply_markup:
        payload["reply_markup"] = send_req.reply_markup
    if send_req.parse_mode:
        payload["parse_mode"] = send_req.parse_mode

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload
            )
            if resp.status_code != 200:
                logger.error(f"[TELEGRAM REAL] Dispatch failed ({resp.status_code})")
                raise HTTPException(
                    status_code=502,
                    detail=f"Upstream Telegram API error (HTTP {resp.status_code})"
                )
            return {
                "ok": True,
                "result": resp.json().get("result"),
                "mode": "LIVE_TELEGRAM",
                "delivery_mode": "PROVIDER_ACCEPTED"
            }
    except httpx.RequestError as exc:
        logger.error(f"[TELEGRAM REAL] Network error during dispatch: {type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail=f"Bad Gateway: Telegram API transport failure ({type(exc).__name__})"
        )
