# Модуль реляційної бази даних (`database/`)

## Опис
Цей каталог містить артефакти для реляційної СУБД **PostgreSQL 18**:
- Схему створення таблиць, обмежень та індексів (`schema.sql`);
- Набір перевірочних запитів для аудиту та контролю цілісності (`verify.sql`).

База даних використовується як транзакційне ядро в **ЛР4** (збереження заявок на консультацію) та як пряме джерело SQL-даних для дашборду Grafana в **ЛР5**.

---

## Структура бази даних
- **Назва бази даних:** `lab4_real_estate`
- **Користувач:** `lab4_user`
- **Порт:** `5432` (localhost)
- **Таблиця:** `public.real_estate_requests`

### Схема таблиці `real_estate_requests`:
| Колонка | Тип даних | Опис |
|---|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` | Унікальний автоінкрементний ідентифікатор заявки |
| `full_name` | `VARCHAR(150) NOT NULL` | Прізвище, ім'я та по батькові клієнта |
| `phone` | `VARCHAR(30) NOT NULL` | Контактний номер телефону (валідований) |
| `request_purpose` | `VARCHAR(50) NOT NULL` | Ціль: «Хочу купити», «Хочу орендувати», «Хочу продати», «Хочу здати в оренду» |
| `property_type` | `VARCHAR(50) NOT NULL` | Тип об'єкта: «Квартира», «Будинок», «Комерційне приміщення», «Земля» |
| `property_location` | `VARCHAR(255) NOT NULL` | Бажане місцезнаходження (район, місто) |
| `consultation_date` | `DATE NOT NULL` | Бажана дата консультації з рієлтором |
| `additional_comments`| `TEXT` | Додаткові побажання клієнта (може бути NULL) |
| `created_at` | `TIMESTAMPTZ DEFAULT NOW()` | Системна часова мітка створення запису |
| `source_row_id` | `VARCHAR(255) UNIQUE` | Детермінований композитний хеш для забезпечення ідемпотентності |

---

## Команди перевірки в PowerShell
Для швидкої перевірки вмісту таблиці виконайте:
```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h 127.0.0.1 -p 5432 -U lab4_user -d lab4_real_estate -c "SELECT id, full_name, phone, request_purpose, property_type, created_at FROM real_estate_requests;"
```
