# Аудит Telegram UX Rentkom

## Джерела повідомлень

- `core/api/v1/endpoints/telegram.py`: команди, RBAC, меню ресурсів, LR2/LR6 mock routing, mode/notification callbacks і Telegram dispatcher.
- `SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json`: production routing, LR2 сценарій, LR6 greeting, RAG presentation і fallback.
- `SMART_ENTERPRISE/automation/n8n/feedback_to_telegram.json`: адміністративне сповіщення про новий відгук.
- `core/services/outbox_service.py`: транспорт транзакційних Telegram-сповіщень.
- `scripts/configure_telegram_commands.py`: USER/ADMIN command scopes.
- `tools/notify_telegram_lifecycle.py`, `tools/start_demo.ps1`, `tools/stop_demo.ps1`: startup/shutdown повідомлення адміністратору.

## Команди та відповіді

| Команда/подія | USER | ADMIN | Представлення |
|---|---:|---:|---|
| `/start` | так | так | LR2 меню або LR6 greeting залежно від глобального режиму |
| `/help` | так | так | довідка про публічні команди |
| `/resources` | так | так | форми; ADMIN додатково бачить Sheets та інфраструктуру |
| `/feedback` | так | так | налаштована форма відгуку |
| `/consultation` | так | так | налаштована форма консультації |
| `/admin` | ні | так | стан і inline-меню адміністратора |
| `/mode` | ні | так | inline-перемикач LR2/LR6; символ `🔄` збережено |
| `/notifications` | ні | так | стан і inline-перемикач сповіщень ЛР3 |
| LR2 кнопки | так | так | `🏠 Купівля та продаж`, `🔑 Орендодавцям` |
| LR2 fallback | так | так | коротка підказка повернення до меню/довідки |
| LR6 RAG | так | так | HTML heading, escaped answer, escaped sources |
| startup/shutdown | ні | так | службовий статус без секретів і технічних деталей |

Адміністративні URL не формуються для неавторизованої Telegram identity. Локальні URL відображаються в `<code>` для читання й копіювання; зовнішні валідні URL додатково отримують inline-кнопки.
