"""Send a best-effort, role-scoped Rentkom lifecycle notification."""

import json
import os
import sys
import urllib.request
from pathlib import Path


MESSAGES = {
    "startup": "<b>🟢 Rentkom запущено</b>\n\nУсі основні сервіси доступні.\nTelegram-бот готовий до роботи.",
    "shutdown": "<b>🔴 Rentkom зупинено</b>\n\nСервер вимкнено.\nОбробка нових запитів тимчасово недоступна.",
}


def _state_path() -> Path:
    return Path(__file__).resolve().parents[1] / "runtime" / "lifecycle_state.json"


def _read_state() -> str:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("state", "unknown")
    except (OSError, ValueError, TypeError):
        return "unknown"


def _write_state(state: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"state": state}), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    values = dict(os.environ)
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = values.get("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if event not in MESSAGES:
        return 0
    target_state = "running" if event == "startup" else "stopped"
    if _read_state() == target_state:
        return 0
    _write_state(target_state)
    if not token or not chat_id:
        return 0
    payload = json.dumps({
        "chat_id": chat_id,
        "text": MESSAGES[event],
        "parse_mode": "HTML",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        return 0 if body.get("ok") else 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
