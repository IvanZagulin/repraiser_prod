#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

echo "[deploy] Pulling from GitHub..."
git pull origin main

echo "[deploy] Clearing Python bytecache..."
find ./backend -name '*.pyc' -delete 2>/dev/null
find ./backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "[deploy] Building images (without stopping running containers)..."
docker-compose build

echo "[deploy] Rolling restart: backend → celery → scheduler (frontend stays up)..."
# Backend first — uses health check, old container serves until new is healthy
docker-compose up -d --no-deps --remove-orphans backend
echo "[deploy] Waiting for backend health check..."
timeout 60 bash -c 'until docker inspect --format="{{.State.Health.Status}}" wb_repricer_backend_prod 2>/dev/null | grep -q healthy; do sleep 2; done' \
  && echo "[deploy] Backend healthy ✓" \
  || echo "[deploy] WARNING: Backend health check timed out"

# Worker + scheduler + flower — no health check needed, just restart
docker-compose up -d --no-deps celery_worker scheduler flower

echo "[deploy] Done. Running containers:"
docker-compose ps --format 'table {{.Name}}\t{{.Status}}'
