#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

echo "[deploy] Pulling from GitHub..."
git pull origin main

echo "[deploy] Clearing Python bytecache..."
find ./backend -name '*.pyc' -delete 2>/dev/null
find ./backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "[deploy] Rebuilding containers..."
docker-compose up -d --build --remove-orphans

echo "[deploy] Done. Running containers:"
docker-compose ps --format 'table {{.Name}}\t{{.Status}}'
