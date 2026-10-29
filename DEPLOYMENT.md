# Керівництво з розгортання «Рентком Смарт Ентерпрайз» (Deployment Guide)

## 1. Системні вимоги

- **Операційна система:** Linux, macOS, або Windows 10/11 (WSL2).
- **Платформа контейнеризації:** Docker Engine 24.0+ та Docker Compose v2.20+.
- **Локальне виконання (без Docker):** Python 3.11–3.13, PostgreSQL 15+ (опціонально), Ollama 0.3+.
- **Мінімальні ресурси:** 4 GB RAM, 2 CPU Cores, 5 GB вільного місця на диску.

---

## 2. Профілі запуску Docker Compose

Система підтримує три модульні профілі виконання:

| Профіль | Сервіси, що запускаються | Призначення | Потрібні секрети |
|---|---|---|:---:|
| **`demo`** *(за замовчуванням)* | `postgres`, `business-api`, `grafana`, `qdrant`, `n8n` | Демонстрація портфоліо, аналітика, REST API, workflow-рушій (RAG зв'язується з хостовим Ollama або працює у черговому режимі) | **Жодних** |
| **`connected`** | `demo` + бойові інтеграції | Робота зі шлюзом автоматизації, live webhooks | Telegram Bot Token, OAuth |
| **`full-ai`** | `connected` + `ollama` | Повний локальний ШІ-контур безпосередньо в контейнерах | 8 GB RAM, GPU (опціонально) |

---

## 3. Швидкий запуск у Docker

### Крок 1. Клонування та налаштування змінних оточення
```bash
cp .env.example .env
```
*(Для демонстраційного режиму редагувати `.env` не потрібно).*

### Крок 2. Запуск контейнерів
```bash
# Демонстраційний профіль (найшвидший запуск):
docker compose --profile demo up -d --build

# Або через утиліту керування:
python tools/manage.py up --profile demo
```

### Крок 2.1. Імпорт workflows у n8n (на чистому інстансі)
Чистий контейнер n8n запускається з порожньою базою даних. Усі 8 workflows змонтовано в контейнер за шляхом `/backup/workflows`:
```bash
docker compose exec n8n n8n import:workflow --input=/backup/workflows
```

### Крок 3. Перевірка доступності сервісів
- **FastAPI Interactive Docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Grafana Dashboards:** [http://localhost:3000](http://localhost:3000) (Логін: `admin`, Пароль: `admin`)
- **Qdrant Vector Web UI:** [http://localhost:6333/dashboard](http://localhost:6333/dashboard)
- **Health Diagnostics Endpoint:** [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

## 4. Локальне розгортання без Docker (Standalone Python)

Якщо Docker Desktop вимкнено або обмежено ресурсами, система працює автономно:

```bash
# 1. Створення віртуального оточення
python -m venv .venv
source .venv/bin/activate  # На Windows: .venv\Scripts\activate

# 2. Встановлення залежностей
pip install -r requirements.txt

# 3. Наповнення демо-бази даних (PostgreSQL)
python tools/seed_demo_data.py

# 4. Запуск сервера бізнес-правил
python tools/manage.py up
```

---

## 5. Зупинка сервісів
```bash
docker compose --profile demo --profile connected --profile full-ai down
# Або:
python tools/manage.py down
```

---

## 6. Налаштування облікових записів n8n (Credentials Bootstrap)

Для роботи підсистеми автоматизації (n8n workflows) у режимах `connected` або `demo`:

### Варіант А. Автоматичний CLI імпорт (Рекомендовано)
Запустіть підтримуваний скрипт імпорту:
```bash
python scripts/bootstrap_n8n_credentials.py
```
Скрипт зчитує `INTERNAL_API_KEY` із середовища/конфігурації та виконує офіційну команду `n8n import:credentials`, миттєво видаляючи тимчасовий дескриптор.

### Варіант Б. Ручне додавання через веб-інтерфейс n8n (2 кліки)
1. Відкрийте n8n: [http://localhost:5678](http://localhost:5678)
2. Перейдіть до **Credentials** $\to$ **Add Credential** $\to$ оберіть **Header Auth**.
3. Заповніть параметри:
   - **Credential Name:** `Rentkom Internal Auth` (ID: `RentkomInternalAuth`)
   - **Name:** `X-Internal-Token`
   - **Value:** значення `INTERNAL_API_KEY` з вашого файлу `.env`
4. Натисніть **Save**.
