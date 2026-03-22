import asyncio
import logging
from typing import Optional, Dict, Any
from decimal import Decimal
import httpx
from app.config import settings
from app.services.proxy_pool import proxy_pool, ProxyConfig

logger = logging.getLogger(__name__)


class CatalogParseError(Exception):
    """Ошибка парсинга каталога"""
    pass


class ProductData:
    """Данные товара из каталога WB"""

    def __init__(self, raw_data: dict):
        self.raw = raw_data
        self.nm_id = raw_data.get('id')
        self.name = raw_data.get('name', '')

        # Парсинг цены (цены в копейках → рубли)
        sizes = raw_data.get('sizes', [])
        if sizes:
            price_data = sizes[0].get('price', {})
            self.seller_price = Decimal(price_data.get('basic', 0)) / 100
            self.final_price = Decimal(price_data.get('product', 0)) / 100
        else:
            self.seller_price = Decimal(0)
            self.final_price = Decimal(0)

        # Остатки
        self.total_quantity = raw_data.get('totalQuantity', 0)
        self.in_stock = self.total_quantity > 0

        # Промо-акции
        self.promotions = raw_data.get('promotions', [])
        self.is_in_promo = len(self.promotions) > 0

        # СПП
        self.spp = self._calculate_spp()

    def _calculate_spp(self) -> Optional[Decimal]:
        """
        Расчет СПП из соотношения цен:
        spp = (1 - final_price / seller_price) * 100
        """
        if self.seller_price > 0 and self.final_price > 0:
            spp = (1 - self.final_price / self.seller_price) * 100
            return round(spp, 2)
        return None

    def to_dict(self) -> dict:
        return {
            'nm_id': self.nm_id,
            'name': self.name,
            'seller_price': float(self.seller_price),
            'final_price': float(self.final_price),
            'total_quantity': self.total_quantity,
            'in_stock': self.in_stock,
            'is_in_promo': self.is_in_promo,
            'spp': float(self.spp) if self.spp else None,
            'promotions': self.promotions,
        }

    def __repr__(self):
        return (
            f"<Product nm={self.nm_id} seller={self.seller_price}₽ "
            f"final={self.final_price}₽ stock={self.total_quantity}>"
        )


_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}


class CatalogParser:
    """
    Парсер каталога WB.
    Пытается получить данные через прокси (если настроены),
    при отсутствии прокси делает прямой запрос.
    """

    def __init__(self):
        self.max_retries = settings.RETRY_ATTEMPTS

    def _build_url(self, nm_id: int) -> str:
        """
        URL для получения данных товара.
        card.wb.ru — публичный API WB, не требует куки/авторизации.
        """
        base = settings.WB_CATALOG_BASE_URL  # https://card.wb.ru
        return (
            f"{base}/cards/v4/detail"
            f"?appType=1&curr=rub&dest=-1257786&nm={nm_id}"
        )

    def _build_batch_url(self, nm_ids: list[int]) -> str:
        """URL для батчевого запроса — WB принимает nm=id1;id2;id3 (до 100 шт.)."""
        base = settings.WB_CATALOG_BASE_URL
        nm_param = ";".join(str(n) for n in nm_ids)
        return f"{base}/cards/v4/detail?appType=1&curr=rub&dest=-1257786&nm={nm_param}"

    @staticmethod
    def _extract_products(data: dict) -> list:
        """
        Поддерживает обе структуры ответа:
        - {"products": [...]}          (wildberries.ru/__internal)
        - {"data": {"products": [...]}} (card.wb.ru — старый формат)
        """
        if 'products' in data:
            return data['products']
        return data.get('data', {}).get('products', [])

    async def _request_with_proxy(self, url: str, proxy: ProxyConfig) -> Optional[dict]:
        """Запрос через прокси"""
        try:
            async with httpx.AsyncClient(
                proxies=proxy.url,
                timeout=15,
                follow_redirects=True,
                headers=_HEADERS,
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 403:
                    logger.warning(f"403 через прокси {proxy} (капча?)")
                    proxy_pool.mark_as_dead(proxy)
                else:
                    logger.warning(f"Код {response.status_code} через прокси {proxy}")
                return None
        except httpx.TimeoutException:
            logger.warning(f"Timeout через прокси {proxy}")
            proxy_pool.mark_as_dead(proxy)
            return None
        except httpx.ProxyError as e:
            logger.warning(f"Ошибка прокси {proxy}: {e}")
            proxy_pool.mark_as_dead(proxy)
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка через {proxy}: {e}")
            return None

    async def _request_direct(self, url: str) -> Optional[dict]:
        """Прямой запрос без прокси"""
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers=_HEADERS,
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Прямой запрос: код {response.status_code} для {url}")
                return None
        except Exception as e:
            logger.error(f"Ошибка прямого запроса: {e}")
            return None

    async def parse_product(self, nm_id: int) -> Optional[ProductData]:
        """
        Парсинг товара. Пытается через прокси (если есть),
        иначе — напрямую.
        """
        url = self._build_url(nm_id)
        logger.info(f"Парсинг товара nm_id={nm_id}")

        proxies_available = proxy_pool._initialized and proxy_pool.get_stats()['alive'] > 0

        if proxies_available:
            # Пробуем через прокси
            for attempt in range(1, self.max_retries + 1):
                proxy = await proxy_pool.get_next_proxy()
                if not proxy:
                    break
                logger.debug(f"Попытка {attempt}/{self.max_retries} через {proxy}")
                data = await self._request_with_proxy(url, proxy)
                if data is not None:
                    return self._parse_response(nm_id, data)
                await asyncio.sleep(0.5)

            logger.warning(f"nm_id={nm_id}: все прокси не сработали, пробуем напрямую")

        # Прямой запрос (когда нет прокси или они все упали)
        data = await self._request_direct(url)
        if data is None:
            logger.error(f"nm_id={nm_id}: не удалось получить данные ни через прокси, ни напрямую")
            return None
        return self._parse_response(nm_id, data)

    def _parse_response(self, nm_id: int, data: dict) -> Optional[ProductData]:
        """Парсинг JSON-ответа каталога"""
        try:
            products = self._extract_products(data)
            if not products:
                logger.warning(f"nm_id={nm_id} не найден в каталоге")
                return None
            product_data = ProductData(products[0])
            logger.info(f"✓ Спарсен: {product_data}")
            return product_data
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"Ошибка парсинга данных nm_id={nm_id}: {e}")
            return None

    async def parse_batch_ids(
        self,
        nm_ids: list[int],
        chunk_size: int = 100,
        delay_between_chunks: float = 0.5,
    ) -> Dict[int, Optional[ProductData]]:
        """
        Батчевый парсинг: объединяет nm_ids в чанки по chunk_size и делает
        один HTTP-запрос на чанк вместо N отдельных. Резко снижает RPS на WB API.
        """
        if not nm_ids:
            return {}

        logger.info(f"Батч-парсинг {len(nm_ids)} товаров (чанки по {chunk_size})")
        result: Dict[int, Optional[ProductData]] = {}

        chunks = [nm_ids[i:i + chunk_size] for i in range(0, len(nm_ids), chunk_size)]
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(delay_between_chunks)

            url = self._build_batch_url(chunk)
            data = None

            proxies_available = proxy_pool._initialized and proxy_pool.get_stats()['alive'] > 0
            if proxies_available:
                for attempt in range(1, self.max_retries + 1):
                    proxy = await proxy_pool.get_next_proxy()
                    if not proxy:
                        break
                    data = await self._request_with_proxy(url, proxy)
                    if data is not None:
                        break
                    await asyncio.sleep(0.5)

            if data is None:
                data = await self._request_direct(url)

            if data is None:
                logger.error(f"Батч чанк {idx + 1}/{len(chunks)}: не удалось получить данные")
                for nm_id in chunk:
                    result[nm_id] = None
                continue

            products_raw = self._extract_products(data)
            products_map = {p_raw.get('id'): p_raw for p_raw in products_raw}
            for nm_id in chunk:
                raw = products_map.get(nm_id)
                if raw:
                    try:
                        result[nm_id] = ProductData(raw)
                    except Exception as e:
                        logger.error(f"Ошибка парсинга nm_id={nm_id} из батча: {e}")
                        result[nm_id] = None
                else:
                    result[nm_id] = None

        success = sum(1 for v in result.values() if v is not None)
        logger.info(f"Батч-парсинг завершён: {success}/{len(nm_ids)} успешно")
        return result

    async def parse_multiple(self, nm_ids: list[int]) -> Dict[int, Optional[ProductData]]:
        """Последовательный парсинг товаров — каждый через следующий прокси по кругу"""
        logger.info(f"Парсинг {len(nm_ids)} товаров (последовательно)")
        parsed_data = {}
        for nm_id in nm_ids:
            try:
                result = await self.parse_product(nm_id)
                parsed_data[nm_id] = result
            except Exception as e:
                logger.error(f"Ошибка парсинга nm_id={nm_id}: {e}")
                parsed_data[nm_id] = None

        success = sum(1 for v in parsed_data.values() if v is not None)
        logger.info(f"Парсинг завершен: {success}/{len(nm_ids)} успешно")
        return parsed_data


# Singleton
catalog_parser = CatalogParser()
