#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

# Сервисы приложения — только их пересобираем и пересоздаём.
# db, redis и мониторинг (loki/grafana/prometheus/promtail/cadvisor/node_exporter)
# НЕ трогаем → нет downtime БД и потери метрик при каждом деплое.
APP_SERVICES="backend celery_worker scheduler flower frontend"

echo "[deploy] Pulling from GitHub..."
git pull origin main

echo "[deploy] Clearing Python bytecache..."
find ./backend -name '*.pyc' -delete 2>/dev/null || true
find ./backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "[deploy] Building app images..."
docker-compose build $APP_SERVICES

# Применяем миграции на свежесобранном образе ДО пересоздания контейнеров.
# Раньше этого шага не было вовсе — миграции приходилось гонять руками.
echo "[deploy] Running DB migrations (alembic upgrade head)..."
docker-compose run --rm --no-deps backend alembic upgrade head

# --no-deps: не перезапускаем db/redis (они в depends_on). Пересоздаются только
# изменившиеся app-сервисы; БД и мониторинг продолжают работать без перерыва.
echo "[deploy] Recreating app services (DB/Redis/monitoring untouched)..."
docker-compose up -d --no-deps $APP_SERVICES

echo "[deploy] Waiting for backend health check..."
timeout 90 bash -c 'until docker inspect --format="{{.State.Health.Status}}" wb_repricer_backend_prod 2>/dev/null | grep -q healthy; do sleep 3; done' \
  && echo "[deploy] Backend healthy ✓" \
  || echo "[deploy] WARNING: Backend health check timed out"

echo "[deploy] Done. Running containers:"
docker-compose ps --format 'table {{.Name}}\t{{.Status}}'
