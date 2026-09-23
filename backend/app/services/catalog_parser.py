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


_HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7,ru-RU;q=0.6',
    'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2lkLndiLnJ1Iiwic3ViIjoiNDcwOTEyNjMiLCJhdWQiOlsiaHR0cHM6Ly93d3cud2lsZGJlcnJpZXMucnUiXSwiZXhwIjoxNzkyNTc1NjgzLCJuYmYiOjE3ODk5ODM2ODMsImlhdCI6MTc4OTk4MzY4MywidXNlciI6IjQ3MDkxMjYzIiwic2hhcmRfa2V5IjoiMTEiLCJjbGllbnRfaWQiOiJtYXJrZXRwbGFjZV93ZWIiLCJzZXNzaW9uX2lkIjoiODc2Y2I3MDM0NTIwNDE4ZmI4NjJkZWY0MzYxMGUwMzgiLCJ2YWxpZGF0aW9uX2tleSI6ImE1ZmFhNjgzMDM0ZDIxYmZlNzZhYWFkZTkyNzJhNDI3MGI3NGVjMWE2OTBhMjA1NWQ2YWJhODUyMjQ0ZDQ1NGQiLCJhcHBsaWNhdGlvbiI6IndiX21hcmtldHBsYWNlIiwic2NvcGUiOlsib3BlbmlkIiwicGhvbmUiLCJyZWFkOnByb2ZpbGUiLCJyZWFkOmVtYWlsIl0sInVzZXJfcmVnaXN0cmF0aW9uX2R0IjoxNjc0NzM3NDM1LCJ0eXBlIjoiYWNjZXNzX3Rva2VuIiwidmVyc2lvbiI6NX0.LNRiluE44Fu5T9rY_JOYZyYQlGul75supURvj9YmsgoL2_H2BWLaRUW_dXf0fj86hIusdBb4K-W3e38MBNKuwfQyxgE7UWXPV5fP2obZxyWFzjKpB2h6b5OtVJmhhnVn7CASC1PQVHZJMl4sdK1GoLN0SBe4PIpCfU6R4NIZkZdXh4sJYtPXZVZGyRa-qFVHzz-Qp9QELs-RVFDQvgLuLaTcysE6s8yMYeKswTLKEOtt-np2v1_8O-bOT_hRsGF-j0kCB7giGRx07vnOqmr1hOWxaOsLiQOKkjxNp8fJjSnriIZp9WWrYRYOC3vPtvYkdQHoCvyktc7e0-4HHMm_IQ',
    'deviceid': 'site_c1013e8f4fce4ae3b5cc9618547f1ef6',
    'priority': 'u=1, i',
    'sec-ch-ua': '"Microsoft Edge";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0',
    'Referer': 'https://www.wildberries.ru/',
    'Origin': 'https://www.wildberries.ru',
    'x-requested-with': 'XMLHttpRequest',
    'x-spa-version': '14.25.3',
}

_DEFAULT_COOKIES = {
    "external-locale": "ru",
    "_wbauid": "7866194451782997876",
    "_cp": "1",
    "device_id": "c3c5454a-f49c-4cfc-a2d7-e406034220ef",
    "feedbacks_link_accepted": "1",
    "routeb": "1784530343.631.373.438960|28979c6168ec4738f0fcb8539a6d5f12",
    "isAuthAccessToken": "true",
    "x_wbaas_token": "1.1000.9adaf7767ce0498dbcc50ddc8addc6b9.MTV8MmEwMzo2ZjAxOjE6Mjo6MjozYzU0fE1vemlsbGEvNS4wIChXaW5kb3dzIE5UIDEwLjA7IFdpbjY0OyB4NjQpIEFwcGxlV2ViS2l0LzUzNy4zNiAoS0hUTUwsIGxpa2UgR2Vja28pIENocm9tZS8xNTMuMC4wLjAgU2FmYXJpLzUzNy4zNiBFZGcvMTUzLjAuMC4wfDE3OTAyNDI4ODF8cmV1c2FibGV8MnxleUpvWVhOb0lqb2lJbjA9fDF8M3wxNzkwMTEzMjgxfDF8SnhjPQ==.MEUCIQDUnvmmlGBM2l02A3Xiv2WHjPYtckfAxB1Bv/lHvo4cDgIgUml7IECQ+wPPAxCQNWlZy+6HokxQ/beHdVT79hdwv14=",
    "wbx-validation-key": "e138f5f1-529e-425e-af77-55f8e99876d9",
    "x-supplier-id-external": "d32f8224-6682-4fb6-bd5e-057f076a969e",
    "__zzatw-wb": "MDA0dC0yYBwREFsKEH49WgsbSl1pCENQGC9LXz1uLWEPJ3wjYnwgGWsvC1RDMmUIPkBNOTM5NGZwVydgTmIfQ1pVfyUiFX1sH0FLVCNyM3dlaXceViUTFmcPRyJ1F0hAGxI6aCU6f1JpGWUzDldjGAsmVDVfP30sGRp4bSlScX9NfXY3PmJ+MQ9pOSRjCh9+OFoLDWk3XBQ8dWU+SHZzKUBtI19QXSRLUT9FbllGaXUVF0M8HHsNKkNtLToZUXYQQlh4cBpEN0AYfxVZUnUpbn06MBtFV08cThQldFpPelghRXt0WVYQPRdAcyYtX28iHV95FCFMR0lrZU5TQixmG3EVTQgNND1aciIPWzklWAgSPwsmIBp3ayhXDQtkQUdtbxt/Nl0cOWMRCxl+OmNdRkc3FSR7dSYKCTU3YnAvTCB7SykWRxsyYV5GaXUVCwoNGUVDb3cmQWslYURdVXVbSjNZHkZ0ayhWPQtiQ3J2eTJwVxlRDxZhDhYYRRcje0I3Yhk4QhgvPV8/YngiD2lIYChDVVIJKxoRenAlS3FPLH12X30beylOIA0lVBMhP05y5q2lDg==",
    "cfidsw-wb": "HwDUScMEm+mXXd4p306aoSQDkCiZVXC/GDkRNpymG6fqZK6DXi7z/xIQYivVqxPCLApM47xvMl6s+xn1rVB5MsudZH6F42TmRlSykL7vcbOiePnHBiWGYh3qjvQjRPkL6FmvVekvMppSHXUo/upDElcjz17Rwey3GC6ppH9nSw==",
}


AUTH_REQUEST_PATHS = [
    "/app/wb-auth/request.txt",
    "/home/ivanz/wb-auth/request.txt",
    "/app/wb_auth_request.txt",
]


def load_auth_session() -> tuple[dict, dict]:
    """Загружает свежие куки и заголовки из wb-auth файла сессии"""
    cookies = dict(_DEFAULT_COOKIES)
    headers = dict(_HEADERS)
    for p in AUTH_REQUEST_PATHS:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
                c_m = re.search(r"-H 'cookie:\s*([^']+)'", text)
                if c_m:
                    cookies.update(dict(item.split("=", 1) for item in c_m.group(1).split("; ") if "=" in item))
                for h in ['deviceid', 'user-agent', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform']:
                    m_h = re.search(r"-H '" + h + r":\s*([^']+)'", text, re.IGNORECASE)
                    if m_h:
                        headers[h] = m_h.group(1)
                break
            except Exception as e:
                logger.warning("Ошибка чтения %s: %s", p, e)
    return cookies, headers


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
                headers=get_headers(),
                cookies=get_cookies(),
                impersonate="chrome124",
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
                headers=get_headers(),
                cookies=get_cookies(),
                impersonate="chrome124",
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
                headers=get_headers(),
                cookies=get_cookies(),
                impersonate="chrome124",
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
        delay_between_chunks: float = 0.05,
        concurrency: int = 10,
    ) -> Dict[int, Optional[ProductData]]:
        if not nm_ids:
            return {}

        logger.info(f"Батч-парсинг {len(nm_ids)} товаров (чанки по {chunk_size}, параллельно {concurrency})")
        chunks = [nm_ids[i:i + chunk_size] for i in range(0, len(nm_ids), chunk_size)]
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_chunk(chunk: list[int]) -> Dict[int, Optional[ProductData]]:
            async with semaphore:
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
            await asyncio.sleep(1)
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
