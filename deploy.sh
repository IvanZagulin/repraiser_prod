#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

# Сервисы приложения — только их пересобираем и пересоздаём.
# db, redis и мониторинг (loki/grafana/prometheus/promtail/cadvisor/node_exporter)
# НЕ трогаем → нет downtime БД и потери метрик при каждом деплое.
APP_SERVICES="backend celery_worker scheduler flower frontend"

# === Guard: do not deploy while repricer is active ===
# Celery inspect alone is not enough: after worker restarts a run can remain
# visible only in repricer_runs.status='running'. Check both signals.
echo "[deploy] Checking repricer activity..."
MAX_WAIT=300
WAITED=0
while true; do
    if docker exec wb_repricer_celery celery -A app.celery_app inspect active 2>/dev/null | grep -q "repricer.run_for_user"; then
        ACTIVE_REPRICER="1"
    else
        ACTIVE_REPRICER="0"
    fi

    RUNNING_DB=$(docker exec wb_repricer_backend_prod python -c 'from app.database import SessionLocal; from app.models import RepricerRun; s=SessionLocal(); print(s.query(RepricerRun).filter(RepricerRun.status == "running").count()); s.close()' 2>/dev/null || echo unknown)

    if [ "$ACTIVE_REPRICER" = "0" ] && [ "$RUNNING_DB" = "0" ]; then
        echo "[deploy] Repricer idle"
        break
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[deploy] ERROR: repricer still active after ${MAX_WAIT}s (celery=${ACTIVE_REPRICER}, db_running=${RUNNING_DB}); aborting deploy"
        exit 1
    fi
    echo "[deploy] Repricer busy (celery=${ACTIVE_REPRICER}, db_running=${RUNNING_DB}), waiting... (${WAITED}/${MAX_WAIT}s)"
    sleep 15
    WAITED=$((WAITED + 15))
done

echo "[deploy] Fixing .git permissions..."
sudo -n chown -R "$(whoami):$(id -gn)" .git/ 2>/dev/null || chmod -R u+rwX .git/ 2>/dev/null || true

echo "[deploy] Pulling from GitHub..."
if git pull origin main 2>&1; then
    echo "[deploy] git pull OK ✓"
else
    echo "[deploy] git pull failed — downloading archive..."
    REPO_URL=$(git remote get-url origin | sed 's/\.git$//' | sed 's|git@github.com:|https://github.com/|')
    curl -sL "${REPO_URL}/archive/refs/heads/main.tar.gz" | tar xz --strip-components=1
    echo "[deploy] Archive extracted ✓"
fi

echo "[deploy] Clearing Python bytecache..."
find ./backend -name '*.pyc' -delete 2>/dev/null || true
find ./backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Нативный `docker compose` (v2): шим /usr/local/bin/docker-compose молча
# терял аргументы и печатал help с кодом 0 — деплои 2026-06-07..10 были no-op.
echo "[deploy] Building frontend (no cache — ensures fresh JS bundle)..."
docker compose build --no-cache frontend

echo "[deploy] Building remaining app images..."
docker compose build backend celery_worker scheduler flower

# Применяем миграции на свежесобранном образе ДО пересоздания контейнеров.
echo "[deploy] Running DB migrations (alembic upgrade head)..."
docker compose run -T --rm --no-deps backend alembic upgrade head </dev/null

# --no-deps: не перезапускаем db/redis. Пересоздаются только app-сервисы.
echo "[deploy] Recreating app services (DB/Redis/monitoring untouched)..."
docker compose up -d --no-deps --force-recreate $APP_SERVICES

echo "[deploy] Waiting for backend health check..."
timeout 90 bash -c 'until docker inspect --format="{{.State.Health.Status}}" wb_repricer_backend_prod 2>/dev/null | grep -q healthy; do sleep 3; done' \
  && echo "[deploy] Backend healthy ✓" \
  || echo "[deploy] WARNING: Backend health check timed out"

echo "[deploy] Done. Running containers:"
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
