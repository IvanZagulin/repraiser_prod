#!/bin/bash
# Бэкап PostgreSQL для WB Repricer
# Добавить в cron: 0 3 * * * /root/repraiser_prod/repraiser_prod/scripts/backup.sh

BACKUP_DIR="/var/backups/wb_repricer"
CONTAINER="wb_repricer_db_prod"
DB_USER="postgres"
DB_NAME="wb_repricer"
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

FILENAME="$BACKUP_DIR/db_$(date +%Y%m%d_%H%M%S).gz"

docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$FILENAME"

if [ $? -eq 0 ]; then
    echo "[$(date)] Бэкап успешно создан: $FILENAME ($(du -h $FILENAME | cut -f1))"
else
    echo "[$(date)] ОШИБКА создания бэкапа!" >&2
    exit 1
fi

# Удаляем бэкапы старше KEEP_DAYS дней
find "$BACKUP_DIR" -name "*.gz" -mtime +"$KEEP_DAYS" -delete
echo "[$(date)] Старые бэкапы удалены (старше $KEEP_DAYS дней)"
