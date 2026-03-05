"""
Скрипт для тестирования парсера и прокси пула

Usage:
    python test_services.py
"""
import asyncio
import logging
from app.services.proxy_pool import proxy_pool
from app.services.catalog_parser import catalog_parser
from app.services.wb_api import create_wb_client
from app.config import settings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_proxy_pool():
    """Тест пула прокси"""
    logger.info("=" * 50)
    logger.info("ТЕСТ: Proxy Pool")
    logger.info("=" * 50)
    
    # Инициализация
    await proxy_pool.initialize()
    
    # Статистика
    stats = proxy_pool.get_stats()
    logger.info(f"Статистика прокси: {stats}")
    
    # Получение нескольких прокси
    for i in range(5):
        proxy = await proxy_pool.get_next_proxy()
        if proxy:
            logger.info(f"Прокси #{i+1}: {proxy}")
        else:
            logger.error(f"Не удалось получить прокси #{i+1}")


async def test_catalog_parser():
    """Тест парсера каталога"""
    logger.info("=" * 50)
    logger.info("ТЕСТ: Catalog Parser")
    logger.info("=" * 50)
    
    # Инициализация прокси
    await proxy_pool.initialize()
    
    # Тестовые nmID (реальные товары с WB)
    test_nm_ids = [
        431871218,  # Книга (из примера в ТЗ)
        # Можно добавить свои nmID для тестирования
    ]
    
    for nm_id in test_nm_ids:
        try:
            logger.info(f"\nПарсинг товара nm_id={nm_id}...")
            product = await catalog_parser.parse_product(nm_id)
            
            if product:
                logger.info(f"✓ Успешно: {product}")
                logger.info(f"  Название: {product.name}")
                logger.info(f"  Цена продавца: {product.seller_price}₽")
                logger.info(f"  Финальная цена: {product.final_price}₽")
                logger.info(f"  СПП: {product.spp}%")
                logger.info(f"  В наличии: {product.in_stock} (остаток: {product.total_quantity})")
                logger.info(f"  В промо: {product.is_in_promo}")
            else:
                logger.warning(f"Товар nm_id={nm_id} не найден")
                
        except Exception as e:
            logger.error(f"✗ Ошибка парсинга nm_id={nm_id}: {e}")


async def test_wb_api():
    """Тест WB API клиента"""
    logger.info("=" * 50)
    logger.info("ТЕСТ: WB API Client")
    logger.info("=" * 50)
    
    # Для тестирования нужен реальный токен
    # Раскомментируйте и вставьте свой токен
    # test_token = "your_wb_api_token_here"
    
    logger.warning("Тест WB API пропущен - требуется реальный токен")
    logger.info("Для тестирования раскомментируйте код и укажите токен")
    
    # Пример использования:
    # async with create_wb_client(test_token) as client:
    #     # Получение цен
    #     prices = await client.get_prices(nm_ids=[431871218])
    #     for price in prices:
    #         logger.info(f"Цена из API: {price}")


async def test_parallel_parsing():
    """Тест параллельного парсинга нескольких товаров"""
    logger.info("=" * 50)
    logger.info("ТЕСТ: Параллельный парсинг")
    logger.info("=" * 50)
    
    await proxy_pool.initialize()
    
    # Несколько товаров для парсинга
    nm_ids = [
        431871218,  # Книга
        # Добавьте больше nmID для тестирования
    ]
    
    logger.info(f"Запускаем параллельный парсинг {len(nm_ids)} товаров...")
    results = await catalog_parser.parse_multiple(nm_ids)
    
    logger.info("\nРезультаты:")
    for nm_id, product in results.items():
        if product:
            logger.info(f"  ✓ nm_id={nm_id}: {product.name[:50]}... ({product.final_price}₽)")
        else:
            logger.warning(f"  ✗ nm_id={nm_id}: не удалось спарсить")


async def main():
    """Запуск всех тестов"""
    try:
        # 1. Тест прокси пула
        await test_proxy_pool()
        
        # 2. Тест парсера
        await test_catalog_parser()
        
        # 3. Тест WB API
        await test_wb_api()
        
        # 4. Тест параллельного парсинга
        await test_parallel_parsing()
        
        logger.info("=" * 50)
        logger.info("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"Критическая ошибка в тестах: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
