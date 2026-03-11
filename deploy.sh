#!/bin/bash
set -e
cd ~/repraiser_prod/repraiser_prod

echo "[deploy] Pulling from GitHub..."
git pull origin main

echo "[deploy] Rebuilding containers..."
docker-compose up -d --build --remove-orphans

echo "[deploy] Done. Running containers:"
docker-compose ps --format 'table {{.Name}}\t{{.Status}}'
