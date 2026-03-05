# WB Repricer - Автоматический репрайсер для Wildberries

SaaS-система автоматического управления ценами товаров на маркетплейсе Wildberries.

## Возможности

- ✅ Управление ценами до 4000 товаров
- ✅ Две стратегии ценообразования: РРЦ и Competitor
- ✅ Автоматический запуск по расписанию (раз в час)
- ✅ Ручной запуск для отдельных товаров
- ✅ Защитные механизмы (min/max цена, промо-товары)
- ✅ Парсинг через пул SOCKS5 прокси
- ✅ JWT авторизация
- ✅ Telegram уведомления о критических сбоях
- ✅ Импорт/экспорт конфигураций в Excel

## Технологический стек

**Backend:**
- Python 3.11
- FastAPI
- PostgreSQL
- SQLAlchemy + Alembic
- APScheduler
- httpx (async HTTP клиент)

**Frontend:**
- React + Vite
- Tailwind CSS

**Инфраструктура:**
- Docker + Docker Compose

## Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone <repository-url>
cd wb-repricer
```

### 2. Настроить переменные окружения

```bash
cp .env.example .env
```

Заполните `.env` файл:
- `POSTGRES_PASSWORD` - пароль для PostgreSQL
- `JWT_SECRET` - секретный ключ для JWT (мин. 32 символа)
- `TELEGRAM_BOT_TOKEN` - токен Telegram бота (опционально)
- `TELEGRAM_CHAT_ID` - ID чата для уведомлений (опционально)
- `SOCKS5_PROXIES` - список SOCKS5 прокси (формат: `ip:port:login:password;ip:port:login:password`)

### 3. Запустить проект

```bash
docker-compose up -d
```

Сервисы:
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/api/docs
- Frontend: http://localhost:5173
- PostgreSQL: localhost:5432

### 4. Применить миграции БД

```bash
docker-compose exec backend alembic upgrade head
```

## Структура проекта

```
wb-repricer/
├── backend/           # FastAPI приложение
│   ├── app/
│   │   ├── models/    # SQLAlchemy модели
│   │   ├── schemas/   # Pydantic схемы
│   │   ├── api/       # API роуты
│   │   ├── services/  # Бизнес-логика
│   │   └── utils/     # Утилиты
│   └── alembic/       # Миграции БД
├── scheduler/         # APScheduler сервис
├── frontend/          # React приложение
└── docker-compose.yml
```

## API Endpoints

### Авторизация
- `POST /api/auth` - Вход и получение JWT токена

### Аккаунты
- `GET /api/accounts` - Список аккаунтов
- `POST /api/accounts` - Создать аккаунт
- `PUT /api/accounts/{id}` - Обновить аккаунт
- `DELETE /api/accounts/{id}` - Удалить аккаунт

### Товары
- `GET /api/products` - Список товаров (с фильтрами)
- `GET /api/products/{id}` - Детали товара
- `GET /api/products/{id}/history` - История цен товара

### Репрайсер
- `POST /api/repricer/run` - Ручной запуск репрайсера
- `POST /api/import/strategy` - Импорт стратегий из Excel
- `POST /api/import/prices` - Импорт цен из Excel

### Экспорт
- `GET /api/export/history` - Экспорт истории в Excel

## Разработка

### Создание новой миграции

```bash
docker-compose exec backend alembic revision --autogenerate -m "описание изменений"
```

### Применение миграций

```bash
docker-compose exec backend alembic upgrade head
```

### Откат миграции

```bash
docker-compose exec backend alembic downgrade -1
```

### Логи

```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f backend
docker-compose logs -f scheduler
```

## Стратегии ценообразования

### РРЦ (Recommended Retail Price)
Поддерживает финальную цену покупателя в диапазоне [РРЦ - tolerance; РРЦ + tolerance].

Параметры:
- `rrp_price` - Рекомендованная розничная цена
- `rrp_tolerance` - Допуск в рублях (по умолчанию 5₽)

### Competitor
Устанавливает цену относительно цены конкурента.

Параметры:
- `competitor_nm_id` - nmID товара конкурента
- `competitor_offset_type` - Тип смещения (`rub` или `pct`)
- `competitor_offset_direction` - Направление (`above` или `below`)
- `competitor_offset_value` - Величина смещения

## Защитные механизмы

1. **Обязательный min_price** - Репрайсер не запустится, если min_price не указан (для отключения указать 0)
2. **Проверка промо-товаров** - Товары в акциях WB не изменяются
3. **Проверка применения цен** - После отправки в WB API делается 3 проверки с паузой 10 сек
4. **Out of Stock** - Если товар конкурента отсутствует, цена не меняется
5. **Rate limiting** - Автоматический retry при получении 429 от WB API

## Telegram уведомления

Система отправляет критические уведомления:
- Истечение срока действия WB API токена
- Полный отказ WB API
- Критические ошибки планировщика

## Лицензия

Proprietary

## Поддержка

При возникновении проблем создайте issue в репозитории.
