import logging
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.wb_catalog import WBCatalog

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="products.sync_from_catalog",
    max_retries=3,
    default_retry_delay=120,
    time_limit=600,
)
def sync_products_from_catalog_task(self, account_id: int):
    """
    Создаёт товары в таблице products из wb_catalog (без вызова WB API).
    Не удаляет существующие товары — только добавляет недостающие.
    """
    from app.models.account import Account
    from app.models.product import Product

    db = SessionLocal()
    try:
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.is_active == True,
        ).first()

        if not account:
            logger.warning(f"[products.sync] account_id={account_id} не найден или неактивен")
            return {"status": "skipped", "reason": "account not found"}

        logger.info(f"[products.sync] Начало для account_id={account_id} ({account.name})")

        # Backfill имён для уже существующих товаров с placeholder-именами
        # (products_sync больше НЕ создаёт новые товары — только обновляет имена)
        try:
            from sqlalchemy import text as sa_text
            name_updated = db.execute(sa_text("""
                UPDATE products p
                SET name = c.name
                FROM wb_catalog c
                WHERE p.account_id = c.account_id
                  AND p.nm_id = c.nm_id
                  AND (p.name IS NULL OR p.name LIKE 'Товар %' OR p.name = p.seller_sku)
                  AND c.name IS NOT NULL AND c.name != ''
            """)).rowcount
            if name_updated:
                db.commit()
                logger.info(f"[products.sync] account_id={account_id}: backfill имён для {name_updated} товаров")
        except Exception as e:
            logger.warning(f"[products.sync] backfill names failed: {e}")
            db.rollback()

        logger.info(f"[products.sync] account_id={account_id}: синхронизация имён завершена")
        return {"status": "ok"}

    except Exception as exc:
        db.rollback()
        logger.error(f"[products.sync] account_id={account_id}: ошибка — {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="products.sync_all",
    time_limit=120,
)
def sync_all_products_task():
    """Запускает sync_products_from_catalog_task для всех активных аккаунтов."""
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
            sync_products_from_catalog_task.delay(account_id)
            dispatched += 1
        except Exception as e:
            logger.error(f"[products.sync_all] account_id={account_id}: {e}")

    logger.info(f"[products.sync_all] поставлено задач: {dispatched}")
    return {"dispatched": dispatched}


@celery_app.task(
    bind=True,
    name="products.import_from_file",
    max_retries=0,
    time_limit=300,
)
def import_products_task(self, file_bytes_b64: str, user_id: int):
    """Фоновый импорт товаров из Excel (base64-encoded bytes)."""
    import base64
    from app.database import SessionLocal
    from app.models.account import Account
    from app.models.product import Product
    from app.utils.excel import ExcelImporter
    import logging

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        file_bytes = base64.b64decode(file_bytes_b64)
        excel_importer = ExcelImporter()
        products_data = excel_importer.import_products(file_bytes)

        results = {"created": 0, "skipped": 0, "errors": []}

        # Кэш аккаунтов
        account_cache = {}
        def _get_account(account_id):
            if account_id not in account_cache:
                account_cache[account_id] = (
                    db.query(Account)
                    .filter(Account.id == account_id, Account.user_id == user_id, Account.is_active == True)
                    .first()
                )
            return account_cache[account_id]

        # Группируем нужные SKU по аккаунту для батч-запроса
        needed_skus = {}  # acc_id → set of vendor_codes
        for pd in products_data:
            if pd.get("seller_sku") and not pd.get("nm_id") and pd.get("account_id"):
                needed_skus.setdefault(pd["account_id"], set()).add(pd["seller_sku"])

        # Загружаем только нужные записи каталога (не все 36к!)
        catalog_by_sku = {}
        catalog_image = {}
        catalog_by_nm = {}
        for acc_id, skus in needed_skus.items():
            if not _get_account(acc_id):
                continue
            rows = db.query(WBCatalog).filter(
                WBCatalog.account_id == acc_id,
                WBCatalog.vendor_code.in_(list(skus))
            ).all()
            catalog_by_sku[acc_id] = {r.vendor_code: r.nm_id for r in rows}
            catalog_by_nm[acc_id] = {r.nm_id: r.vendor_code for r in rows}
            catalog_image[acc_id] = {r.nm_id: r.image_url for r in rows if r.image_url}

        for product_data in products_data:
            nm_id = product_data.get("nm_id")
            seller_sku = product_data.get("seller_sku")
            account_id = product_data.get("account_id")
            name = product_data.get("name") or ""
            label = f"nmID={nm_id}" if nm_id else f"артикул={seller_sku!r}"

            if not account_id:
                results["errors"].append(f"{label}: не указан ID аккаунта")
                continue

            account = _get_account(account_id)
            if not account:
                results["errors"].append(f"{label}: аккаунт {account_id} не найден")
                continue

            if not nm_id and seller_sku:
                nm_id = catalog_by_sku.get(account_id, {}).get(seller_sku)
                if not nm_id:
                    results["errors"].append(
                        f"артикул={seller_sku!r}: не найден в каталоге WB. Сначала выполните синхронизацию каталога в настройках аккаунта"
                    )
                    continue

            if nm_id and not seller_sku:
                seller_sku = catalog_by_nm.get(account_id, {}).get(nm_id) or str(nm_id)

            existing = db.query(Product).filter(
                Product.nm_id == nm_id,
                Product.account_id == account_id,
            ).first()
            if existing:
                results["skipped"] += 1
                continue

            db.add(Product(
                nm_id=nm_id,
                account_id=account_id,
                seller_sku=seller_sku or str(nm_id),
                name=name or seller_sku or str(nm_id),
                image_url=catalog_image.get(account_id, {}).get(nm_id),
            ))
            results["created"] += 1

        if results["created"] > 0:
            db.commit()

        logger.info(f"[import_products] user={user_id}: создано={results['created']} пропущено={results['skipped']} ошибок={len(results['errors'])}")
        return results

    except Exception as exc:
        db.rollback()
        logger.error(f"[import_products] user={user_id}: {exc}", exc_info=True)
        raise
    finally:
        db.close()
