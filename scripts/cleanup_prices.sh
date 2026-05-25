#!/bin/bash
CONTAINER="wb_repricer_db_prod"
DB="wb_repricer"
DB_USER="postgres"
LOG="/home/ivanzagulin/backups/cleanup.log"
mkdir -p /home/ivanzagulin/backups
exec >> "$LOG" 2>&1
echo "=== $(date) START ==="

pg() { docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -t -A -c "$1"; }

# 1. Удаляем старше 30 дней
for PAIR in "price_snapshots snapshot_at" "price_changes changed_at"; do
    TBL=$(echo $PAIR | cut -d' ' -f1)
    COL=$(echo $PAIR | cut -d' ' -f2)
    echo "[delete >30d] $TBL"
    while true; do
        N=$(pg "DELETE FROM ${TBL} WHERE id IN (SELECT id FROM ${TBL} WHERE ${COL} < NOW()-INTERVAL '30 days' LIMIT 100000);" | tr -cd '0-9')
        echo "  batch: ${N:-0}"
        [ "${N:-0}" -lt 1 ] && break
    done
done

# 2. Дедупликация price_snapshots по дням
echo "[dedup] price_snapshots"
DATES=$(pg "SELECT DISTINCT DATE(snapshot_at)::text FROM price_snapshots ORDER BY 1;")
for D in $DATES; do
    docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -t -A -c "
WITH daily AS (
    SELECT product_id, MAX(snapshot_at) AS keep_ts,
           MAX(seller_price::numeric) / NULLIF(MIN(seller_price::numeric),0) AS ratio
    FROM price_snapshots
    WHERE DATE(snapshot_at) = '${D}'
    GROUP BY product_id HAVING COUNT(*) > 1
)
DELETE FROM price_snapshots ps
USING daily d
WHERE ps.product_id = d.product_id
  AND DATE(ps.snapshot_at) = '${D}'
  AND ps.snapshot_at < d.keep_ts
  AND d.ratio < 1.25;" > /dev/null
    echo -n "."
done
echo " snapshots done"

# 3. Дедупликация price_changes по дням
echo "[dedup] price_changes"
DATES=$(pg "SELECT DISTINCT DATE(changed_at)::text FROM price_changes ORDER BY 1;")
for D in $DATES; do
    docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -t -A -c "
WITH daily AS (
    SELECT product_id, MAX(changed_at) AS keep_ts,
           MAX(ABS(new_seller_price::numeric - old_seller_price::numeric) / NULLIF(old_seller_price::numeric,0)) AS max_chg
    FROM price_changes
    WHERE DATE(changed_at) = '${D}'
    GROUP BY product_id HAVING COUNT(*) > 1
)
DELETE FROM price_changes pc
USING daily d
WHERE pc.product_id = d.product_id
  AND DATE(pc.changed_at) = '${D}'
  AND pc.changed_at < d.keep_ts
  AND d.max_chg < 0.25;" > /dev/null
    echo -n "."
done
echo " changes done"

# 4. VACUUM
echo "[vacuum]"
docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -c "VACUUM (ANALYZE) price_snapshots;" 2>&1 | tail -1
docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -c "VACUUM (ANALYZE) price_changes;" 2>&1 | tail -1

echo "[stats]"
docker exec "$CONTAINER" psql -U "$DB_USER" "$DB" -c "
SELECT tbl, cnt, sz FROM (
  SELECT 'snapshots' AS tbl, COUNT(*) AS cnt, pg_size_pretty(pg_total_relation_size('price_snapshots')) AS sz FROM price_snapshots
  UNION ALL
  SELECT 'changes', COUNT(*), pg_size_pretty(pg_total_relation_size('price_changes')) FROM price_changes
) t;"

echo "=== $(date) DONE ==="
