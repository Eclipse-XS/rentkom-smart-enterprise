# Telegram mode-switch notifications

`POST /api/v1/telegram/mode` is the authoritative switching path for REST and `tools/set_telegram_mode.ps1`. It requires an administrator or internal token. The selected LR2/LR6 value is committed to PostgreSQL `system_config` before notification delivery is attempted.

Successful LR2 activation sends the restored confirmation text and the same `ReplyKeyboardMarkup` used by the LR2 `/start` interface. Successful LR6 activation sends the restored confirmation text and `{"remove_keyboard": true}`. The configured and live Ollama model was verified as `qwen3:4b-instruct` on 2026-11-05.

The user-facing Telegram interface is the single `/mode` command. The unified webhook intercepts it before LR2/LR6 routing and returns an inline keyboard with `ЛР2 — Сценарний бот` and `ЛР6 — AI-асистент`. Its callback is handled by the same webhook, answered with `answerCallbackQuery`, and then passed to the existing PostgreSQL-backed mode controller. Both the command and callback require matching `TELEGRAM_ADMIN_USER_ID` and `TELEGRAM_ADMIN_CHAT_ID`; chat ownership alone is not authorization.

The former `/mode lr2` and `/mode lr6` forms are deprecated and do not change state. REST and PowerShell changes notify only `TELEGRAM_ADMIN_CHAT_ID`. Selecting the active mode returns `UNCHANGED` without another activation notification. Telegram update IDs are acquired through the existing inbox guard, so repeated callbacks cannot execute the state change twice. A post-commit Telegram failure returns `PARTIAL_SUCCESS`; it does not roll back the persisted mode.
