# ✅ WB Repricer - ПРОЕКТ ЗАВЕРШЕН

## 🎯 Общая информация

**WB Repricer** - полнофункциональная SaaS-система автоматического управления ценами товаров на маркетплейсе Wildberries.

**Версия:** 1.2.0  
**Дата:** 2026-02-17  
**Статус:** ✅ Production Ready (без Frontend UI)

---

## 📦 Что включено

### Backend (FastAPI + PostgreSQL)
✅ **REST API** (5 модулей, 25+ endpoints)
- Authentication (JWT)
- Accounts Management
- Products & Config
- Repricer Control
- Import/Export (Excel)

✅ **Бизнес-логика**
- Repricer Engine с 2 стратегиями
- SOCKS5 Proxy Pool (ротация + health check)
- WB API Client (retry, rate limiting)
- Catalog Parser (catalog.wb.ru v4)
- Telegram Notifier

✅ **База данных**
- 6 таблиц с relationships
- Alembic миграции
- Индексы и constraints
- Cascade delete

✅ **Scheduler**
- Hourly автопрогоны
- Proxy health checks
- Telegram alerts

---

## 🚀 Возможности

### Стратегии ценообразования

**1. RRP (Recommended Retail Price)**
- Поддержание финальной цены в диапазоне [РРЦ ± tolerance]
- Автоматический расчет цены продавца
- Учет СПП (скидка платформы)

**2. Competitor**
- Привязка к цене конкурента
- Смещение в рублях или процентах
- Направление: выше или ниже
- Out-of-Stock обработка

### Защитные механизмы

✅ **Обязательный min_price** (0 для отключения)  
✅ **Опциональный max_price**  
✅ **Проверка промо-акций WB**  
✅ **Проверка истечения токенов**  
✅ **Проверка применения цен** (3 × 10с)  
✅ **Out-of-Stock конкурентов**

### Парсинг и API

✅ **Пул из 20 SOCKS5 прокси** с ротацией  
✅ **До 5 попыток** парсинга на товар  
✅ **Retry при 429** от WB API  
✅ **Exponential backoff**  
✅ **Детальное логирование**

### Мониторинг

✅ **Telegram уведомления** о критических сбоях  
✅ **История цен** с временными метками  
✅ **Детальные логи прогонов**  
✅ **Excel экспорт** истории и прогонов

---

## 📊 Архитектура

```
┌─────────────┐
│   React UI  │ (будет в следующей версии)
└──────┬──────┘
       │ HTTP
┌──────▼──────────────────────────────────────┐
│          FastAPI Backend                     │
│  ┌────────────────────────────────────────┐ │
│  │ API Routes (auth, accounts, products,  │ │
│  │            repricer, export)           │ │
│  └────────────────┬───────────────────────┘ │
│                   │                          │
│  ┌────────────────▼───────────────────────┐ │
│  │ Services                                │ │
│  │  • RepricerEngine (стратегии)          │ │
│  │  • ProxyPool (SOCKS5 ротация)          │ │
│  │  • CatalogParser (catalog.wb.ru)       │ │
│  │  • WBAPIClient (официальный API)       │ │
│  │  • TelegramNotifier (alerts)           │ │
│  └────────────────┬───────────────────────┘ │
│                   │                          │
│  ┌────────────────▼───────────────────────┐ │
│  │ SQLAlchemy Models (6 таблиц)           │ │
│  └────────────────────────────────────────┘ │
└──────────────────┬───────────────────────────┘
                   │
         ┌─────────▼─────────┐
         │   PostgreSQL DB    │
         │   • accounts       │
         │   • products       │
         │   • product_config │
         │   • price_snapshots│
         │   • repricer_runs  │
         │   • price_changes  │
         └───────────────────┘

┌────────────────────────────────────────┐
│      APScheduler (отдельный сервис)    │
│  • Hourly repricer (cron: 0 * * * *)  │
│  • Proxy health check (*/30)           │
└────────────────────────────────────────┘

External APIs:
• catalog.wb.ru/cards/v4/detail (через SOCKS5)
• suppliers-api.wildberries.ru (WB Prices API)
• api.telegram.org (уведомления)
```

---

## 📁 Структура проекта

```
wb-repricer/
├── docker-compose.yml          # Оркестрация 4 сервисов
├── .env.example                # Шаблон переменных окружения
├── README.md                   # Полная документация
├── QUICKSTART.md              # Быстрый старт
│
├── backend/                    # FastAPI приложение
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── test_services.py       # Тесты сервисов
│   │
│   ├── alembic/               # Миграции БД
│   │   ├── env.py
│   │   └── versions/
│   │       └── 001_initial.py
│   │
│   └── app/
│       ├── main.py            # FastAPI app
│       ├── config.py          # Настройки
│       ├── database.py        # SQLAlchemy
│       │
│       ├── models/            # 6 моделей БД
│       │   ├── account.py
│       │   ├── product.py
│       │   ├── product_config.py
│       │   ├── price_snapshot.py
│       │   ├── repricer_run.py
│       │   └── price_change.py
│       │
│       ├── schemas/           # Pydantic схемы
│       │   ├── account.py
│       │   ├── product.py
│       │   ├── auth.py
│       │   └── repricer.py
│       │
│       ├── api/               # API роуты
│       │   ├── auth.py        # JWT авторизация
│       │   ├── accounts.py    # Управление аккаунтами
│       │   ├── products.py    # CRUD товаров
│       │   ├── repricer.py    # Запуск репрайсера
│       │   └── export.py      # Import/Export Excel
│       │
│       ├── services/          # Бизнес-логика
│       │   ├── repricer_engine.py    # Движок
│       │   ├── proxy_pool.py         # SOCKS5
│       │   ├── catalog_parser.py     # Парсер
│       │   ├── wb_api.py            # WB API
│       │   └── telegram.py          # Alerts
│       │
│       └── utils/
│           ├── jwt.py         # JWT утилиты
│           └── excel.py       # Excel import/export
│
├── scheduler/                 # APScheduler сервис
│   ├── Dockerfile
│   ├── requirements.txt
│   └── scheduler.py
│
└── frontend/                  # React UI (TODO)
    └── (будет создан позже)
```

---

## 🔧 API Endpoints

### Authentication
```
POST   /api/auth/login        # Получить JWT
GET    /api/auth/verify       # Проверить токен
```

### Accounts
```
GET    /api/accounts           # Список аккаунтов
POST   /api/accounts           # Создать аккаунт
GET    /api/accounts/{id}      # Получить аккаунт
PUT    /api/accounts/{id}      # Обновить аккаунт
DELETE /api/accounts/{id}      # Удалить аккаунт
```

### Products
```
GET    /api/products                    # Список товаров (фильтры)
POST   /api/products                    # Создать товар
GET    /api/products/{id}               # Получить товар
DELETE /api/products/{id}               # Удалить товар
POST   /api/products/{id}/config        # Создать конфиг
PUT    /api/products/{id}/config        # Обновить конфиг
GET    /api/products/{id}/history       # История цен
```

### Repricer
```
POST   /api/repricer/run-sync           # Синхронный запуск
POST   /api/repricer/run                # Асинхронный запуск
GET    /api/repricer/runs               # Список прогонов
GET    /api/repricer/runs/{id}          # Детали прогона
```

### Import/Export
```
GET    /api/export/history              # Экспорт истории (Excel)
GET    /api/export/run/{id}             # Экспорт прогона (Excel)
POST   /api/export/import/strategy      # Импорт стратегий (Excel)
POST   /api/export/import/products      # Импорт товаров (Excel)
```

**Swagger UI:** http://localhost:8000/api/docs

---

## 🎮 Примеры использования

### Создание товара с RRP стратегией

```bash
# 1. Логин
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  | jq -r '.access_token')

# 2. Создать аккаунт
curl -X POST http://localhost:8000/api/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Мой магазин",
    "wb_api_token": "YOUR_WB_TOKEN"
  }'

# 3. Создать товар
curl -X POST http://localhost:8000/api/products \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": 1,
    "nm_id": 431871218,
    "seller_sku": "BOOK-001",
    "name": "Философия войны"
  }'

# 4. Настроить репрайсер
curl -X POST http://localhost:8000/api/products/1/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "rrp",
    "is_active": true,
    "min_price": 2000,
    "max_price": 3000,
    "rrp_price": 2500,
    "rrp_tolerance": 100
  }'

# 5. Запустить
curl -X POST http://localhost:8000/api/repricer/run-sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"seller_skus": ["BOOK-001"]}'
```

### Создание товара с Competitor стратегией

```bash
curl -X POST http://localhost:8000/api/products/2/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "competitor",
    "is_active": true,
    "min_price": 1500,
    "max_price": 4000,
    "competitor_nm_id": 123456789,
    "competitor_offset_type": "rub",
    "competitor_offset_direction": "below",
    "competitor_offset_value": 50
  }'
```
→ Цена будет на 50₽ ниже конкурента

---

## 📈 Коды причин (reason_code)

| Код | Описание | Цена изменена? |
|-----|----------|----------------|
| `UPDATED` | Цена успешно обновлена | ✅ |
| `CLAMPED_TO_MIN` | Ограничено минимумом | ✅ |
| `CLAMPED_TO_MAX` | Ограничено максимумом | ✅ |
| `NO_CHANGE` | Цена в допуске | ❌ |
| `SKIPPED_PROMO` | Товар в акции WB | ❌ |
| `SKIPPED_INACTIVE` | Репрайсер отключен | ❌ |
| `COMPETITOR_OOS` | Конкурент отсутствует | ❌ |
| `COMPETITOR_UNAVAILABLE` | Карточка недоступна | ❌ |
| `WB_API_ERROR` | Ошибка WB API | ❌ |
| `PARSE_ERROR` | Ошибка парсинга | ❌ |

---

## ⚙️ Конфигурация (.env)

```bash
# Database
POSTGRES_DB=wb_repricer
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password

# JWT
JWT_SECRET=your_jwt_secret_min_32_chars
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# SOCKS5 Proxies
SOCKS5_PROXIES=ip:port:user:pass;ip:port:user:pass

# Repricer
RETRY_ATTEMPTS=5
PRICE_CHECK_DELAY=10
PRICE_CHECK_ATTEMPTS=3
```

---

## 🧪 Тестирование

```bash
# Тесты сервисов
docker-compose exec backend python test_services.py

# Health check
curl http://localhost:8000/api/health

# API docs
open http://localhost:8000/api/docs
```

---

## 📝 Что НЕ включено (TODO)

- ❌ Frontend UI (React)
- ❌ Аналитика и графики
- ❌ Multi-tenancy (несколько пользователей)
- ❌ Webhook интеграции
- ❌ Mobile app

---

## 🔒 Безопасность

✅ JWT авторизация  
✅ Password hashing (bcrypt)  
✅ SQL injection защита (SQLAlchemy ORM)  
✅ CORS настройки  
✅ Валидация входных данных (Pydantic)  
✅ Secrets в .env (не в коде)

**⚠️ Важно для продакшена:**
- Изменить дефолтный пароль (`admin/admin123`)
- Настроить CORS для конкретных доменов
- Использовать HTTPS
- Настроить rate limiting
- Включить мониторинг (Sentry, etc)

---

## 📚 Документация по этапам

1. ✅ **STAGE_1_COMPLETE.md** - Фундамент (БД, модели, схемы)
2. ✅ **STAGE_2_COMPLETE.md** - Парсинг & WB API
3. ✅ **STAGE_3_COMPLETE.md** - Бизнес-логика + API + Scheduler
4. ✅ **Этап 4** - Excel импорт/экспорт + миграции (текущий)

---

## 🎯 Производительность

- **Товаров:** до 4 000 шт
- **Прогон (1000 товаров):** ~10-15 минут
- **Прокси:** 20 шт (ротация)
- **Retry:** 5 попыток на товар
- **Rate limit:** авто retry при 429

---

## 🚀 Production Deployment

### Docker Compose (рекомендуется)

```bash
# Production build
docker-compose -f docker-compose.yml build

# Запуск
docker-compose up -d

# Логи
docker-compose logs -f
```

### Отдельные сервисы

Можно запустить каждый сервис отдельно:
- Backend на одном сервере
- Scheduler на другом
- PostgreSQL на третьем

---

## 📞 Поддержка

При возникновении проблем:
1. Проверьте `QUICKSTART.md`
2. Смотрите логи: `docker-compose logs -f`
3. Health check: `curl http://localhost:8000/api/health`
4. API docs: http://localhost:8000/api/docs

---

## 📄 Лицензия

Proprietary

---

**Проект полностью готов к использованию! 🎉**

Осталось только создать Frontend UI для удобного управления через браузер.
