import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class WBAPIError(Exception):
    """Ошибка WB API"""
    pass


class WBAuthError(WBAPIError):
    """401 от WB — токен невалиден или отозван. Caller должен деактивировать аккаунт."""
    pass


class WBQuarantineError(WBAPIError):
    """WB пометил товар в Price Quarantine. Caller должен заблокировать товар на 24ч."""
    def __init__(self, message: str, nm_id: int):
        super().__init__(message)
        self.nm_id = nm_id


class RateLimitError(WBAPIError):
    """Ошибка превышения лимита запросов (429)"""
    pass


class PartialCatalogError(WBAPIError):
    """Каталог загружен не полностью из-за сетевой ошибки или 429"""
    def __init__(self, message: str, partial_cards: dict):
        super().__init__(message)
        self.partial_cards = partial_cards


class PriceData:
    """Данные о цене товара из WB Prices API"""

    def __init__(self, raw_data: dict):
        self.nm_id = raw_data.get('nmID')
        self.vendor_code = raw_data.get('vendorCode', '')

        # discounts-prices-api возвращает цены в sizes[0], discount — на верхнем уровне
        sizes = raw_data.get('sizes', [])
        if sizes:
            first_size = sizes[0]
            self.seller_price = Decimal(str(first_size.get('price', 0)))
            self.discounted_price = Decimal(str(first_size.get('discountedPrice', 0)))
        else:
            self.seller_price = Decimal(str(raw_data.get('price', 0)))
            self.discounted_price = Decimal(0)

        # discount всегда на верхнем уровне объекта товара (не внутри sizes)
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
        return f"<WBPrice nm={self.nm_id} price={self.seller_price}₽ discount={self.discount}%>"


class WBAPIClient:
    """
    Клиент для работы с WB Prices API
    
    Документация: https://openapi.wildberries.ru/prices/api/ru/
    """
    
    def __init__(self, api_token: str):
        """
        Args:
            api_token: JWT токен WB API
        """
        self.base_url = settings.WB_API_BASE_URL
        self.api_token = api_token

    def _get_headers(self) -> dict:
        """Формирование заголовков для запросов"""
        return {
            'Authorization': self.api_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        retry_on_429: bool = True,
        max_retries: int = 5
    ) -> dict:
        """
        Выполнение HTTP запроса с обработкой ошибок.
        Сессия создаётся и гарантированно закрывается внутри каждого вызова
        (fix: TCP socket leak — persistent self.session мог не закрываться).

        Args:
            method: HTTP метод (GET, POST, etc)
            endpoint: Endpoint API
            json_data: JSON данные для POST
            params: Query параметры
            retry_on_429: Повторять ли при 429 ошибке
            max_retries: Максимум попыток при 429

        Returns:
            JSON ответ

        Raises:
            WBAPIError: При ошибках API
            RateLimitError: При превышении лимита
        """
        retry_count = 0
        backoff_delay = 7  # Начальная задержка при 429 (> 6s window WB rate limit)

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._get_headers(),
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            while retry_count <= max_retries:
                try:
                    response = await client.request(
                        method=method,
                        url=endpoint,
                        json=json_data,
                        params=params
                    )

                    # Обработка кодов ответа
                    if response.status_code == 200:
                        return response.json()

                    elif response.status_code == 429:
                        # Rate limit exceeded
                        if not retry_on_429 or retry_count >= max_retries:
                            raise RateLimitError(f"Rate limit exceeded (429) после {retry_count} попыток")

                        logger.warning(f"Rate limit (429), ждем {backoff_delay}s перед retry {retry_count + 1}/{max_retries}")
                        await asyncio.sleep(backoff_delay)
                        backoff_delay *= 2  # Exponential backoff
                        retry_count += 1
                        continue

                    elif response.status_code == 401:
                        raise WBAuthError("Unauthorized (401) - токен невалиден или отозван WB")

                    elif response.status_code == 400:
                        error_msg = response.text
                        raise WBAPIError(f"Bad Request (400): {error_msg}")

                    else:
                        raise WBAPIError(f"API вернул код {response.status_code}: {response.text}")

                except httpx.TimeoutException:
                    raise WBAPIError("Timeout при запросе к WB API")
                except httpx.RequestError as e:
                    raise WBAPIError(f"Ошибка соединения с WB API: {e}")

        # Если вышли из цикла - значит превысили лимит попыток
        raise RateLimitError(f"Rate limit (429) после {max_retries} попыток")
    
    async def get_prices(
        self,
        nm_ids: Optional[List[int]] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[PriceData]:
        """
        Получение цен товаров
        
        Endpoint: GET /api/v2/list/goods/filter
        
        Args:
            nm_ids: Список артикулов (если None - все товары)
            limit: Лимит товаров в ответе
            offset: Смещение для пагинации
            
        Returns:
            Список PriceData
        """
        params = {
            'limit': limit,
            'offset': offset
        }
        
        # Фильтр по nmID (если указан)
        if nm_ids:
            # WB API принимает несколько nmID через запятую
            params['filterNmID'] = ','.join(map(str, nm_ids))
        
        logger.info(f"Получаем цены из WB API (limit={limit}, offset={offset}, nm_ids={len(nm_ids) if nm_ids else 'all'})")
        
        try:
            data = await self._make_request('GET', '/api/v2/list/goods/filter', params=params)
            
            # Парсим ответ
            goods = data.get('data', {}).get('listGoods', [])
            prices = [PriceData(item) for item in goods]
            
            logger.info(f"✓ Получено {len(prices)} цен из WB API")
            return prices
            
        except Exception as e:
            logger.error(f"Ошибка получения цен из WB API: {e}")
            raise
    
    async def update_prices(self, price_updates: List[Dict[str, Any]]) -> dict:
        """
        Пакетное обновление цен с автоматическим разбиением на чанки.

        WB API ограничивает один запрос 1000 товарами. При >1000
        автоматически разбивает на чанки и отправляет последовательно.

        Returns:
            Ответ API последнего чанка (с ID задачи).
        """
        if not price_updates:
            raise ValueError("price_updates не может быть пустым")

        WB_BATCH_LIMIT = 1000
        chunks = [price_updates[i:i + WB_BATCH_LIMIT]
                  for i in range(0, len(price_updates), WB_BATCH_LIMIT)]

        logger.info(
            "Отправляем обновление %d цен в WB API (%d чанк(ов))",
            len(price_updates), len(chunks),
        )

        last_response = None
        for idx, chunk in enumerate(chunks):
            try:
                payload = {"data": chunk}
                data = await self._make_request('POST', '/api/v2/upload/task', json_data=payload)
                task_id = data.get('data', {}).get('id')
                logger.info("✓ Чанк %d/%d (%d цен): task_id=%s", idx + 1, len(chunks), len(chunk), task_id)
                last_response = data
            except WBAPIError as e:
                if "This task already exists" in str(e):
                    logger.info(
                        "Чанк %d/%d: задача уже в очереди WB — считаем успехом",
                        idx + 1, len(chunks),
                    )
                    last_response = {"data": {"id": None}, "_already_queued": True}
                    continue
                logger.error("Ошибка обновления цен (чанк %d/%d): %s", idx + 1, len(chunks), e)
                raise

        return last_response

    async def verify_batch_update(
        self,
        upload_id: int,
        max_attempts: int = 5,
        delay: float = 5.0,
    ) -> Dict[int, Tuple[bool, str]]:
        """
        Опрашивает WB о per-nm статусе задачи batch-обновления цен.

        WB обрабатывает POST /upload/task асинхронно и может отклонить
        отдельные товары (например, при снижении цены более чем в 2 раза
        или при price < cost). Статус каждого товара виден в
        /history/goods/task?uploadID=<id>. Метод опрашивает несколько раз,
        пока есть pending товары.

        Returns:
            dict {nm_id: (success, error_text)}. Пустой dict если статус
            не получен за max_attempts опросов.
        """
        if not upload_id:
            return {}

        last_pending = 0
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(8)  # первый опрос: WB нужно время применить цену
            try:
                data = await self._make_request(
                    'GET',
                    '/api/v2/history/goods/task',
                    params={'uploadID': upload_id, 'limit': 1000, 'offset': 0},
                )
            except Exception as e:
                logger.warning(f"Не удалось получить статус задачи {upload_id}: {e}")
                continue

            goods = (data.get('data') or {}).get('historyGoods') or []
            if not goods:
                continue

            pending = [g for g in goods if g.get('status') in (None, 0, 1)]
            if pending:
                last_pending = len(pending)
                logger.info(
                    f"Задача {upload_id}: {last_pending}/{len(goods)} товаров ещё в обработке"
                )
                continue

            result: Dict[int, Tuple[bool, str]] = {}
            for g in goods:
                nm_id = g.get('nmID')
                if nm_id is None:
                    continue
                status = g.get('status')
                err = g.get('errorText') or ""
                # status==2: успешно применено
                ok = (status == 2) and not err
                result[int(nm_id)] = (ok, err)
            logger.info(
                f"Задача {upload_id}: финальные статусы получены для {len(result)} товаров"
            )
            return result

        logger.warning(
            f"Задача {upload_id} не завершилась за {max_attempts} опросов "
            f"(осталось {last_pending} pending) — статусы неизвестны"
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
        Проверка применения обновленной цены
        
        Args:
            nm_id: Артикул товара
            expected_price: Ожидаемая цена
            max_attempts: Максимум попыток проверки (default из config)
            delay: Задержка между попытками в секундах (default из config)
            
        Returns:
            True если цена применилась, False если нет
        """
        if max_attempts is None:
            max_attempts = settings.PRICE_CHECK_ATTEMPTS
        if delay is None:
            delay = settings.PRICE_CHECK_DELAY
        
        logger.info(f"Проверка применения цены для nm_id={nm_id} (ожидается {expected_price}₽)")
        
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(delay // 2)  # Первая проверка — ждём меньше

            try:
                prices = await self.get_prices(nm_ids=[nm_id])
                
                if not prices:
                    logger.warning(f"Попытка {attempt}/{max_attempts}: товар nm_id={nm_id} не найден")
                    continue
                
                current_price = prices[0].seller_price
                
                if current_price == expected_price:
                    logger.info(f"✓ Цена применилась на попытке {attempt}/{max_attempts}: {current_price}₽")
                    return True
                else:
                    logger.warning(
                        f"Попытка {attempt}/{max_attempts}: текущая цена {current_price}₽, "
                        f"ожидается {expected_price}₽"
                    )
                    
            except Exception as e:
                logger.error(f"Ошибка проверки цены на попытке {attempt}: {e}")
                continue
        
        logger.error(f"✗ Цена не применилась после {max_attempts} попыток")
        return False
    
    async def get_card_info(self, nm_id: int, max_retries: int = 5) -> dict:
        """
        Получение информации о карточке товара из WB Content API по nmID.

        Возвращает словарь {seller_sku, name, image_url} или {} если не найдено.
        Автоматически повторяет запрос при 429 с экспоненциальной задержкой.
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
                    response = await client.post(
                        f"{content_api_url}/content/v2/get/cards/list",
                        json=payload,
                        headers=self._get_headers()
                    )

                if response.status_code == 429:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    logger.warning(
                        f"get_card_info: 429 для nm_id={nm_id} "
                        f"(попытка {attempt + 1}/{max_retries}), ждём {delay}s..."
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay)
                        continue
                    return {}

                if response.status_code != 200:
                    logger.warning(f"Content API вернул {response.status_code} для nm_id={nm_id}")
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
                        logger.info(f"✓ Информация о карточке получена для nm_id={nm_id}: {info['name']!r}")
                        return info

                logger.warning(f"Карточка не найдена для nm_id={nm_id} (карточек в ответе: {len(cards)})")
                return {}

            except Exception as e:
                logger.error(f"Ошибка получения информации о карточке nm_id={nm_id}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                    continue
                return {}

        return {}

    async def get_card_info_by_sku(self, seller_sku: str) -> dict:
        """
        Получение информации о карточке товара из WB Content API по артикулу продавца (vendorCode).

        Возвращает словарь {nm_id, seller_sku, name, image_url} или {} если не найдено.
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
                response = await client.post(
                    f"{content_api_url}/content/v2/get/cards/list",
                    json=payload,
                    headers=self._get_headers()
                )
                if response.status_code != 200:
                    logger.warning(f"Content API вернул {response.status_code} при поиске по артикулу {seller_sku!r}")
                    return {}
                cards = response.json().get("cards", [])

            # Ищем точное совпадение vendorCode
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
                    logger.info(f"✓ Карточка найдена по артикулу {seller_sku!r}: nm_id={info['nm_id']}")
                    return info

            logger.warning(f"Карточка с артикулом {seller_sku!r} не найдена (карточек в ответе: {len(cards)})")
            return {}

        except Exception as e:
            logger.error(f"Ошибка поиска карточки по артикулу {seller_sku!r}: {e}")
            return {}

    async def get_card_photo(self, nm_id: int) -> Optional[str]:
        """
        Получение URL фото товара из WB Content API по nmID.
        Обёртка над get_card_info для обратной совместимости.
        """
        info = await self.get_card_info(nm_id)
        return info.get("image_url")

    async def get_cards_batch(
        self,
        nm_ids: List[int],
        concurrency: int = 10
    ) -> Dict[int, dict]:
        """
        Batch-получение информации о карточках по списку nmID.
        Запросы выполняются параллельно с ограничением concurrency.

        Returns:
            Словарь {nm_id: {seller_sku, name, image_url}}
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
                logger.warning(f"Ошибка в batch-запросе карточки: {item}")
                continue
            nm_id, info = item
            if info:
                result[nm_id] = info

        logger.info(f"get_cards_batch: запрошено {len(nm_ids)}, получено {len(result)}")
        return result

    async def get_all_cards(self, page_limit: int = 100) -> Dict[str, dict]:
        """
        Загружает ВСЕ карточки аккаунта через cursor-пагинацию.
        Вместо N отдельных запросов (по одному на артикул) делает 2-5 запросов.

        Returns:
            Словарь {vendorCode: {nm_id, seller_sku, name, image_url}}
        """
        content_api_url = "https://content-api.wildberries.ru"
        all_cards: Dict[str, dict] = {}
        cursor: dict = {"limit": page_limit}

        max_page_retries = 5
        retry_delays = [2, 5, 10, 20, 30]  # секунды перед каждой попыткой

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                payload = {
                    "settings": {
                        "sort": {"ascending": True},
                        "filter": {"withPhoto": -1},
                        "cursor": cursor,
                    }
                }

                # Retry-цикл для текущей страницы
                page_data = None
                for attempt in range(max_page_retries):
                    try:
                        response = await client.post(
                            f"{content_api_url}/content/v2/get/cards/list",
                            json=payload,
                            headers=self._get_headers(),
                        )
                    except Exception as e:
                        logger.error(f"get_all_cards: ошибка запроса (попытка {attempt+1}): {e}")
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        break

                    if response.status_code == 200:
                        page_data = response.json()
                        break
                    elif response.status_code == 429:
                        wait_time = retry_delays[min(attempt, len(retry_delays)-1)] * 4
                        logger.warning(f"get_all_cards: Rate limit 429, wait {wait_time}s attempt {attempt+1}/{max_page_retries}")
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(wait_time)
                            continue
                        logger.error("get_all_cards: 429 after max retries, stopping")
                        break
                    elif response.status_code in (500, 502, 503, 504):
                        logger.warning(
                            f"get_all_cards: Content API вернул {response.status_code} "
                            f"(попытка {attempt+1}/{max_page_retries}), повтор через {retry_delays[attempt]}s..."
                        )
                        if attempt < max_page_retries - 1:
                            await asyncio.sleep(retry_delays[attempt])
                            continue
                        logger.error(f"get_all_cards: страница не загружена после {max_page_retries} попыток, прерываем")
                        break
                    else:
                        logger.warning(f"get_all_cards: Content API вернул {response.status_code}")
                        break

                if page_data is None:
                    # Прервалось из-за 429 или сетевой ошибки — каталог неполный
                    logger.error(
                        "get_all_cards: прервано после загрузки %d карточек "
                        "(ошибка API или 429). Каталог неполный.", len(all_cards)
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
                logger.info(f"get_all_cards: страница {len(all_cards)} карточек, total={total}")

                if total < page_limit:
                    break  # последняя страница

                cursor = {
                    "limit":     page_limit,
                    "updatedAt": resp_cursor.get("updatedAt"),
                    "nmID":      resp_cursor.get("nmID"),
                }

        logger.info(f"get_all_cards: итого загружено {len(all_cards)} карточек")
        return all_cards

    async def get_cards_batch_by_skus(
        self,
        seller_skus: List[str],
        concurrency: int = 10
    ) -> Dict[str, dict]:
        """
        Batch-получение информации о карточках по списку артикулов продавца.
        Ищет каждый SKU точечно через Content API search — без загрузки всего каталога.

        Returns:
            Словарь {seller_sku: {nm_id, seller_sku, name, image_url}}
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

        logger.info(f"get_cards_batch_by_skus: запрошено {len(seller_skus)}, найдено {len(result)}")
        return result

    async def search_cards(self, text_search: str, limit: int = 100) -> List[dict]:
        """
        Поиск карточек товаров по nmID или артикулу продавца через Content API

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
                        f"Content API вернул код {response.status_code}: {response.text}"
                    )
        except httpx.RequestError as e:
            raise WBAPIError(f"Ошибка соединения с WB Content API: {e}")

    async def __aenter__(self):
        """Context manager support (no-op: сессии управляются per-request)."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager support (no-op: сессии управляются per-request)."""
        pass


def create_wb_client(api_token: str) -> WBAPIClient:
    """
    Фабрика для создания WB API клиента
    
    Args:
        api_token: JWT токен WB API
        
    Returns:
        WBAPIClient
    """
    return WBAPIClient(api_token)
