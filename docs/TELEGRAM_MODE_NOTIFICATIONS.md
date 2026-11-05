# Telegram mode-switch notifications

`POST /api/v1/telegram/mode` is the authoritative switching path for REST and `tools/set_telegram_mode.ps1`. It requires an administrator or internal token. The selected LR2/LR6 value is committed to PostgreSQL `system_config` before notification delivery is attempted.

Successful LR2 activation sends the restored confirmation text and the same `ReplyKeyboardMarkup` used by the LR2 `/start` interface. Successful LR6 activation sends the restored confirmation text and `{"remove_keyboard": true}`. The configured and live Ollama model was verified as `qwen3:4b-instruct` on 2026-11-05.

Authorized Telegram `/mode lr2` and `/mode lr6` commands return the same confirmation in the originating administrator chat. REST and PowerShell changes notify only `TELEGRAM_ADMIN_CHAT_ID`. Selecting the active mode returns `UNCHANGED` without another notification. A post-commit Telegram failure returns `PARTIAL_SUCCESS`; it does not roll back the persisted mode.
