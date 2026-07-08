import asyncio
from collections import deque
import logging
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
import time
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class _AsyncWBLimiter:
    def __init__(self, period: float, limit: int, interval: float, burst: int):
        self.period = period
        self.limit = limit
        self.interval = interval
        self.burst = burst
        self._timestamps = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.period:
                    self._timestamps.popleft()

                waits = []
                if len(self._timestamps) >= self.limit:
                    waits.append(self.period - (now - self._timestamps[0]))

                recent = [ts for ts in self._timestamps if now - ts < self.interval]
                if len(recent) >= self.burst:
                    waits.append(self.interval - (now - recent[0]))

                wait_for = max((delay for delay in waits if delay > 0), default=0)
                if wait_for <= 0:
                    self._timestamps.append(now)
                    return

            await asyncio.sleep(wait_for)


_LIMITS = {
    # WB docs: Prices and Discounts category, per seller account.
    "prices": {"period": 6.0, "limit": 10, "interval": 0.6, "burst": 5},
    # WB docs: Content category, per seller account.
    "content": {"period": 60.0, "limit": 100, "interval": 0.6, "burst": 5},
}
_limiters: Dict[Tuple[str, str], _AsyncWBLimiter] = {}


def _get_limiter(category: str, api_token: str) -> _AsyncWBLimiter:
    key = (category, api_token)
    if key not in _limiters:
        _limiters[key] = _AsyncWBLimiter(**_LIMITS[category])
    return _limiters[key]


class WBAPIError(Exception):
    """?????? WB API"""
    pass


class WBAuthError(WBAPIError):
    """401 ?? WB ? ????? ????????? ??? ???????. Caller ?????? ?????????????? ???????."""
    pass


class WBQuarantineError(WBAPIError):
    """WB ??????? ????? ? Price Quarantine. Caller ?????? ????????????? ????? ?? 24?."""
    def __init__(self, message: str, nm_id: int):
        super().__init__(message)
        self.nm_id = nm_id


class RateLimitError(WBAPIError):
    """?????? ?????????? ?????? ???????? (429)"""
    pass


class PartialCatalogError(WBAPIError):
    """??????? ???????? ?? ????????? ??-?? ??????? ?????? ??? 429"""
    def __init__(self, message: str, partial_cards: dict):
        super().__init__(message)
        self.partial_cards = partial_cards


class PriceData:
    """?????? ? ???? ?????? ?? WB Prices API"""

    def __init__(self, raw_data: dict):
        self.nm_id = raw_data.get('nmID')
        self.vendor_code = raw_data.get('vendorCode', '')

        # discounts-prices-api ?????????? ???? ? sizes[0], discount ? ?? ??????? ??????
        sizes = raw_data.get('sizes', [])
        if sizes:
            first_size = sizes[0]
            self.seller_price = Decimal(str(first_size.get('price', 0)))
            self.discounted_price = Decimal(str(first_size.get('discountedPrice', 0)))
        else:
            self.seller_price = Decimal(str(raw_data.get('price', 0)))
            self.discounted_price = Decimal(0)

        # discount ?????? ?? ??????? ?????? ??????? ?????? (?? ?????? sizes)
        self.discount = Decimal(str(raw_data.get('discount', 0)))

        self.sizes = sizes
        
    def to_dict(self) -> dict:
        return {
            'nm_id': self.nm_id,
            'seller_price': float(self.seller_price),
            'discounted_price': float(self.discounted_price),
            'discount': float(self.discount),
            'sizes': self.sizes
        }
    
    def __repr__(self):
        return f"<WBPrice nm={self.nm_id} price={self.seller_price}? discount={self.discount}%>"


class WBAPIClient:
    """
    ?????? ??? ?????? ? WB Prices API
    
    ????????????: https://openapi.wildberries.ru/prices/api/ru/
    """
    
    def __init__(self, api_token: str):
        """
        Args:
            api_token: JWT ????? WB API
        """
        self.base_url = settings.WB_API_BASE_URL
        self.api_token = api_token

    def _get_headers(self) -> dict:
        """???????????? ?????????? ??? ????????"""
        return {
            'Authorization': self.api_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    @staticmethod
    def _ratelimit_delay(response: httpx.Response, fallback_delay: int) -> int:
        """????? delay ?? X-Ratelimit-Retry/Reset, ???? WB ??? ???????."""
        for header_name in ("X-Ratelimit-Retry", "X-Ratelimit-Reset"):
            raw_value = response.headers.get(header_name)
            if not raw_value:
                continue
            try:
                parsed = int(float(raw_value))
            except ValueError:
                continue
            if parsed > 0:
                return parsed
        return fallback_delay

    async def _wait_for_limit(self, category: str) -> None:
        await _get_limiter(category, self.api_token).wait()
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        retry_on_429: bool = True,
        max_retries: int = 5,
        retry_on_5xx: bool = True,
    ) -> dict:
        """
        ?????????? HTTP ??????? ? ?????????? ??????.
        ?????? ????????? ? ?????????????? ??????????? ?????? ??????? ??????
        (fix: TCP socket leak ? persistent self.session ??? ?? ???????????).

        Args:
            method: HTTP ????? (GET, POST, etc)
            endpoint: Endpoint API
            json_data: JSON ?????? ??? POST
            params: Query ?????????
            retry_on_429: ????????? ?? ??? 429 ??????
            max_retries: ???????? ??????? ??? 429

        Returns:
            JSON ?????

        Raises:
            WBAPIError: ??? ??????? API
            RateLimitError: ??? ?????????? ??????
        """
        retry_count = 0
        backoff_delay = 7  # ????????? ???????? ??? 429 (> 6s window WB rate limit)
        transient_delay = 1.0

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._get_headers(),
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            while retry_count <= max_retries:
                try:
                    await self._wait_for_limit("prices")
                    response = await client.request(
                        method=method,
                        url=endpoint,
                        json=json_data,
                        params=params
                    )

                    # ????????? ????? ??????
                    if response.status_code in (200, 208):
                        return response.json()

                    elif response.status_code == 429:
                        # Rate limit exceeded
                        if not retry_on_429 or retry_count >= max_retries:
                            raise RateLimitError(f"Rate limit exceeded (429) ????? {retry_count} ???????")

                        delay = self._ratelimit_delay(response, backoff_delay)
                        logger.warning(
                            "Rate limit (429), ???? %ss ????? retry %s/%s "
                            "(retry=%s reset=%s remaining=%s)",
                            delay,
                            retry_count + 1,
                            max_retries,
                            response.headers.get("X-Ratelimit-Retry"),
                            response.headers.get("X-Ratelimit-Reset"),
                            response.headers.get("X-Ratelimit-Remaining"),
                        )
                        await asyncio.sleep(delay)
                        backoff_delay = max(backoff_delay * 2, delay)
                        retry_count += 1
                        continue

                    elif retry_on_5xx and response.status_code in (500, 502, 503, 504):
                        if retry_count >= max_retries:
                            raise WBAPIError(
                                f"API error {response.status_code} after "
                                f"{retry_count} retries: {response.text}"
                            )

                        logger.warning(
                            "WB API %s, waiting %.1fs before retry %s/%s",
                            response.status_code,
                            transient_delay,
                            retry_count + 1,
                            max_retries,
                        )
                        await asyncio.sleep(transient_delay)
                        transient_delay = min(transient_delay * 2, 16.0)
                        retry_count += 1
                        continue

                    elif response.status_code == 401:
                        raise WBAuthError("Unauthorized (401) - ????? ????????? ??? ??????? WB")

                    elif response.status_code == 400:
                        error_msg = response.text
                        raise WBAPIError(f"Bad Request (400): {error_msg}")

                    else:
                        raise WBAPIError(f"API ?????? ??? {response.status_code}: {response.text}")

                except httpx.TimeoutException:
                    if retry_count >= max_retries:
                        raise WBAPIError("Timeout при запросе к WB API")
                    logger.warning(
                        "WB API timeout, waiting %.1fs before retry %s/%s",
                        transient_delay,
                        retry_count + 1,
                        max_retries,
                    )
                    await asyncio.sleep(transient_delay)
                    transient_delay = min(transient_delay * 2, 16.0)
                    retry_count += 1
                    continue
                except httpx.RequestError as e:
                    if retry_count >= max_retries:
                        raise WBAPIError(f"Ошибка соединения с WB API: {e}")
                    logger.warning(
                        "WB API connection error (%s), retry %s/%s",
                        e,
                        retry_count + 1,
                        max_retries,
                    )
                    await asyncio.sleep(transient_delay)
                    transient_delay = min(transient_delay * 2, 16.0)
                    retry_count += 1
                    continue

        # ???? ????? ?? ????? - ?????? ????????? ????? ???????
        raise RateLimitError(f"Rate limit (429) ????? {max_retries} ???????")
    
    async def get_prices(
        self,
        nm_ids: Optional[List[int]] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[PriceData]:
        """
        ????????? ??? ???????
        
        Endpoint: GET /api/v2/list/goods/filter
        
        Args:
            nm_ids: ?????? ????????? (???? None - ??? ??????)
            limit: ????? ??????? ? ??????
            offset: ???????? ??? ?????????
            
        Returns:
            ?????? PriceData
        """
        params = {
            'limit': limit,
            'offset': offset
        }
        
        # ?????? ?? nmID (???? ??????)
        if nm_ids:
            # WB API ????????? ????????? nmID ????? ???????
            params['filterNmID'] = ','.join(map(str, nm_ids))
        
        logger.info(f"???????? ???? ?? WB API (limit={limit}, offset={offset}, nm_ids={len(nm_ids) if nm_ids else 'all'})")
        
        try:
            data = await self._make_request('GET', '/api/v2/list/goods/filter', params=params)
            
            # ?????? ?????
            goods = data.get('data', {}).get('listGoods', [])
            prices = [PriceData(item) for item in goods]
            
            logger.info(f"? ???????? {len(prices)} ??? ?? WB API")
            return prices
            
        except Exception as e:
            logger.error(f"?????? ????????? ??? ?? WB API: {e}")
            raise

    async def get_prices_by_articles(
        self,
        nm_ids: List[int],
        batch_size: int = 1000,
    ) -> List[PriceData]:
        """Fetch prices by article using POST batches of up to 1000 nmIDs.

        This is the safe fallback after a partial paginated prefetch. It avoids
        turning one failed page into hundreds of concurrent single-item GETs.
        """
        if not nm_ids:
            return []

        prices: List[PriceData] = []
        unique_nm_ids = list(dict.fromkeys(int(nm_id) for nm_id in nm_ids))
        for start in range(0, len(unique_nm_ids), batch_size):
            chunk = unique_nm_ids[start:start + batch_size]
            data = await self._make_request(
                "POST",
                "/api/v2/list/goods/filter",
                json_data={"nmList": chunk},
            )
            goods = data.get("data", {}).get("listGoods", [])
            prices.extend(PriceData(item) for item in goods)

        logger.info(
            "WB price batch fallback returned %d/%d items",
            len(prices),
            len(unique_nm_ids),
        )
        return prices

    async def update_prices(self, price_updates: List[Dict[str, Any]]) -> dict:
        """
        ???????? ?????????? ??? ? ?????????????? ?????????? ?? ?????.

        WB API ???????????? ???? ?????? 1000 ????????. ??? >1000
        ????????????? ????????? ?? ????? ? ?????????? ???????????????.

        Returns:
            ????? API ?????????? ????? (? ID ??????).
        """
        if not price_updates:
            raise ValueError("price_updates ?? ????? ???? ??????")

        WB_BATCH_LIMIT = 1000
        chunks = [price_updates[i:i + WB_BATCH_LIMIT]
                  for i in range(0, len(price_updates), WB_BATCH_LIMIT)]

        logger.info(
            "?????????? ?????????? %d ??? ? WB API (%d ????(??))",
            len(price_updates), len(chunks),
        )

        last_response = None
        for idx, chunk in enumerate(chunks):
            try:
                payload = {"data": chunk}
                data = await self._make_request('POST', '/api/v2/upload/task', json_data=payload)
                task_id = data.get('data', {}).get('id')
                if data.get('data', {}).get('alreadyExists'):
                    logger.info(
                        "???? %d/%d: ?????? ??? ?????????? ? WB (task_id=%s)",
                        idx + 1, len(chunks), task_id,
                    )
                    last_response = {**data, "_already_queued": True}
                    continue
                logger.info("? ???? %d/%d (%d ???): task_id=%s", idx + 1, len(chunks), len(chunk), task_id)
                last_response = data
            except WBAPIError as e:
                if "This task already exists" in str(e):
                    logger.info(
                        "???? %d/%d: ?????? ??? ? ??????? WB ? ??????? ???????",
                        idx + 1, len(chunks),
                    )
                    last_response = {"data": {"id": None}, "_already_queued": True}
                    continue
                logger.error("?????? ?????????? ??? (???? %d/%d): %s", idx + 1, len(chunks), e)
                raise

        return last_response

    async def verify_batch_update(
        self,
        upload_id: int,
        max_attempts: int = 5,
        delay: float = 5.0,
    ) -> Dict[int, Tuple[bool, str]]:
        """
        ?????????? WB ? per-nm ??????? ?????? batch-?????????? ???.

        WB ???????????? POST /upload/task ?????????? ? ????? ?????????
        ????????? ?????? (????????, ??? ???????? ???? ????? ??? ? 2 ????
        ??? ??? price < cost). ?????? ??????? ?????? ????? ?
        /history/goods/task?uploadID=<id>. ????? ?????????? ????????? ???,
        ???? ???? pending ??????.

        Returns:
            dict {nm_id: (success, error_text)}. ?????? dict ???? ??????
            ?? ??????? ?? max_attempts ???????.
        """
        if not upload_id:
            return {}

        last_pending = 0
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(8)  # ?????? ?????: WB ????? ????? ????????? ????
            try:
                data = await self._make_request(
                    'GET',
                    '/api/v2/history/goods/task',
                    params={'uploadID': upload_id, 'limit': 1000, 'offset': 0},
                )
            except Exception as e:
                logger.warning(f"?? ??????? ???????? ?????? ?????? {upload_id}: {e}")
                continue

            goods = (data.get('data') or {}).get('historyGoods') or []
            if not goods:
                continue

            pending = [g for g in goods if g.get('status') in (None, 0, 1)]
            if pending:
                last_pending = len(pending)
                logger.info(
                    f"?????? {upload_id}: {last_pending}/{len(goods)} ??????? ??? ? ?????????"
                )
                continue

            result: Dict[int, Tuple[bool, str]] = {}
            for g in goods:
                nm_id = g.get('nmID')
                if nm_id is None:
                    continue
                status = g.get('status')
                err = g.get('errorText') or ""
                # status==2: ??????? ?????????
                ok = (status == 2) and not err
                result[int(nm_id)] = (ok, err)
            logger.info(
                f"?????? {upload_id}: ????????? ??????? ???????? ??? {len(result)} ???????"
            )
            return result

        logger.warning(
            f"?????? {upload_id} ?? ??????????? ?? {max_attempts} ??????? "
            f"(???????? {last_pending} pending) ? ??????? ??????????"
        )
        return {}
    
    async def verify_price_update(
        self,
        nm_id: int,
        expected_price: Decimal,
        max_attempts: int = None,
        delay: int = None
    ) -> bool:
        """
        ???????? ?????????? ??????????? ????
        
        Args:
            nm_id: ??????? ??????
            expected_price: ????????? ????
            max_attempts: ???????? ??????? ???????? (default ?? config)
            delay: ???????? ????? ????????? ? ???????? (default ?? config)
            
        Returns:
            True ???? ???? ???????????, False ???? ???
        """
        if max_attempts is None:
            max_attempts = settings.PRICE_CHECK_ATTEMPTS
        if delay is None:
            delay = settings.PRICE_CHECK_DELAY
        
        logger.info(f"???????? ?????????? ???? ??? nm_id={nm_id} (????????? {expected_price}?)")
        
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(delay // 2)  # ?????? ???????? ? ???? ??????

            try:
                prices = await self.get_prices(nm_ids=[nm_id])
                
                if not prices:
                    logger.warning(f"??????? {attempt}/{max_attempts}: ????? nm_id={nm_id} ?? ??????")
                    continue
                
                current_price = prices[0].seller_price
                
                if current_price == expected_price:
                    logger.info(f"? ???? ??????????? ?? ??????? {attempt}/{max_attempts}: {current_price}?")
                    return True
                else:
                    logger.warning(
                        f"??????? {attempt}/{max_attempts}: ??????? ???? {current_price}?, "
                        f"????????? {expected_price}?"
                    )
                    
            except Exception as e:
                logger.error(f"?????? ???????? ???? ?? ??????? {attempt}: {e}")
                continue
        
        logger.error(f"? ???? ?? ??????????? ????? {max_attempts} ???????")
        return False
    
    async def get_card_info(self, nm_id: int, max_retries: int = 5) -> dict:
        """
        ????????? ?????????? ? ???????? ?????? ?? WB Content API ?? nmID.

        ?????????? ??????? {seller_sku, name, image_url} ??? {} ???? ?? ???????.
        ????????????? ????????? ?????? ??? 429 ? ???????????????? ?????????.
        """
        content_api_url = "https://content-api.wildberries.ru"
        payload = {
            "settings": {
                "sort": {"ascending": False},
                "filter": {
                    "textSearch": str(nm_id),
                    "withPhoto": -1
                },
                "cursor": {"limit": 10}
            }
        }
        retry_delays = [5, 15, 30, 60, 120]

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await self._wait_for_limit("content")
                    response = await client.post(
                        f"{content_api_url}/content/v2/get/cards/list",
                        json=payload,
                        headers=self._get_headers()
                    )

                if response.status_code == 429:
                    delay = self._ratelimit_delay(response, retry_delays[min(attempt, len(retry_delays) - 1)])
                    logger.warning(
                        f"get_card_info: 429 ??? nm_id={nm_id} "
                        f"(??????? {attempt + 1}/{max_retries}), ???? {delay}s..."
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay)
                        continue
                    return {}

                if response.status_code != 200:
                    logger.warning(f"Content API ?????? {response.status_code} ??? nm_id={nm_id}")
                    return {}

                cards = response.json().get("cards", [])
                for card in cards:
                    if card.get("nmID") == nm_id:
                        photos = card.get("photos", [])
                        image_url = None
                        if photos:
                            photo = photos[0]
                            image_url = photo.get("c246x328") or photo.get("big")
                        info = {
                            "seller_sku": card.get("vendorCode", ""),
                            "name":       card.get("title", ""),
                            "image_url":  image_url,
                        }
                        logger.info(f"? ?????????? ? ???????? ???????? ??? nm_id={nm_id}: {info['name']!r}")
                        return info

                logger.warning(f"???????? ?? ??????? ??? nm_id={nm_id} (???????? ? ??????: {len(cards)})")
                return {}

            except Exception as e:
                logger.error(f"?????? ????????? ?????????? ? ???????? nm_id={nm_id}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                    continue
                return {}

        return {}

    async def get_card_info_by_sku(self, seller_sku: str) -> dict:
        """
        ????????? ?????????? ? ???????? ?????? ?? WB Content API ?? ???????? ???????? (vendorCode).

        ?????????? ??????? {nm_id, seller_sku, name, image_url} ??? {} ???? ?? ???????.
        """
        try:
            content_api_url = "https://content-api.wildberries.ru"
            payload = {
                "settings": {
                    "sort": {"ascending": False},
                    "filter": {
                        "textSearch": seller_sku,
                        "withPhoto": -1
                    },
                    "cursor": {"limit": 20}
                }
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                await self._wait_for_limit("content")
                response = await client.post(
                    f"{content_api_url}/content/v2/get/cards/list",
                    json=payload,
                    headers=self._get_headers()
                )
                if response.status_code != 200:
                    logger.warning(f"Content API ?????? {response.status_code} ??? ?????? ?? ???????? {seller_sku!r}")
                    return {}
                cards = response.json().get("cards", [])

            # ???? ?????? ?????????? vendorCode
            for card in cards:
                if card.get("vendorCode") == seller_sku:
                    photos = card.get("photos", [])
                    image_url = None
                    if photos:
                        photo = photos[0]
                        image_url = photo.get("c246x328") or photo.get("big")
                    info = {
                        "nm_id":      card.get("nmID"),
                        "seller_sku": card.get("vendorCode", seller_sku),
                        "name":       card.get("title", ""),
                        "image_url":  image_url,
                    }
                    logger.info(f"? ???????? ??????? ?? ???????? {seller_sku!r}: nm_id={info['nm_id']}")
                    return info

            logger.warning(f"???????? ? ????????? {seller_sku!r} ?? ??????? (???????? ? ??????: {len(cards)})")
            return {}

        except Exception as e:
            logger.error(f"?????? ?????? ???????? ?? ???????? {seller_sku!r}: {e}")
            return {}

    async def get_card_photo(self, nm_id: int) -> Optional[str]:
        """
        ????????? URL ???? ?????? ?? WB Content API ?? nmID.
        ??????? ??? get_card_info ??? ???????? ?????????????.
        """
        info = await self.get_card_info(nm_id)
        return info.get("image_url")

    async def get_cards_batch(
        self,
        nm_ids: List[int],
        concurrency: int = 10
    ) -> Dict[int, dict]:
        """
        Batch-????????? ?????????? ? ????????? ?? ?????? nmID.
        ??????? ??????????? ??????????? ? ???????????? concurrency.

        Returns:
            ??????? {nm_id: {seller_sku, name, image_url}}
        """
        if not nm_ids:
            return {}

        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_one(nm_id: int) -> Tuple[int, dict]:
            async with semaphore:
                info = await self.get_card_info(nm_id)
                return nm_id, info

        results_raw = await asyncio.gather(
            *[fetch_one(nm_id) for nm_id in nm_ids],
            return_exceptions=True
        )

        result: Dict[int, dict] = {}
        for item in results_raw:
            if isinstance(item, Exception):
                logger.warning(f"?????? ? batch-??????? ????????: {item}")
                continue
            nm_id, info = item
            if info:
                result[nm_id] = info

        logger.info(f"get_cards_batch: ????????? {len(nm_ids)}, ???????? {len(result)}")
        return result

    async def get_all_cards(self, page_limit: int = 100) -> Dict[str, dict]:
        """
        ????????? ??? ???????? ???????? ????? cursor-?????????.
        ?????? N ????????? ???????? (?? ?????? ?? ???????) ?????? 2-5 ????????.

        Returns:
            ??????? {vendorCode: {nm_id, seller_sku, name, image_url}}
        """
        content_api_url = "https://content-api.wildberries.ru"
        all_cards: Dict[str, dict] = {}
        cursor: dict = {"limit": page_limit}

        max_page_retries = 5
        retry_delays = [2, 5, 10, 20, 30]  # ??????? ????? ?????? ????????

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                payload = {
                    "settings": {
                        "sort": {"ascending": True},
                        "filter": {"withPhoto": -1},
                        "cursor": cursor,
                    }
                }

                # Retry-???? ??? ??????? ????????
                page_data = None
                for attempt in range(max_page_retries):
                    try:
                        await self._wait_for_limit("content")
                        response = await client.post(
                            f"{content_api_url}/content/v2/get/cards/list",
                            json=payload,
                            headers=self._get_headers(),
                        )
                    except Exception as e:
                        logger.error(f"get_all_cards: ?????? ??????? (??????? {attempt+1}): {e}")
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        break

                    if response.status_code == 200:
                        page_data = response.json()
                        break
                    elif response.status_code == 429:
                        fallback_wait = retry_delays[min(attempt, len(retry_delays)-1)] * 4
                        wait_time = self._ratelimit_delay(response, fallback_wait)
                        logger.warning(f"get_all_cards: Rate limit 429, wait {wait_time}s attempt {attempt+1}/{max_page_retries}")
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(wait_time)
                            continue
                        logger.error("get_all_cards: 429 after max retries, stopping")
                        break
                    elif response.status_code in (500, 502, 503, 504):
                        logger.warning(
                            f"get_all_cards: Content API ?????? {response.status_code} "
                            f"(??????? {attempt+1}/{max_page_retries}), ?????? ????? {retry_delays[attempt]}s..."
                        )
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        logger.error(f"get_all_cards: ???????? ?? ????????? ????? {max_page_retries} ???????, ?????????")
                        break
                    else:
                        logger.warning(f"get_all_cards: Content API ?????? {response.status_code}")
                        break

                if page_data is None:
                    # ?????????? ??-?? 429 ??? ??????? ?????? ? ??????? ????????
                    logger.error(
                        "get_all_cards: ???????? ????? ???????? %d ???????? "
                        "(?????? API ??? 429). ??????? ????????.", len(all_cards)
                    )
                    raise PartialCatalogError(
                        "Partial catalog: loaded %d cards before API error" % len(all_cards),
                        partial_cards=all_cards,
                    )

                cards = page_data.get("cards", [])

                for card in cards:
                    vendor_code = card.get("vendorCode")
                    if not vendor_code:
                        continue
                    photos = card.get("photos", [])
                    image_url = None
                    if photos:
                        image_url = photos[0].get("c246x328") or photos[0].get("big")
                    all_cards[vendor_code] = {
                        "nm_id":      card.get("nmID"),
                        "seller_sku": vendor_code,
                        "name":       card.get("title", ""),
                        "image_url":  image_url,
                    }

                resp_cursor = page_data.get("cursor", {})
                total = resp_cursor.get("total", 0)
                logger.info(f"get_all_cards: ???????? {len(all_cards)} ????????, total={total}")

                if total < page_limit:
                    break  # ????????? ????????

                cursor = {
                    "limit":     page_limit,
                    "updatedAt": resp_cursor.get("updatedAt"),
                    "nmID":      resp_cursor.get("nmID"),
                }

        logger.info(f"get_all_cards: ????? ????????? {len(all_cards)} ????????")
        return all_cards

    async def get_cards_batch_by_skus(
        self,
        seller_skus: List[str],
        concurrency: int = 10
    ) -> Dict[str, dict]:
        """
        Batch-????????? ?????????? ? ????????? ?? ?????? ????????? ????????.
        ???? ?????? SKU ??????? ????? Content API search ? ??? ???????? ????? ????????.

        Returns:
            ??????? {seller_sku: {nm_id, seller_sku, name, image_url}}
        """
        if not seller_skus:
            return {}

        result = {}
        sem = asyncio.Semaphore(concurrency)

        async def _search_one(sku: str):
            async with sem:
                try:
                    cards = await self.search_cards(text_search=sku, limit=100)
                    for card in cards:
                        vendor_code = card.get("vendorCode", "")
                        if vendor_code == sku:
                            nm_id = card.get("nmID")
                            if nm_id:
                                photos = card.get("photos", [])
                                image_url = None
                                if photos:
                                    photo = photos[0]
                                    image_url = photo.get("c246x328") or photo.get("big")
                                result[sku] = {
                                    "nm_id": nm_id,
                                    "seller_sku": sku,
                                    "name": card.get("title", ""),
                                    "image_url": image_url,
                                }
                            break
                except Exception as e:
                    logger.warning(f"search_cards failed for sku={sku!r}: {e}")

        tasks = [_search_one(sku) for sku in seller_skus]
        await asyncio.gather(*tasks)

        logger.info(f"get_cards_batch_by_skus: ????????? {len(seller_skus)}, ??????? {len(result)}")
        return result

    async def search_cards(self, text_search: str, limit: int = 100) -> List[dict]:
        """
        ????? ???????? ??????? ?? nmID ??? ???????? ???????? ????? Content API

        Endpoint: POST https://content-api.wildberries.ru/content/v2/get/cards/list
        """
        content_api_url = "https://content-api.wildberries.ru"
        payload = {
            "settings": {
                "sort": {"ascending": False},
                "filter": {
                    "textSearch": str(text_search),
                    "withPhoto": -1
                },
                "cursor": {
                    "limit": limit
                }
            }
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await self._wait_for_limit("content")
                response = await client.post(
                    f"{content_api_url}/content/v2/get/cards/list",
                    json=payload,
                    headers=self._get_headers()
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("cards", [])
                else:
                    raise WBAPIError(
                        f"Content API ?????? ??? {response.status_code}: {response.text}"
                    )
        except httpx.RequestError as e:
            raise WBAPIError(f"?????? ?????????? ? WB Content API: {e}")

    async def __aenter__(self):
        """Context manager support (no-op: ?????? ??????????? per-request)."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager support (no-op: ?????? ??????????? per-request)."""
        pass


def create_wb_client(api_token: str) -> WBAPIClient:
    """
    ??????? ??? ???????? WB API ???????
    
    Args:
        api_token: JWT ????? WB API
        
    Returns:
        WBAPIClient
    """
    return WBAPIClient(api_token)

