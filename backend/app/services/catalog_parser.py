import os
import re
import asyncio
import logging
import time
from typing import Optional, Dict, Any
from decimal import Decimal
import httpx
from curl_cffi.requests import AsyncSession
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

        # Парсинг цены (цены в копейках → рубли). Раньше бралась только
        # sizes[0]: у распроданного первого размера нет цены → final=0, и у
        # конкурента «в наличии» оказывалась цена 0. Берём самый дешёвый размер
        # с ценой, из размеров с остатком — если такие есть.
        sizes = raw_data.get('sizes') or []
        priced = [sz for sz in sizes if (sz.get('price') or {}).get('product')]
        in_stock = [
            sz for sz in priced
            if any((st.get('qty') or 0) > 0 for st in (sz.get('stocks') or []))
        ]
        chosen = min(in_stock or priced, key=lambda sz: sz['price']['product']) if priced else None
        if chosen:
            price_data = chosen.get('price', {})
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
            'spp': float(self.spp) if self.spp is not None else None,
            'promotions': self.promotions,
        }

    def __repr__(self):
        return (
            f"<Product nm={self.nm_id} seller={self.seller_price}₽ "
            f"final={self.final_price}₽ stock={self.total_quantity}>"
        )


# Сессия WB для каталога собирается настоящим браузером (~/wb-auth, Playwright)
# и лежит в request.txt в виде curl-команды. Антибот WB (x_wbaas_token)
# привязывает сессию к браузеру, который её получил, поэтому запросы должны
# выглядеть так же: те же заголовки и куки, тот же User-Agent и похожий
# TLS-отпечаток. Раньше к ним подмешивались зашитые в код User-Agent Edge 153,
# токен покупательского аккаунта и 9 кук из июльской сессии другого браузера —
# WB отзывал сессию через 15-30 минут.
_FALLBACK_HEADERS = {
    "accept": "*/*",
    "accept-language": "ru-RU,ru;q=0.9",
    "referer": "https://www.wildberries.ru/",
    "x-requested-with": "XMLHttpRequest",
}
# Заголовки из curl-команды, которые задаёт сама библиотека
_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "cookie"}
_DEFAULT_IMPERSONATE = "chrome124"


AUTH_REQUEST_PATHS = [
    "/app/wb-auth/request.txt",
    "/home/ivanz/wb-auth/request.txt",
    "/app/wb_auth_request.txt",
]


def load_auth_session() -> tuple[dict, dict]:
    """Куки и заголовки браузерной сессии из wb-auth (без подмешивания своих)."""
    for p in AUTH_REQUEST_PATHS:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            logger.warning("Ошибка чтения %s: %s", p, e)
            continue
        headers: Dict[str, str] = {}
        cookies: Dict[str, str] = {}
        for name, value in re.findall(r"-H '([^:']+):\s*([^']*)'", text):
            name = name.strip().lower()
            if name == "cookie":
                cookies.update(
                    dict(item.split("=", 1) for item in value.split("; ") if "=" in item)
                )
            elif name not in _SKIP_HEADERS:
                headers[name] = value
        for name, value in _FALLBACK_HEADERS.items():
            headers.setdefault(name, value)
        return cookies, headers
    logger.warning("Файл сессии WB (request.txt) не найден — каталог, скорее всего, ответит 498")
    return {}, dict(_FALLBACK_HEADERS)


def get_cookies() -> dict:
    """Возвращает куки для каталога"""
    raw = getattr(settings, 'WB_CATALOG_COOKIES', '')
    if raw:
        try:
            return dict(item.split("=", 1) for item in raw.split("; ") if "=" in item)
        except Exception:
            pass
    cookies, _ = load_auth_session()
    return cookies


def get_headers() -> dict:
    """Возвращает заголовки для каталога"""
    _, headers = load_auth_session()
    return headers


def _supported_chrome_versions() -> list[int]:
    try:
        from curl_cffi.requests import BrowserType
        return sorted(
            int(m.group(1))
            for b in BrowserType
            if (m := re.fullmatch(r"chrome(\d+)", b.value))
        )
    except Exception:
        return [124]


def get_impersonate(headers: Optional[dict] = None) -> str:
    """TLS-отпечаток под версию Chrome из User-Agent сессии (не новее доступной)."""
    ua = (headers or get_headers()).get("user-agent", "")
    m = re.search(r"Chrome/(\d+)", ua)
    versions = _supported_chrome_versions()
    if not m or not versions:
        return _DEFAULT_IMPERSONATE
    major = int(m.group(1))
    fitting = [v for v in versions if v <= major]
    return f"chrome{max(fitting) if fitting else min(versions)}"


class CatalogParser:
    """
    Парсер каталога WB.
    Пытается получить данные через CATALOG_PROXY_URL, затем через пул SOCKS5,
    при отсутствии — напрямую.
    """

    _BACKOFF_STEPS = [5 * 60, 15 * 60, 30 * 60]

    def __init__(self):
        self.max_retries = settings.RETRY_ATTEMPTS
        self._blocked_until = 0.0
        self._last_block_status: Optional[int] = None
        self._consecutive_blocks = 0

    def _build_url(self, nm_id: int) -> str:
        base = settings.WB_CATALOG_BASE_URL.rstrip('/')
        return (
            f"{base}/cards/v4/detail"
            f"?appType=1&curr=rub&dest=-4039466&nm={nm_id}"
        )

    def _build_batch_url(self, nm_ids: list[int]) -> str:
        base = settings.WB_CATALOG_BASE_URL.rstrip('/')
        nm_param = ";".join(str(n) for n in nm_ids)
        return f"{base}/cards/v4/detail?appType=1&curr=rub&dest=-4039466&nm={nm_param}"

    @staticmethod
    def _extract_products(data: dict) -> list:
        if 'products' in data:
            return data['products']
        return data.get('data', {}).get('products', [])

    async def _request_via_catalog_proxy(self, url: str) -> Optional[dict]:
        proxy_url = settings.CATALOG_PROXY_URL
        if not proxy_url:
            return None
        try:
            async with AsyncSession(
                timeout=15,
                headers=(_h := get_headers()),
                cookies=get_cookies(),
                impersonate=get_impersonate(_h),
                proxy=proxy_url,
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"Catalog proxy: код {response.status_code} для {url}")
                return None
        except Exception as e:
            logger.warning(f"Catalog proxy ({proxy_url}): ошибка {e}")
            return None

    async def _request_with_proxy(self, url: str, proxy: ProxyConfig) -> Optional[dict]:
        try:
            async with AsyncSession(
                timeout=15,
                headers=(_h := get_headers()),
                cookies=get_cookies(),
                impersonate=get_impersonate(_h),
                proxy=proxy.url,
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Код {response.status_code} через прокси {proxy}")
                return None
        except Exception as e:
            logger.warning(f"Ошибка прокси {proxy}: {e}")
            proxy_pool.mark_as_dead(proxy)
            return None

    async def _request_direct(self, url: str) -> Optional[dict]:
        import random
        now = time.monotonic()
        if now < self._blocked_until:
            return None
        await asyncio.sleep(random.uniform(0.05, 0.15))
        try:
            async with AsyncSession(
                timeout=10,
                headers=(_h := get_headers()),
                cookies=get_cookies(),
                impersonate=get_impersonate(_h),
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    self._consecutive_blocks = 0
                    return response.json()
                if response.status_code in (403, 429):
                    idx = min(self._consecutive_blocks, len(self._BACKOFF_STEPS) - 1)
                    backoff = self._BACKOFF_STEPS[idx]
                    self._consecutive_blocks += 1
                    self._blocked_until = time.monotonic() + backoff
                    self._last_block_status = response.status_code
                    return None
                return None
        except Exception as e:
            logger.error(f"Ошибка прямого запроса: {e}")
            return None

    async def parse_product(self, nm_id: int) -> Optional[ProductData]:
        url = self._build_url(nm_id)
        data = await self._request_via_catalog_proxy(url)
        if data is None:
            data = await self._request_direct(url)
        if data is None:
            for attempt in range(self.max_retries):
                proxy = await proxy_pool.get_next_proxy()
                if proxy is None:
                    break
                data = await self._request_with_proxy(url, proxy)
                if data is not None:
                    break
        if data is None:
            logger.error(f"nm_id={nm_id}: не удалось получить данные")
            return None
        return self._parse_response(nm_id, data)

    def _parse_response(self, nm_id: int, data: dict) -> Optional[ProductData]:
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
        concurrency: int = 2,
    ) -> Dict[int, Optional[ProductData]]:
        if not nm_ids:
            return {}

        logger.info(f"Батч-парсинг {len(nm_ids)} товаров (чанки по {chunk_size}, параллельно {concurrency})")
        chunks = [nm_ids[i:i + chunk_size] for i in range(0, len(nm_ids), chunk_size)]
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_chunk(chunk: list[int]) -> Dict[int, Optional[ProductData]]:
            async with semaphore:
                # Браузер не шлёт десятки запросов разом: 2 параллельно и пауза
                # 0.3-0.8 с между запросами, иначе антибот WB быстро отзывает сессию.
                import random
                await asyncio.sleep(delay_between_chunks * random.uniform(0.6, 1.6))
                url = self._build_batch_url(chunk)
                data = None

                data = await self._request_via_catalog_proxy(url)
                if data is None:
                    data = await self._request_direct(url)
                if data is None:
                    for _attempt in range(3):
                        proxy = await proxy_pool.get_next_proxy()
                        if proxy is None:
                            break
                        data = await self._request_with_proxy(url, proxy)
                        if data is not None:
                            break

                chunk_result: Dict[int, Optional[ProductData]] = {}
                if data is None:
                    for nm_id in chunk:
                        chunk_result[nm_id] = None
                    return chunk_result

                products_raw = self._extract_products(data)
                products_map = {p_raw.get('id'): p_raw for p_raw in products_raw}
                for nm_id in chunk:
                    raw = products_map.get(nm_id)
                    if raw:
                        try:
                            chunk_result[nm_id] = ProductData(raw)
                        except Exception as e:
                            logger.error(f"Ошибка парсинга nm_id={nm_id} из батча: {e}")
                            chunk_result[nm_id] = None
                    else:
                        chunk_result[nm_id] = None
                return chunk_result

        result: Dict[int, Optional[ProductData]] = {}

        # Раунд 1: основной парсинг
        chunk_results = await asyncio.gather(*[fetch_chunk(c) for c in chunks])
        for cr in chunk_results:
            result.update(cr)

        # Раунд 2-3: retry неудавшихся чанков
        for retry_round in range(2):
            failed_ids = [nm for nm, v in result.items() if v is None]
            if not failed_ids:
                break
            retry_chunks = [failed_ids[i:i + chunk_size] for i in range(0, len(failed_ids), chunk_size)]
            logger.info(f"Retry раунд {retry_round + 1}: {len(failed_ids)} неудавшихся товаров ({len(retry_chunks)} чанков)")
            await asyncio.sleep(3)
            retry_results = await asyncio.gather(*[fetch_chunk(c) for c in retry_chunks])
            for cr in retry_results:
                for nm_id, data in cr.items():
                    if data is not None:
                        result[nm_id] = data

        success = sum(1 for v in result.values() if v is not None)
        logger.info(f"Батч-парсинг завершён: {success}/{len(nm_ids)} успешно")
        return result

    async def parse_multiple(self, nm_ids: list[int]) -> Dict[int, Optional[ProductData]]:
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
