# Керівництво з розгортання «Рентком Смарт Ентерпрайз» (Deployment Guide)

## 1. Системні вимоги

- **Операційна система:** Linux, macOS, або Windows 10/11 (WSL2).
- **Платформа контейнеризації:** Docker Engine 24.0+ та Docker Compose v2.20+.
- **Локальне виконання (без Docker):** Python 3.11–3.13, PostgreSQL 16+ (або SQLite у демо-режимі), Qdrant v1.12+, Ollama 0.3+.
- **Мінімальні ресурси:** 4 GB RAM (8 GB для `full-ai`), 2 CPU Cores, 5 GB вільного місця на диску.

---

## 2. Режими та профілі запуску (`demo` vs `connected` vs `production`)

Система підтримує чотири модульні профілі Docker Compose та три режими середовища (`ENVIRONMENT`):

| Профіль Docker Compose | Сервіси, що запускаються | Призначення | Потрібні секрети |
|---|---|---|:---:|
| **`demo`** *(за замовчуванням)* | `postgres`, `business-api`, `grafana`, `qdrant` | Локальна демонстрація портфоліо, аналітика, REST API, mock-сповіщення | **Жодних** |
| **`connected`** | `demo` + `n8n` | Інтеграція зі шлюзом автоматизації n8n, live Telegram webhooks та SMTP | `INTERNAL_API_KEY`, `ADMIN_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SECRET_TOKEN`, SMTP |
| **`full-ai`** | `connected` + `ollama` | Повний локальний контур RAG з контейнеризованим Ollama | 8 GB RAM, GPU (опціонально) |
| **`dedicated-worker`** | `outbox-worker` | Окремий фоновий процес обробки Transactional Outbox (коли `RUN_OUTBOX_WORKER_IN_LIFESPAN=false`) | Як у поточного режиму |

### Режими безпеки (`ENVIRONMENT`)
- **`demo` / `development` / `test`**: Дозволяє використання `/api/v1/telegram/webhook-mock`, `/api/v1/telegram/mock-dispatcher` та імітованого транспорту сповіщень (`NOTIFICATION_PROVIDER=mock`).
- **`connected` / `production`**:
  - Примусово вимикає демонстраційні та mock-ендпоінти (`HTTP 403`).
  - Під час старту (`settings.validate_runtime_security()`) перевіряє наявність надійних ключів `ADMIN_API_KEY` та `INTERNAL_API_KEY` (мінімум 24 символи, заборона демо-значень), забороняє `ALLOW_TEST_WEBHOOK=true` та `NOTIFICATION_PROVIDER=mock` у `production`.
  - Ендпоінт аналітики (`GET /api/v1/analytics/summary`) вимагає авторизації адміністратора (`X-Admin-Token`) або внутрішнього токена.
  - Анонімні заявки (`POST /api/v1/requests/`) проходять через захисний шлюз неперевірених лідів (HTTP 202 Accepted, opaque submission ID) без розкриття персональних даних клієнтів (SEC-01/02).
  - База даних розділяє привілеї: застосунок працює під `rentkom_app`; n8n працює під `rentkom_n8n`, має DDL лише у схемі `n8n` і не має доступу до бізнес-таблиць `public`.
  - Для наявного volume після встановлення `RENTKOM_APP_PASSWORD` і `RENTKOM_N8N_PASSWORD` виконайте `docker compose exec postgres bash /docker-entrypoint-initdb.d/02_roles.sh`; видалення volume не потрібне.

---

## 3. Швидкий запуск у Docker

### Крок 1. Клонування та налаштування змінних оточення
```bash
cp .env.example .env
```
*(Для профілю `demo` редагувати `.env` не обов'язково; для `connected` або `production` обов'язково заповніть секретні ключі).*

### Крок 2. Запуск контейнерів
```bash
# Демонстраційний профіль:
docker compose --profile demo up -d --build

# Або через утиліту керування:
python tools/manage.py up --profile demo
```

### Крок 3. Перевірка доступності сервісів (Liveness & Readiness)
- **FastAPI Interactive Docs (Swagger):** `http://localhost:8000/docs`
- **Liveness Probe:** `http://localhost:8000/api/v1/health/live`
- **Readiness Probe (перевірка БД):** `http://localhost:8000/api/v1/health/ready`
- **Full Diagnostics Endpoint:** `http://localhost:8000/api/v1/health`
- **Grafana Dashboards:** `http://localhost:3000` (Логін та пароль задаються через `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` у `.env`)
- **Qdrant Vector Web UI:** `http://localhost:6333/dashboard`

---

## 4. Локальне розгортання без Docker (Standalone Python)

Якщо Docker Desktop вимкнено або недоступний, система підтримує повний цикл локальної перевірки:

```bash
# 1. Створення віртуального оточення
python -m venv .venv
source .venv/bin/activate  # На Windows: .venv\Scripts\activate

# 2. Встановлення залежностей
pip install -r requirements.txt

# 3. Наповнення демо-бази даних
python tools/seed_demo_data.py

# 4. Переіндексація бази знань RAG у Qdrant (за наявності Qdrant + Ollama)
python tools/reindex_knowledge_base.py

# 5. Запуск сервера бізнес-правил
python tools/manage.py up
```

---

## 5. Резервне копіювання та відновлення PostgreSQL (`pg_dump` / `psql`)

На відміну від файлового копіювання SQLite, для PostgreSQL використовується утиліта логічного резервного копіювання `tools/backup_restore.py` (інтегрована в `tools/manage.py`):

```bash
# Створення резервної копії PostgreSQL або SQLite:
python tools/manage.py backup --db-url "postgresql://postgres:password@127.0.0.1:5432/rentkom" --output-dir backups

# Перевірка відновлення у тестову ізольовану базу даних (обов'язково має містити test/disposable/temp у назві):
python tools/manage.py restore --backup-file backups/rentkom_pg_backup_YYYYMMDD_HHMMSS.sql --target-db-url "postgresql://postgres:password@127.0.0.1:5432/rentkom_test"
```
> **Захист даних:** Команда `restore` використовує `tests/safety.py` (`assert_safe_test_database`) і категорично відмовляється перезаписувати робочі або системні бази даних (`postgres`, `rentkom`, `lab4_real_estate`).

---

## 6. Налаштування облікових записів n8n (Credentials Bootstrap)

Для роботи підсистеми автоматизації (`SMART_ENTERPRISE/automation/n8n/*.json`) у режимах `connected` або `demo`:

### Варіант А. Автоматичний CLI імпорт (Рекомендовано)
```bash
python scripts/bootstrap_n8n_credentials.py
```
Скрипт вимагає `INTERNAL_API_KEY`, `TEST_WEBHOOK_SECRET` і `TELEGRAM_SECRET_TOKEN`, імпортує три header credentials через `n8n import:credentials` та видаляє тимчасовий файл.

### Trusted configuration
`N8N_BLOCK_ENV_ACCESS_IN_NODE=true` залишається увімкненим. Workflow expressions не читають `$env`: внутрішня авторизація та ingress secrets надходять з n8n credentials, адреса API є внутрішнім Compose DNS endpoint, а `TELEGRAM_ADMIN_CHAT_ID` задається як n8n project variable (`$vars`).

---

## 7. Зупинка сервісів
```bash
docker compose --profile demo --profile connected --profile full-ai --profile dedicated-worker down
# Або:
python tools/manage.py down
```
