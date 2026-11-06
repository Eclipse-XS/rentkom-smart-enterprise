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
    check_public_rate_limit,
)
from core.schemas import (
    TelegramInboxGuardRequest,
    TelegramInboxGuardResponse,
    TelegramInboxCompleteRequest,
    TelegramMockDispatchRequest,
    TelegramMockDispatchResponse,
    TelegramRealSendRequest,
    TelegramModeRequest,
    TelegramModeResponse,
    TelegramModeControlRequest,
    TelegramModeControlResponse,
)
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

router = APIRouter()


def get_telegram_bot_mode(db: Optional[Session] = None) -> str:
    """Retrieves the authoritative active Telegram mode (LR2 or LR6) from PostgreSQL."""
    if db is not None:
        try:
            from core.models import SystemConfig
            row = db.query(SystemConfig).filter(SystemConfig.key == "telegram_bot_mode").first()
            if row and row.value in ("LR2", "LR6"):
                return row.value
        except Exception as e:
            logger.warning(f"Failed to query system_config for telegram_bot_mode: {e}")
    return os.getenv("DEFAULT_TELEGRAM_MODE", "LR2")


def set_telegram_bot_mode(mode: str, db: Session) -> tuple[str, bool]:
    """Persists the selected mode (LR2 or LR6) into PostgreSQL."""
    from core.models import SystemConfig
    mode_upper = mode.strip().upper()
    if mode_upper not in ("LR2", "LR6"):
        raise HTTPException(status_code=400, detail=f"Invalid mode '{mode}'. Must be 'LR2' or 'LR6'.")
    row = db.query(SystemConfig).filter(SystemConfig.key == "telegram_bot_mode").first()
    if row and row.value == mode_upper:
        return mode_upper, False
    if not row:
        row = SystemConfig(key="telegram_bot_mode", value=mode_upper)
        db.add(row)
    else:
        row.value = mode_upper
    db.commit()
    logger.info(f"[MODE] Telegram bot mode successfully updated to {mode_upper}")
    return mode_upper, True


LR2_REPLY_KEYBOARD = {
    "keyboard": [[
        {"text": "Процес купівлі/продажу"},
        {"text": "Послуги для орендодавців"},
    ]],
    "resize_keyboard": True,
}
LR6_REPLY_KEYBOARD_REMOVE = {"remove_keyboard": True}
MODE_SELECTOR_TEXT = "Оберіть режим роботи Telegram-бота:"
MODE_SELECTOR_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "Сценарний режим (LR2)", "callback_data": "mode:LR2"},
        {"text": "AI-консультант (LR6)", "callback_data": "mode:LR6"},
    ]]
}
LR3_NOTIFICATIONS_KEY = "lr3_telegram_notifications_enabled"
NOTIFICATIONS_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "Увімкнути сповіщення", "callback_data": "notifications:ON"},
        {"text": "Вимкнути сповіщення", "callback_data": "notifications:OFF"},
    ]]
}
ADMIN_KEYBOARD = {"inline_keyboard": [
    [{"text": "Режим бота", "callback_data": "admin:mode"}],
    [{"text": "Сповіщення", "callback_data": "admin:notifications"}],
    [{"text": "Ресурси", "callback_data": "admin:resources"}],
    [{"text": "Оновити стан", "callback_data": "admin:refresh"}],
]}
MODE_NOTIFICATION_TEXT = {
    "LR2": (
        "🔄 Режим переключено на ЛР2: Сценарний чат-бот агентства «Рентком».\n\n"
        "Сценарні кнопки активовано. Виберіть потрібний розділ меню нижче:"
    ),
    "LR6": (
        "🔄 Режим переключено на ЛР6: Інтелектуальний RAG AI-консультант агентства «Рентком» "
        "(база знань Qdrant + Ollama qwen3:4b-instruct).\n\n"
        "Сценарну клавіатуру вимкнено. Ви можете ставити будь-які запитання щодо купівлі, "
        "оренди, перевірки документів або правил агентства."
    ),
}


def get_mode_notification(mode: str) -> tuple[str, Dict[str, Any]]:
    return (
        MODE_NOTIFICATION_TEXT[mode],
        LR2_REPLY_KEYBOARD if mode == "LR2" else LR6_REPLY_KEYBOARD_REMOVE,
    )


def get_lr3_notifications_enabled(db: Session) -> bool:
    from core.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == LR3_NOTIFICATIONS_KEY).first()
    if not row:
        db.add(SystemConfig(key=LR3_NOTIFICATIONS_KEY, value="false"))
        db.commit()
        return False
    return row.value.strip().lower() in ("true", "1", "yes", "on")


def set_lr3_notifications_enabled(enabled: bool, db: Session) -> bool:
    from core.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == LR3_NOTIFICATIONS_KEY).first()
    value = "true" if enabled else "false"
    if row and row.value == value:
        return False
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=LR3_NOTIFICATIONS_KEY, value=value))
    db.commit()
    return True


def notifications_status_text(enabled: bool) -> str:
    return f"Сповіщення ЛР3: {'увімкнено' if enabled else 'вимкнено'}."


def admin_panel_text(db: Session) -> str:
    return (
        "Rentkom — Адміністрування\n\n"
        f"Поточний глобальний режим: {get_telegram_bot_mode(db)}\n"
        f"Сповіщення адміністратора: {'ON' if get_lr3_notifications_enabled(db) else 'OFF'}"
    )


def _safe_resource_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return value.strip()


def _is_local_resource(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def build_resources_menu(is_admin: bool) -> tuple[str, Dict[str, Any]]:
    rows = []
    unavailable = []
    public_resources = (
        ("Залишити відгук", settings.LR3_FEEDBACK_FORM_URL),
        ("Записатися на консультацію", settings.LR4_CONSULTATION_FORM_URL),
    )
    for label, configured_url in public_resources:
        url = _safe_resource_url(configured_url)
        if url:
            rows.append([{"text": label, "url": url}])
        else:
            unavailable.append(label)

    lines = ["Rentkom | Корисні посилання"]
    if unavailable:
        lines.append("Недоступно: " + ", ".join(unavailable) + ".")

    if is_admin:
        lines.extend(["", "Дані та аналітика"])
        data_resources = (
            ("Google Sheets: відгуки", settings.LR3_SHEET_URL),
            ("Google Sheets: заявки", settings.LR4_SHEET_URL),
        )
        missing_admin = []
        for label, configured_url in data_resources:
            url = _safe_resource_url(configured_url)
            if url:
                rows.append([{"text": label, "url": url}])
            else:
                missing_admin.append(label)
        if missing_admin:
            lines.append("Недоступно: " + ", ".join(missing_admin) + ".")

        lines.extend(["", "Інфраструктура"])
        infrastructure = (
            ("Grafana", settings.GRAFANA_URL),
            ("n8n", settings.N8N_URL),
            ("FastAPI Swagger", settings.FASTAPI_DOCS_URL),
            ("Qdrant Dashboard", settings.QDRANT_DASHBOARD_URL),
        )
        local_labels = []
        unavailable_infrastructure = []
        for label, configured_url in infrastructure:
            url = _safe_resource_url(configured_url)
            if not url:
                unavailable_infrastructure.append(label)
            elif _is_local_resource(url):
                local_labels.append(label)
            else:
                rows.append([{"text": label, "url": url}])
        if local_labels:
            lines.append("Локальний доступ: " + ", ".join(local_labels) + ".")
        if unavailable_infrastructure:
            lines.append("Не налаштовано: " + ", ".join(unavailable_infrastructure) + ".")

    return "\n".join(lines), {"inline_keyboard": rows}


async def send_mode_notification(chat_id: int, mode: str) -> Optional[int]:
    """Deliver the post-commit mode confirmation to exactly one configured chat."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    text, reply_markup = get_mode_notification(mode)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "reply_markup": reply_markup},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram API rejected notification (HTTP {response.status_code})")
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError("Telegram API did not accept the notification")
    return (body.get("result") or {}).get("message_id")


async def _telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
            json=payload,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram API {method} failed (HTTP {response.status_code})")
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API {method} returned ok=false")
    return body


def _is_authorized_telegram_admin(user_id: int, chat_id: int, chat_type: Optional[str] = None) -> bool:
    admin_user_id = getattr(settings, "TELEGRAM_ADMIN_USER_ID", None)
    admin_chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    return bool(
        admin_user_id
        and admin_chat_id
        and str(user_id) == str(admin_user_id)
        and str(chat_id) == str(admin_chat_id)
        and (chat_type is None or chat_type == "private")
    )


@router.post("/mode/telegram-control", response_model=TelegramModeControlResponse)
async def telegram_mode_control(
    req: TelegramModeControlRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """Execute a normalized /mode command or inline callback from the single n8n webhook."""
    internal_key = get_effective_internal_key()
    token = x_internal_token or _extract_bearer(authorization)
    if not (internal_key and token and constant_time_equals(token, internal_key)):
        raise HTTPException(status_code=403, detail="Forbidden: requires valid internal token")

    authorized = bool(
        req.chat_type == "private"
        and _is_authorized_telegram_admin(req.telegram_user_id, req.chat_id, req.chat_type)
    )
    callback_answered = False

    public_form_actions = {
        "SHOW_CONSULTATION_FORM": settings.LR4_CONSULTATION_FORM_URL,
        "SHOW_FEEDBACK_FORM": settings.LR3_FEEDBACK_FORM_URL,
    }
    if req.action in public_form_actions:
        text = public_form_actions[req.action]
        if not req.is_test:
            sent = await _telegram_api("sendMessage", {"chat_id": req.chat_id, "text": text})
            message_id = (sent.get("result") or {}).get("message_id")
        else:
            message_id = None
        return TelegramModeControlResponse(
            status="FORM_LINK_SENT", action=req.action, authorized=authorized,
            notification_message_id=message_id, response_text=text,
        )

    if req.callback_query_id and not req.is_test:
        answer_text = None if (authorized or req.action == "SHOW_RESOURCES") else "Недостатньо прав."
        await _telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": req.callback_query_id,
                **({"text": answer_text, "show_alert": True} if answer_text else {}),
            },
        )
        callback_answered = True

    if req.action == "SHOW_RESOURCES":
        text, reply_markup = build_resources_menu(authorized)
        if req.is_test:
            message_id = None
        else:
            sent = await _telegram_api("sendMessage", {
                "chat_id": req.chat_id, "text": text, "reply_markup": reply_markup,
            })
            message_id = (sent.get("result") or {}).get("message_id")
        return TelegramModeControlResponse(
            status="RESOURCES_SENT", action=req.action, authorized=authorized,
            callback_answered=callback_answered, notification_message_id=message_id,
            response_text=text, reply_markup=reply_markup,
        )

    if not authorized:
        return TelegramModeControlResponse(
            status="FORBIDDEN", action=req.action, authorized=False,
            callback_answered=callback_answered,
            response_text="Недостатньо прав.",
        )

    if req.action == "SHOW_SELECTOR":
        if req.is_test:
            message_id = None
        else:
            sent = await _telegram_api(
                "sendMessage",
                {"chat_id": req.chat_id, "text": MODE_SELECTOR_TEXT, "reply_markup": MODE_SELECTOR_KEYBOARD},
            )
            message_id = (sent.get("result") or {}).get("message_id")
        return TelegramModeControlResponse(
            status="SELECTOR_SENT", action=req.action, mode=get_telegram_bot_mode(db),
            authorized=True, notification_message_id=message_id,
            response_text=MODE_SELECTOR_TEXT,
        )

    if req.action in ("SHOW_ADMIN", "REFRESH_ADMIN"):
        text = admin_panel_text(db)
        if req.is_test:
            message_id = req.message_id
        elif req.action == "REFRESH_ADMIN" and req.message_id:
            await _telegram_api("editMessageText", {
                "chat_id": req.chat_id, "message_id": req.message_id,
                "text": text, "reply_markup": ADMIN_KEYBOARD,
            })
            message_id = req.message_id
        else:
            sent = await _telegram_api("sendMessage", {
                "chat_id": req.chat_id, "text": text, "reply_markup": ADMIN_KEYBOARD,
            })
            message_id = (sent.get("result") or {}).get("message_id")
        return TelegramModeControlResponse(
            status="ADMIN_PANEL_SENT", action=req.action, mode=get_telegram_bot_mode(db),
            authorized=True, callback_answered=callback_answered,
            notification_message_id=message_id, response_text=text,
        )

    if req.action == "DEPRECATED_COMMAND":
        text = "Команди /mode lr2 та /mode lr6 застаріли. Використовуйте /mode."
        if not req.is_test:
            sent = await _telegram_api("sendMessage", {"chat_id": req.chat_id, "text": text})
            message_id = (sent.get("result") or {}).get("message_id")
        else:
            message_id = None
        return TelegramModeControlResponse(
            status="DEPRECATED", action=req.action, mode=get_telegram_bot_mode(db),
            authorized=True, notification_message_id=message_id, response_text=text,
        )

    if req.action == "SHOW_NOTIFICATIONS":
        enabled = get_lr3_notifications_enabled(db)
        text = notifications_status_text(enabled)
        if not req.is_test:
            sent = await _telegram_api(
                "sendMessage",
                {"chat_id": req.chat_id, "text": text, "reply_markup": NOTIFICATIONS_KEYBOARD},
            )
            message_id = (sent.get("result") or {}).get("message_id")
        else:
            message_id = None
        return TelegramModeControlResponse(
            status="NOTIFICATIONS_STATUS_SENT", action=req.action, authorized=True,
            notification_message_id=message_id, response_text=text,
        )

    if req.action == "SET_NOTIFICATIONS" and req.callback_data in ("notifications:ON", "notifications:OFF"):
        enabled = req.callback_data.endswith(":ON")
        changed = set_lr3_notifications_enabled(enabled, db)
        text = notifications_status_text(enabled)
        if not req.is_test:
            if not req.message_id:
                raise HTTPException(status_code=400, detail="Notification callback is missing message_id")
            if changed:
                await _telegram_api(
                    "editMessageText",
                    {
                        "chat_id": req.chat_id,
                        "message_id": req.message_id,
                        "text": text,
                        "reply_markup": NOTIFICATIONS_KEYBOARD,
                    },
                )
            message_id = req.message_id
        else:
            message_id = None
        return TelegramModeControlResponse(
            status="SUCCESS" if changed else "UNCHANGED", action=req.action,
            changed=changed, authorized=True, callback_answered=callback_answered,
            notification_message_id=message_id, response_text=text,
        )

    if req.action != "SELECT_MODE" or req.callback_data not in ("mode:LR2", "mode:LR6"):
        raise HTTPException(status_code=400, detail="Invalid Telegram mode control action")

    target_mode = req.callback_data.split(":", 1)[1]
    updated, changed = set_telegram_bot_mode(target_mode, db)
    if not changed:
        text = f"Режим {updated} вже активний. Зміни не потрібні."
        if not req.is_test:
            sent = await _telegram_api("sendMessage", {"chat_id": req.chat_id, "text": text})
            message_id = (sent.get("result") or {}).get("message_id")
        else:
            message_id = None
        return TelegramModeControlResponse(
            status="UNCHANGED", action=req.action, mode=updated, changed=False,
            authorized=True, callback_answered=callback_answered,
            notification_message_id=message_id, response_text=text,
        )

    text, reply_markup = get_mode_notification(updated)
    if req.is_test:
        message_id = None
    else:
        try:
            sent = await _telegram_api(
                "sendMessage",
                {"chat_id": req.chat_id, "text": text, "reply_markup": reply_markup},
            )
            message_id = (sent.get("result") or {}).get("message_id")
        except Exception as exc:
            logger.error("[MODE] Persisted %s but callback notification failed: %s", updated, exc)
            return TelegramModeControlResponse(
                status="PARTIAL_SUCCESS", action=req.action, mode=updated, changed=True,
                authorized=True, callback_answered=callback_answered, response_text=str(exc),
            )
    return TelegramModeControlResponse(
        status="SUCCESS", action=req.action, mode=updated, changed=True,
        authorized=True, callback_answered=callback_answered,
        notification_message_id=message_id, response_text=text,
    )


@router.get("/lr3-notifications")
def get_lr3_notifications(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    internal_key = get_effective_internal_key()
    token = x_internal_token or _extract_bearer(authorization)
    if not (internal_key and token and constant_time_equals(token, internal_key)):
        raise HTTPException(status_code=403, detail="Forbidden: requires valid internal token")
    return {"enabled": get_lr3_notifications_enabled(db)}


@router.get("/mode", response_model=TelegramModeResponse)
def get_mode_endpoint(db: Session = Depends(get_db)):
    """Retrieves the active Telegram operating mode (LR2 or LR6)."""
    mode = get_telegram_bot_mode(db)
    return TelegramModeResponse(mode=mode, status="SUCCESS")


@router.post("/mode", response_model=TelegramModeResponse)
async def set_mode_endpoint(
    req: TelegramModeRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db)
):
    """Explicitly switches the active Telegram bot operating mode between LR2 and LR6."""
    internal_key = get_effective_internal_key()
    admin_key = getattr(settings, "ADMIN_API_KEY", None)
    bearer = _extract_bearer(authorization)
    token = x_internal_token or x_admin_key or bearer
    is_valid_internal = bool(internal_key and token and constant_time_equals(token, internal_key))
    is_valid_admin = bool(admin_key and token and constant_time_equals(token, admin_key))
    if not (is_valid_internal or is_valid_admin):
        raise HTTPException(status_code=403, detail="Forbidden: requires valid administrator or internal token")
    updated, changed = set_telegram_bot_mode(req.mode, db)
    if not changed:
        return TelegramModeResponse(
            mode=updated, status="UNCHANGED", changed=False,
            notification_status="SKIPPED_ALREADY_ACTIVE",
        )

    admin_chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    if not admin_chat_id:
        return TelegramModeResponse(
            mode=updated, status="PARTIAL_SUCCESS", updated_at=str(time.time()),
            notification_status="FAILED",
            notification_error="TELEGRAM_ADMIN_CHAT_ID is not configured",
        )
    try:
        message_id = await send_mode_notification(int(admin_chat_id), updated)
    except Exception as exc:
        logger.error("[MODE] Persisted %s but notification failed: %s", updated, exc)
        return TelegramModeResponse(
            mode=updated, status="PARTIAL_SUCCESS", updated_at=str(time.time()),
            notification_status="FAILED", notification_error=str(exc),
        )
    return TelegramModeResponse(
        mode=updated, status="SUCCESS", updated_at=str(time.time()),
        notification_status="DELIVERED", notification_message_id=message_id,
    )



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


START_MENU_TEXT = """Вітаємо у навчальному чат-боті агентства нерухомості «Рентком»!
Оберіть потрібний розділ:"""

BUY_PROCESS_TEXT = """Процес купівлі/продажу:

1. Звернення та визначення потреб: бюджет, район, тип об'єкта, площа, кількість кімнат і строки.
2. Підбір об'єктів за визначеними критеріями.
3. Узгодження та проведення переглядів.
4. Вибір відповідного об'єкта.
5. Перевірка необхідних документів компетентним фахівцем.
6. Переговори щодо ціни, строків і умов розрахунку.
7. Підготовка угоди: за потреби завдаток, договір, нотаріальне оформлення та розрахунок.
8. Передача об'єкта і ключів, завершення угоди."""

RENT_SERVICES_TEXT = """Послуги для орендодавців у навчальному сценарії:

• консультація та визначення умов здачі;
• підготовка і презентація об'єкта;
• розміщення та просування пропозиції;
• пошук потенційного орендаря і первинний відбір кандидатів;
• організація переглядів та погодження умов;
• підготовка інформації і документів для оформлення;
• супровід укладення договору та передачі об'єкта;
• подальша комунікація й координація питань у межах управління об'єктом."""

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

    telegram_idempotency.start_renewal(event_key, update_dict)

    message = update.message or {}
    text_content = (message.get("text") or "").strip()

    try:
        active_mode = get_telegram_bot_mode(db)

        # Administrator mode selector is intercepted before LR2/LR6 business routing.
        chat_id_val = message.get("chat", {}).get("id") or 0
        chat_type_val = message.get("chat", {}).get("type")
        user_id_val = message.get("from", {}).get("id") or 0
        is_admin_update = _is_authorized_telegram_admin(user_id_val, chat_id_val, chat_type_val)
        admin_command = text_content.lower()
        if admin_command in ("/admin", "/mode", "/notifications", "/resources"):
            if admin_command == "/resources":
                text, markup = build_resources_menu(is_admin_update)
                res = TelegramResponseMock(route="Resources", response_text=text, reply_markup=markup)
            elif not is_admin_update:
                res = TelegramResponseMock(route="Access Denied", response_text="Недостатньо прав.")
            elif admin_command == "/admin":
                res = TelegramResponseMock(route="Admin Panel", response_text=admin_panel_text(db), reply_markup=ADMIN_KEYBOARD)
            elif admin_command == "/mode":
                res = TelegramResponseMock(
                    route="Admin Mode Selector",
                    response_text=MODE_SELECTOR_TEXT,
                    reply_markup=MODE_SELECTOR_KEYBOARD,
                )
            elif admin_command == "/notifications":
                res = TelegramResponseMock(
                    route="Admin Notifications", response_text=notifications_status_text(get_lr3_notifications_enabled(db)),
                    reply_markup=NOTIFICATIONS_KEYBOARD,
                )
        elif admin_command.startswith("/mode"):
            res = TelegramResponseMock(
                route="Deprecated Admin Command" if is_admin_update else "Access Denied",
                response_text="Команди /mode lr2 та /mode lr6 застаріли. Використовуйте /mode." if is_admin_update else "Недостатньо прав.",
            )
        elif active_mode == "LR2":
            # PURE LR2 EXECUTION MODE
            if text_content.lower() == "/start":
                res = TelegramResponseMock(
                    route="Start Menu",
                    response_text=START_MENU_TEXT,
                    reply_markup=LR2_REPLY_KEYBOARD
                )
            elif any(k in text_content.lower() for k in ["процес купівлі", "купівл"]):
                res = TelegramResponseMock(
                    route="Купівля/Продаж",
                    response_text=BUY_PROCESS_TEXT,
                )
            elif any(k in text_content.lower() for k in ["орендодавц", "послуги для орендодавців"]):
                res = TelegramResponseMock(
                    route="Орендодавцям",
                    response_text=RENT_SERVICES_TEXT,
                )
            else:
                # LR2 RULE-BASED FALLBACK: Strictly deterministic, NEVER invokes RAG!
                res = TelegramResponseMock(
                    route="LR2 Fallback",
                    response_text=(
                        "Не вдалося розпізнати запит.\n\n"
                        "Я можу надати інформацію про:\n"
                        "• процес купівлі/продажу;\n"
                        "• послуги для орендодавців.\n\n"
                        "Будь ласка, скористайтеся кнопками меню нижче."
                    ),
                    reply_markup=LR2_REPLY_KEYBOARD
                )

        else:
            # PURE LR6 EXECUTION MODE (RAG AI Agent)
            if text_content.lower() == "/start":
                res = TelegramResponseMock(
                    route="LR6 AI Greeting",
                    response_text=(
                        "Вітаю! Я інформаційний помічник агентства нерухомості «Рентком» (м. Львів).\n\n"
                        "Я можу відповісти за матеріалами корпоративної бази знань про:\n"
                        "• купівлю житла та 6-етапну перевірку документів;\n"
                        "• порядок підписання договорів та нотаріальне посвідчення;\n"
                        "• умови типового договору оренди;\n"
                        "• послуги агентства та правила конфіденційності.\n\n"
                        "Поставте будь-яке запитання з нерухомості у вільній формі."
                    ),
                    reply_markup=LR6_REPLY_KEYBOARD_REMOVE,
                )
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
async def telegram_webhook_mock(
    update: TelegramUpdateMock,
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(check_public_rate_limit),
):
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
    db: Session = Depends(get_db),
    _rate_limit: None = Depends(check_public_rate_limit),
):
    """Production ingress endpoint for Unified Telegram Router with webhook secret verification."""
    tg_secret = settings.TELEGRAM_SECRET_TOKEN
    if not settings.is_safe_demo_or_dev or tg_secret:
        if not tg_secret:
            raise HTTPException(
                status_code=500,
                detail="Server security misconfiguration: TELEGRAM_SECRET_TOKEN is not configured."
            )
        if not x_telegram_bot_api_secret_token:
            internal_key = get_effective_internal_key()
            bearer = _extract_bearer(authorization)
            internal_candidate = x_internal_token or bearer
            if internal_key and internal_candidate and constant_time_equals(internal_candidate, internal_key):
                pass
            else:
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: missing X-Telegram-Bot-Api-Secret-Token or internal service token."
                )
        elif not constant_time_equals(x_telegram_bot_api_secret_token, tg_secret):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: invalid Telegram webhook secret token."
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

    # 2. Normalize messages and callback queries before any LR2/LR6 routing.
    raw_body = guard_req.body or {}
    callback = raw_body.get("callback_query") or {}
    msg = guard_req.message or raw_body.get("message") or callback.get("message") or {}
    chat_id = guard_req.chat_id or (msg.get("chat") or {}).get("id") or 12345
    text_content = guard_req.text or msg.get("text") or raw_body.get("text") or ""
    telegram_user_id = (callback.get("from") or {}).get("id") or (msg.get("from") or {}).get("id")
    callback_query_id = callback.get("id")
    callback_data = callback.get("data")
    message_id = msg.get("message_id")
    chat_type = (msg.get("chat") or {}).get("type")
    normalized_text = text_content.strip().lower()
    if callback_data in ("mode:LR2", "mode:LR6"):
        mode_action = "SELECT_MODE"
    elif callback_data in ("notifications:ON", "notifications:OFF"):
        mode_action = "SET_NOTIFICATIONS"
    elif normalized_text == "/mode":
        mode_action = "SHOW_SELECTOR"
    elif normalized_text == "/admin":
        mode_action = "SHOW_ADMIN"
    elif callback_data == "admin:refresh":
        mode_action = "REFRESH_ADMIN"
    elif callback_data == "admin:mode":
        mode_action = "SHOW_SELECTOR"
    elif callback_data == "admin:notifications":
        mode_action = "SHOW_NOTIFICATIONS"
    elif callback_data == "admin:resources":
        mode_action = "SHOW_RESOURCES"
    elif normalized_text == "/notifications":
        mode_action = "SHOW_NOTIFICATIONS"
    elif normalized_text == "/resources":
        mode_action = "SHOW_RESOURCES"
    elif normalized_text == "/consultation":
        mode_action = "SHOW_CONSULTATION_FORM"
    elif normalized_text == "/feedback":
        mode_action = "SHOW_FEEDBACK_FORM"
    elif normalized_text.startswith("/mode "):
        mode_action = "DEPRECATED_COMMAND"
    else:
        mode_action = "NONE"

    # 3. Form standard update dict for idempotency manager
    update_data = {
        "update_id": guard_req.update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text_content,
            "from": {"id": telegram_user_id},
        },
        "callback_query": callback or None,
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

    current_mode = get_telegram_bot_mode(db)
    telegram_idempotency.start_renewal(event_key, update_data, guard_req.bot_id)
    logger.info(f"[TELEGRAM-GUARD] Update {update_id} acquired lease. Action: EXECUTE. Active Mode: {current_mode}")
    return TelegramInboxGuardResponse(
        action="EXECUTE",
        status="NEW",
        update_id=update_id,
        chat_id=clean_chat_id,
        text=clean_text,
        is_test=guard_req.is_test,
        cached_response=None,
        mode=current_mode,
        mode_action=mode_action,
        telegram_user_id=telegram_user_id,
        callback_query_id=callback_query_id,
        callback_data=callback_data,
        message_id=message_id,
        chat_type=chat_type,
        owner_token=telegram_idempotency.get_owner_token(event_key),
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
    if not req.owner_token:
        raise HTTPException(status_code=409, detail="Missing processing owner token.")
    cached = CachedResponse(
        route=req.route,
        response_text=req.response_text,
        reply_markup=req.reply_markup,
        sources=req.sources,
        confidence_score=req.confidence_score,
        processed_at=time.time(),
        is_duplicate=False
    )
    completed = await telegram_idempotency.finish_processing(
        event_key,
        cached,
        db=db,
        update_data={"update_id": req.update_id, "message": {"chat": {"id": 0}, "text": ""}},
        bot_id=req.bot_id,
        owner_token=req.owner_token,
    )
    if not completed:
        raise HTTPException(status_code=409, detail="Processing lease ownership was lost.")
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
            "text": dispatch_req.text,
            "reply_markup": dispatch_req.reply_markup
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
