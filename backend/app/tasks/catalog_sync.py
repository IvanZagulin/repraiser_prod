import asyncio
import logging
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="catalog.sync_account",
    max_retries=3,
    default_retry_delay=60,
    time_limit=600,   # 10 минут максимум на один аккаунт
)
def sync_wb_catalog_task(self, account_id: int):
    """
    Скачивает ВСЕ карточки WB для аккаунта через cursor-пагинацию
    и сохраняет только nmID + vendorCode в таблицу wb_catalog.
    Вызывается при создании/обновлении аккаунта и раз в неделю.
    """
    from app.models.account import Account
    from app.models.wb_catalog import WBCatalog
    from app.services.wb_api import create_wb_client

    db = SessionLocal()
    try:
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.is_active == True,
        ).first()

        if not account:
            logger.warning(f"[catalog.sync] account_id={account_id} не найден или неактивен")
            return {"status": "skipped", "reason": "account not found"}

        logger.info(f"[catalog.sync] Начало синхронизации account_id={account_id} ({account.name})")

        wb = create_wb_client(account.wb_api_token)
        all_cards = asyncio.run(wb.get_all_cards())

        if not all_cards:
            logger.warning(f"[catalog.sync] account_id={account_id}: карточки не найдены")
            return {"status": "ok", "synced": 0}

        # Полная замена: удаляем старые записи, вставляем новые
        deleted = db.query(WBCatalog).filter(WBCatalog.account_id == account_id).delete()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for vendor_code, info in all_cards.items():
            nm_id = info.get("nm_id")
            if not nm_id:
                continue
            db.add(WBCatalog(
                account_id=account_id,
                nm_id=nm_id,
                vendor_code=vendor_code,
                image_url=info.get("image_url"),   # первое фото с WB Content API
                name=info.get("name") or None,
                synced_at=now,
            ))

        db.commit()
        count = len(all_cards)
        logger.info(f"[catalog.sync] account_id={account_id}: удалено {deleted}, добавлено {count} карточек")

        # Backfill image_url и name в products из только что синхронизированного каталога
        try:
            from sqlalchemy import text as sa_text
            updated = db.execute(sa_text("""
                UPDATE products p
                SET image_url = COALESCE(p.image_url, c.image_url),
                    name = CASE
                        WHEN (p.name IS NULL OR p.name LIKE 'Товар %' OR p.name = p.seller_sku) AND c.name IS NOT NULL AND c.name != ''
                        THEN c.name
                        ELSE p.name
                    END
                FROM wb_catalog c
                WHERE p.account_id = c.account_id
                  AND p.nm_id = c.nm_id
                  AND (p.image_url IS NULL OR p.name IS NULL OR p.name LIKE 'Товар %' OR p.name = p.seller_sku)
            """)).rowcount
            db.commit()
            if updated:
                logger.info(f"[catalog.sync] account_id={account_id}: обновлено image_url/name у {updated} товаров")
        except Exception as e:
            logger.warning(f"[catalog.sync] backfill image_url/name failed: {e}")
            db.rollback()

        # Targeted-backfill: дозапрашиваем по nm_id товары, у которых image_url всё ещё пустой.
        # Это нужно когда get_all_cards прервался по 429 раньше конца каталога.
        try:
            from app.models.product import Product
            from sqlalchemy import text as sa_text
            missing_rows = db.execute(sa_text("""
                SELECT p.id, p.nm_id FROM products p
                WHERE p.account_id = :acc AND p.image_url IS NULL AND p.nm_id IS NOT NULL
            """), {"acc": account_id}).fetchall()

            if missing_rows:
                logger.info(
                    f"[catalog.sync] account_id={account_id}: targeted-backfill для "
                    f"{len(missing_rows)} товаров без фото"
                )
                missing_nm_ids = [r.nm_id for r in missing_rows]
                # concurrency=3 чтобы не получить очередной 429
                batch_result = asyncio.run(wb.get_cards_batch(missing_nm_ids, concurrency=3))

                targeted_updated = 0
                # Оба UPDATE (products + wb_catalog) для одного товара должны
                # фиксироваться атомарно — используем один commit на весь batch.
                # При исключении внешний except откатит сессию.
                for product_id, nm_id in ((r.id, r.nm_id) for r in missing_rows):
                    info = batch_result.get(nm_id)
                    if not info:
                        continue
                    image_url = info.get("image_url")
                    if not image_url:
                        continue
                    # Обновляем product и wb_catalog в одной транзакции
                    db.execute(sa_text(
                        "UPDATE products SET image_url = :url WHERE id = :id"
                    ), {"url": image_url, "id": product_id})
                    db.execute(sa_text("""
                        INSERT INTO wb_catalog (account_id, nm_id, vendor_code, image_url, synced_at)
                        VALUES (:acc, :nm_id, :vc, :url, NOW())
                        ON CONFLICT (account_id, vendor_code)
                        DO UPDATE SET nm_id = :nm_id, image_url = :url, synced_at = NOW()
                    """), {
                        "acc": account_id,
                        "nm_id": nm_id,
                        "vc": info.get("seller_sku", str(nm_id)),
                        "url": image_url,
                    })
                    targeted_updated += 1

                # Один commit фиксирует все изменения атомарно
                if targeted_updated:
                    db.commit()
                    logger.info(
                        f"[catalog.sync] account_id={account_id}: "
                        f"targeted-backfill заполнил image_url у {targeted_updated} товаров"
                    )
                else:
                    logger.info(
                        f"[catalog.sync] account_id={account_id}: "
                        f"targeted-backfill: WB не вернул фото для оставшихся товаров"
                    )
        except Exception as e:
            logger.warning(f"[catalog.sync] targeted-backfill failed: {e}")
            db.rollback()

        return {"status": "ok", "synced": count}

    except Exception as exc:
        db.rollback()
        logger.error(f"[catalog.sync] account_id={account_id}: ошибка — {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="catalog.sync_all",
    time_limit=120,
)
def sync_all_wb_catalogs_task():
    """
    Запускает sync_wb_catalog_task для всех активных аккаунтов.
    Вызывается планировщиком раз в неделю.
    """
    from app.models.account import Account

    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.is_active == True).all()
        account_ids = [a.id for a in accounts]
    finally:
        db.close()

    dispatched = 0
    for account_id in account_ids:
        try:
            sync_wb_catalog_task.delay(account_id)
            dispatched += 1
        except Exception as e:
            logger.error(f"[catalog.sync_all] не удалось поставить задачу для account_id={account_id}: {e}")

    logger.info(f"[catalog.sync_all] поставлено задач: {dispatched}")
    return {"dispatched": dispatched}
