#!/bin/bash
# Использование:
#   ./push-deploy.sh "commit message"   — коммитит всё, пушит, деплоит
#   ./push-deploy.sh --deploy-only      — только деплой на сервер (без коммита)

set -e

SERVER="qvaz.quasar-x.ru"
DEPLOY_CMD="bash ~/repraiser_prod/repraiser_prod/deploy.sh"

if [ "$1" == "--deploy-only" ]; then
  echo "[push-deploy] Deploying on server..."
  ssh "$SERVER" "$DEPLOY_CMD"
  exit 0
fi

if [ -z "$1" ]; then
  echo "Usage: ./push-deploy.sh \"commit message\""
  echo "       ./push-deploy.sh --deploy-only"
  exit 1
fi

echo "[push-deploy] Staging all changes..."
git add -A

echo "[push-deploy] Committing: $1"
git commit -m "$1" || echo "[push-deploy] Nothing to commit, skipping..."

echo "[push-deploy] Pushing to GitHub..."
git push origin main

echo "[push-deploy] Deploying on server..."
ssh "$SERVER" "$DEPLOY_CMD"

echo "[push-deploy] All done!"
