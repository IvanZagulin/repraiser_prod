# 🚀 WB Repricer - Быстрый старт

## Шаг 1: Клонирование и настройка

```bash
# Распаковать архив
tar -xzf wb-repricer-stage3.tar.gz
cd wb-repricer

# Создать .env из примера
cp .env.example .env
```

## Шаг 2: Настройка .env

Откройте `.env` и заполните обязательные переменные:

```bash
# PostgreSQL
POSTGRES_PASSWORD=your_secure_password_123

# JWT (минимум 32 символа)
JWT_SECRET=your_very_long_and_secure_jwt_secret_key_here_min_32_chars

# SOCKS5 Proxies (формат: ip:port:login:password)
SOCKS5_PROXIES=1.2.3.4:1080:user1:pass1;5.6.7.8:1080:user2:pass2

# Telegram (опционально, для уведомлений)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Шаг 3: Запуск проекта

```bash
# Запустить все сервисы
docker-compose up -d

# Проверить статус
docker-compose ps
```

Должны работать 4 контейнера:
- ✅ wb_repricer_db (PostgreSQL)
- ✅ wb_repricer_backend (FastAPI)
- ✅ wb_repricer_scheduler (APScheduler)
- ✅ wb_repricer_frontend (React) - будет готов позже

## Шаг 4: Применить миграции БД

```bash
# Применить миграции
docker-compose exec backend alembic upgrade head

# Проверка
docker-compose exec backend alembic current
```

## Шаг 5: Проверка работы

### API Documentation
Откройте в браузере: http://localhost:8000/api/docs

Вы увидите Swagger UI со всеми доступными endpoints.

### Health Check
```bash
curl http://localhost:8000/api/health
```

Ответ:
```json
{
  "status": "healthy",
  "database": "connected",
  "services": {
    "wb_api": "operational",
    "catalog_parser": "operational",
    "proxy_pool": {...}
  }
}
```

## Шаг 6: Первый запуск (через API)

### 6.1 Получить JWT токен

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123"
  }'
```

Ответ:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

Скопируйте `access_token` для следующих запросов.

### 6.2 Создать аккаунт WB

```bash
curl -X POST http://localhost:8000/api/accounts \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Основной магазин",
    "wb_api_token": "YOUR_WB_API_TOKEN"
  }'
```

### 6.3 Создать товар

```bash
curl -X POST http://localhost:8000/api/products \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": 1,
    "nm_id": 431871218,
    "seller_sku": "SKU-001",
    "name": "Тестовый товар"
  }'
```

### 6.4 Настроить репрайсер (RRP стратегия)

```bash
curl -X POST http://localhost:8000/api/products/1/config \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "rrp",
    "is_active": true,
    "min_price": 2000,
    "max_price": 3000,
    "rrp_price": 2500,
    "rrp_tolerance": 100
  }'
```

### 6.5 Запустить репрайсер вручную

```bash
curl -X POST http://localhost:8000/api/repricer/run-sync \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "seller_skus": ["SKU-001"]
  }'
```

## Шаг 7: Мониторинг

### Просмотр логов

```bash
# Backend
docker-compose logs -f backend

# Scheduler
docker-compose logs -f scheduler

# Все сервисы
docker-compose logs -f
```

### Проверка прогонов

```bash
# Список прогонов
curl -X GET http://localhost:8000/api/repricer/runs \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"

# Детали прогона с изменениями
curl -X GET "http://localhost:8000/api/repricer/runs/1?include_changes=true" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

### История цен товара

```bash
curl -X GET http://localhost:8000/api/products/1/history \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

## Шаг 8: Excel импорт/экспорт

### Экспорт истории цен

```bash
# Скачать через браузер
http://localhost:8000/api/export/history?seller_skus=SKU-001

# Или через curl
curl -X GET "http://localhost:8000/api/export/history?seller_skus=SKU-001" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  --output history.xlsx
```

### Импорт стратегий

Создайте Excel файл со следующей структурой:

| seller_sku | strategy | is_active | min_price | max_price | rrp_price | rrp_tolerance | competitor_nm_id | competitor_offset_type | competitor_offset_direction | competitor_offset_value |
|------------|----------|-----------|-----------|-----------|-----------|---------------|------------------|------------------------|----------------------------|-------------------------|
| SKU-001    | rrp      | true      | 2000      | 3000      | 2500      | 100           |                  |                        |                            |                         |
| SKU-002    | competitor | true    | 1500      | 4000      |           |               | 123456789        | rub                    | below                      | 50                      |

```bash
curl -X POST http://localhost:8000/api/export/import/strategy \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -F "file=@strategy_config.xlsx"
```

## Автоматический планировщик

Scheduler запускает репрайсер автоматически:

- **Каждый час** (в :00) - обработка всех активных товаров
- **Каждые 30 минут** - health check прокси

Проверка планировщика:
```bash
docker-compose logs -f scheduler
```

## Остановка проекта

```bash
# Остановить все контейнеры
docker-compose down

# Остановить и удалить volumes (БД будет очищена!)
docker-compose down -v
```

## Troubleshooting

### Проблема: Backend не стартует

```bash
# Проверить логи
docker-compose logs backend

# Перезапустить
docker-compose restart backend
```

### Проблема: Ошибка подключения к БД

```bash
# Проверить что БД запущена
docker-compose ps db

# Проверить переменные окружения
docker-compose exec backend env | grep DATABASE
```

### Проблема: Все прокси мертвы

```bash
# Проверить конфигурацию прокси
docker-compose exec backend env | grep SOCKS5

# Проверить логи
docker-compose logs backend | grep proxy
```

### Проблема: Миграции не применяются

```bash
# Попробовать вручную
docker-compose exec backend alembic upgrade head

# Проверить текущую версию
docker-compose exec backend alembic current

# Если нужно - откатить
docker-compose exec backend alembic downgrade -1
```

## Полезные команды

```bash
# Зайти в контейнер backend
docker-compose exec backend bash

# Выполнить Python код
docker-compose exec backend python -c "from app.services import proxy_pool; print('OK')"

# Проверить БД
docker-compose exec db psql -U postgres -d wb_repricer -c "SELECT count(*) FROM products;"

# Очистить логи
docker-compose logs --no-log-prefix > /dev/null 2>&1

# Пересоздать контейнеры
docker-compose up -d --force-recreate
```

## Следующие шаги

1. ✅ Настройте Telegram уведомления для критических алертов
2. ✅ Загрузите свои товары через API или Excel импорт
3. ✅ Настройте стратегии ценообразования
4. ✅ Запустите тестовый прогон
5. ✅ Мониторьте через `/api/docs` или создайте Frontend

## Поддержка

При возникновении проблем:
1. Проверьте логи: `docker-compose logs -f`
2. Проверьте health check: `curl http://localhost:8000/api/health`
3. Проверьте документацию API: http://localhost:8000/api/docs

---

**Проект готов к использованию! 🎉**
