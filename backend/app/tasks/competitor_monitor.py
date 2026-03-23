import asyncio
import logging
from decimal import Decimal

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="competitor.monitor",
    max_retries=1,
    default_retry_delay=120,
    time_limit=1800,
)
def monitor_competitors_task(self, user_id: int):
    """Мониторинг цен конкурентов — создаёт алерты при значительных изменениях.

    Оптимизации:
    - Один batch-запрос к WB API вместо asyncio.run() на каждого конкурента.
    - Дедупликация: новый алерт создаётся только если нет непрочитанного
      алерта для той же пары (product_id, competitor_nm_id).
    """
    db = SessionLocal()
    try:
        from app.models import Product, ProductConfig, Account, PriceSnapshot
        from app.models.competitor_entry import CompetitorEntry
        from app.models.competitor_alert import CompetitorAlert
        from app.models.app_setting import AppSetting
        from app.services.catalog_parser import catalog_parser
        from sqlalchemy import desc

        # Порог для алерта (% изменения)
        threshold_row = db.query(AppSetting).filter(
            AppSetting.key == "competitor_alert_threshold_pct",
            AppSetting.user_id == user_id,
        ).first()
        if not threshold_row:
            threshold_row = db.query(AppSetting).filter(
                AppSetting.key == "competitor_alert_threshold_pct",
                AppSetting.user_id.is_(None),
            ).first()
        threshold = float(threshold_row.value) if threshold_row else 5.0

        # Товары с конкурентной стратегией
        products = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .join(ProductConfig, Product.id == ProductConfig.product_id)
            .filter(
                Account.user_id == user_id,
                Account.is_active == True,
                ProductConfig.strategy == "competitor",
                ProductConfig.is_active == True,
            )
            .all()
        )

        if not products:
            return {"status": "ok", "monitored": 0, "alerts": 0}

        # ── Шаг 1: собираем все уникальные nm_id конкурентов ───────────────────
        # Структура: {product_id: [nm_id, ...]}
        product_competitor_map: dict[int, list[int]] = {}
        all_nm_ids: set[int] = set()

        for product in products:
            config = product.config
            if not config:
                continue
            nm_ids: list[int] = []

            entries = db.query(CompetitorEntry).filter(
                CompetitorEntry.product_config_id == config.id
            ).all()
            for e in entries:
                nm_ids.append(e.nm_id)
                all_nm_ids.add(e.nm_id)

            if not entries and config.competitor_nm_id:
                nm_ids.append(config.competitor_nm_id)
                all_nm_ids.add(config.competitor_nm_id)

            if nm_ids:
                product_competitor_map[product.id] = nm_ids

        if not all_nm_ids:
            return {"status": "ok", "monitored": len(products), "alerts": 0}

        # ── Шаг 2: один batch-запрос к WB вместо asyncio.run() в цикле ─────────
        loop = asyncio.new_event_loop()
        try:
            batch_results: dict = loop.run_until_complete(
                catalog_parser.parse_batch_ids(list(all_nm_ids))
            )
        finally:
            loop.close()

        # ── Шаг 3: предзагружаем непрочитанные алерты (дедупликация) ───────────
        product_ids = list(product_competitor_map.keys())
        existing_alerts: set[tuple] = set()
        if product_ids:
            rows = db.query(
                CompetitorAlert.product_id,
                CompetitorAlert.competitor_nm_id,
            ).filter(
                CompetitorAlert.user_id == user_id,
                CompetitorAlert.product_id.in_(product_ids),
                CompetitorAlert.is_read == False,
            ).all()
            existing_alerts = {(r.product_id, r.competitor_nm_id) for r in rows}

        # ── Шаг 4: обрабатываем результаты ─────────────────────────────────────
        alerts_created = 0

        for product in products:
            config = product.config
            if not config:
                continue
            nm_ids = product_competitor_map.get(product.id, [])

            for nm_id in nm_ids:
                try:
                    # Пропускаем если уже есть непрочитанный алерт — дедупликация
                    if (product.id, nm_id) in existing_alerts:
                        continue

                    parsed = batch_results.get(nm_id)
                    if not parsed:
                        continue

                    current_price = Decimal(str(parsed.final_price))

                    # Последний снэпшот с ценой этого конкурента
                    last_snap = (
                        db.query(PriceSnapshot)
                        .filter(
                            PriceSnapshot.product_id == product.id,
                            PriceSnapshot.competitor_nm_id == nm_id,
                            PriceSnapshot.competitor_price.isnot(None),
                        )
                        .order_by(desc(PriceSnapshot.snapshot_at))
                        .first()
                    )

                    if not last_snap or not last_snap.competitor_price:
                        continue

                    old_price = last_snap.competitor_price
                    if old_price == 0:
                        continue

                    change_pct = float((current_price - old_price) / old_price * 100)

                    if abs(change_pct) >= threshold:
                        alert = CompetitorAlert(
                            user_id=user_id,
                            product_id=product.id,
                            competitor_nm_id=nm_id,
                            old_price=old_price,
                            new_price=current_price,
                            change_pct=round(Decimal(str(change_pct)), 1),
                        )
                        db.add(alert)
                        alerts_created += 1
                        # Помечаем как уже созданный чтобы не дублировать внутри этого прогона
                        existing_alerts.add((product.id, nm_id))

                except Exception as e:
                    logger.warning(f"[CompMonitor] Failed to check competitor {nm_id}: {e}")
                    continue

        db.commit()
        logger.info(f"[CompMonitor] user={user_id}: monitored {len(products)} products, {alerts_created} alerts")
        return {"status": "ok", "monitored": len(products), "alerts": alerts_created}

    except Exception as exc:
        db.rollback()
        logger.error(f"[CompMonitor] user={user_id} failed: {exc}", exc_info=True)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()
