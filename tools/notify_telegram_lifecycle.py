"""Send a best-effort, role-scoped Rentkom lifecycle notification."""

import json
import os
import sys
import urllib.request
from pathlib import Path


MESSAGES = {
    "startup": "✅ <b>Rentkom запущено</b>\n\nСервіси готові до роботи, Telegram webhook налаштовано.",
    "shutdown": "⏹️ <b>Rentkom зупинено</b>\n\nСервіси завершують роботу. Дані та volumes збережено.",
}


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
    if event not in MESSAGES or not token or not chat_id:
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
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    return 0 if body.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
