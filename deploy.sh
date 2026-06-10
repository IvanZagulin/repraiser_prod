#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

# Сервисы приложения — только их пересобираем и пересоздаём.
# db, redis и мониторинг (loki/grafana/prometheus/promtail/cadvisor/node_exporter)
# НЕ трогаем → нет downtime БД и потери метрик при каждом деплое.
APP_SERVICES="backend celery_worker scheduler flower frontend"

# === Guard: не деплоить если celery занят ===
# Рестарт celery при активных задачах (acks_late=True) вызывает redelivery
# и может привести к 429-шторму WB API. Ждём до 5 мин.
echo "[deploy] Проверяем что celery свободен..."
MAX_WAIT=300
WAITED=0
while true; do
    # grep -c returns "0" with exit code 1 on no matches, causing "0\n0" via ||
    # Use grep -q to detect any active tasks
    if docker exec wb_repricer_celery celery -A app.celery_app inspect active 2>/dev/null | grep -q "'id':"; then
        ACTIVE="busy"
    else
        ACTIVE="0"
    fi
    if [ "$ACTIVE" = "0" ]; then
        echo "[deploy] Celery свободен ✓"
        break
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[deploy] WARNING: celery занят ($ACTIVE задач) уже ${MAX_WAIT}с — продолжаем деплой принудительно"
        break
    fi
    echo "[deploy] Celery занят ($ACTIVE задач), ждём... (${WAITED}/${MAX_WAIT}с)"
    sleep 15
    WAITED=$((WAITED + 15))
done

echo "[deploy] Fixing .git permissions..."
sudo chown -R "$(whoami):$(id -gn)" .git/ 2>/dev/null || chmod -R u+rwX .git/ 2>/dev/null || true

echo "[deploy] Pulling from GitHub..."
git pull origin main

echo "[deploy] Clearing Python bytecache..."
find ./backend -name '*.pyc' -delete 2>/dev/null || true
find ./backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Нативный `docker compose` (v2): шим /usr/local/bin/docker-compose молча
# терял аргументы и печатал help с кодом 0 — деплои 2026-06-07..10 были no-op.
echo "[deploy] Building app images..."
docker compose build $APP_SERVICES

# Применяем миграции на свежесобранном образе ДО пересоздания контейнеров.
echo "[deploy] Running DB migrations (alembic upgrade head)..."
docker compose run --rm --no-deps backend alembic upgrade head

# --no-deps: не перезапускаем db/redis. Пересоздаются только app-сервисы.
echo "[deploy] Recreating app services (DB/Redis/monitoring untouched)..."
docker compose up -d --no-deps $APP_SERVICES

echo "[deploy] Waiting for backend health check..."
timeout 90 bash -c 'until docker inspect --format="{{.State.Health.Status}}" wb_repricer_backend_prod 2>/dev/null | grep -q healthy; do sleep 3; done' \
  && echo "[deploy] Backend healthy ✓" \
  || echo "[deploy] WARNING: Backend health check timed out"

echo "[deploy] Done. Running containers:"
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
