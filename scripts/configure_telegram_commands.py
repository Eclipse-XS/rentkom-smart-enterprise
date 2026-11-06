"""Configure role-scoped Telegram bot commands without exposing credentials."""

import json
import os
import urllib.request


USER_COMMANDS = [
    {"command": "start", "description": "Почати роботу"},
    {"command": "feedback", "description": "Залишити відгук"},
    {"command": "consultation", "description": "Записатися на консультацію"},
    {"command": "resources", "description": "Корисні посилання"},
]

ADMIN_COMMANDS = USER_COMMANDS + [
    {"command": "admin", "description": "Панель адміністратора"},
    {"command": "mode", "description": "Режим бота"},
    {"command": "notifications", "description": "Сповіщення"},
]


def telegram_api(token: str, method: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}")
    return result


def configure_commands(token: str, admin_chat_id: int) -> None:
    broad_scopes = (
        {"type": "all_private_chats"},
        {"type": "all_group_chats"},
        {"type": "all_chat_administrators"},
    )
    relevant_scopes = broad_scopes + (
        {"type": "default"},
        {"type": "chat", "chat_id": admin_chat_id},
    )
    for scope in relevant_scopes:
        telegram_api(token, "deleteMyCommands", {"scope": scope})
        for language_code in ("uk", "ru", "en"):
            telegram_api(token, "deleteMyCommands", {
                "scope": scope, "language_code": language_code,
            })

    telegram_api(token, "setMyCommands", {
        "scope": {"type": "default"}, "commands": USER_COMMANDS,
    })
    telegram_api(token, "setMyCommands", {
        "scope": {"type": "chat", "chat_id": admin_chat_id},
        "commands": ADMIN_COMMANDS,
    })

    default = telegram_api(token, "getMyCommands", {"scope": {"type": "default"}})["result"]
    admin = telegram_api(token, "getMyCommands", {
        "scope": {"type": "chat", "chat_id": admin_chat_id},
    })["result"]
    if default != USER_COMMANDS or admin != ADMIN_COMMANDS:
        raise RuntimeError("Telegram command scope verification failed")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not admin_chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID are required")
    configure_commands(token, int(admin_chat_id))
    print("Telegram USER and ADMIN command scopes configured and verified.")


if __name__ == "__main__":
    main()
