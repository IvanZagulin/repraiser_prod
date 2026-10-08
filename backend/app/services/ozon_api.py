import asyncio
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class OzonAPIError(Exception):
    """Base Ozon Seller API error."""


class OzonAuthError(OzonAPIError):
    """Ozon Seller API auth error."""


class OzonRateLimitError(OzonAPIError):
    """Ozon Seller API rate limit error."""


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


class OzonPriceData:
    """Normalized Ozon price response used by the repricer.

    Ozon pricing model:
      seller_price  = what seller sets ("Ваша цена")
      marketing_seller_price = price after seller's own promotions
      final_price (customer) = marketing_seller_price - Ozon co-investment
                               (NOT available in Seller API)

    We use marketing_seller_price as our best approximation of the effective
    price. The actual customer-facing price may be lower due to Ozon's
    co-investment (bank partnerships, бустинг, etc.), which the Seller API
    does not expose.
    """

    def __init__(self, raw_data: Dict[str, Any]):
        self.raw_data = raw_data
        price = raw_data.get("price") or {}

        self.product_id = int(raw_data.get("product_id") or 0)
        self.offer_id = raw_data.get("offer_id") or ""
        self.name = raw_data.get("name") or raw_data.get("product_name") or ""

        self.price = _to_decimal(price.get("price"))
        self.old_price = _to_decimal(price.get("old_price"))
        self.min_price = _to_decimal(price.get("min_price"))
        self.marketing_price = _to_decimal(price.get("marketing_price"))
        self.marketing_seller_price = _to_decimal(price.get("marketing_seller_price"))
        self.currency_code = price.get("currency_code") or "RUB"

        price_indexes = raw_data.get("price_indexes") or {}
        ozon_idx = price_indexes.get("ozon_index_data") or {}
        ext_idx = price_indexes.get("external_index_data") or {}
        self.price_index = _to_decimal(ozon_idx.get("price_index_value"))
        self.ozon_min_price = _to_decimal(ozon_idx.get("min_price"))
        self.external_min_price = _to_decimal(ext_idx.get("min_price"))
        self.color_index = price_indexes.get("color_index") or ""

    @property
    def seller_price(self) -> Decimal:
        return self.price

    @property
    def final_price(self) -> Decimal:
        msp = self.marketing_seller_price
        if msp > 0:
            return msp
        return self.price

    @property
    def ozon_discount_pct(self) -> Decimal:
        """Estimated seller-side discount (price → marketing_seller_price)."""
        if self.price > 0 and self.marketing_seller_price > 0 and self.marketing_seller_price < self.price:
            return (self.price - self.marketing_seller_price) / self.price * 100
        return Decimal(0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "offer_id": self.offer_id,
            "name": self.name,
            "price": float(self.price),
            "old_price": float(self.old_price),
            "min_price": float(self.min_price),
            "marketing_price": float(self.marketing_price),
            "marketing_seller_price": float(self.marketing_seller_price),
            "currency_code": self.currency_code,
            "price_index": float(self.price_index),
            "ozon_min_price": float(self.ozon_min_price),
            "external_min_price": float(self.external_min_price),
            "color_index": self.color_index,
        }


class OzonAPIClient:
    """Client for Ozon Seller API price operations."""

    base_url = "https://api-seller.ozon.ru"

    def __init__(self, client_id: str, api_key: str):
        self.client_id = client_id
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Client-Id": str(self.client_id),
            "Api-Key": str(self.api_key),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _make_request(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        *,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        delay = 1.0
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=30.0,
        ) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(endpoint, json=payload)
                except httpx.TimeoutException as exc:
                    raise OzonAPIError(f"Timeout while calling Ozon API {endpoint}") from exc
                except httpx.RequestError as exc:
                    raise OzonAPIError(f"Ozon API connection error: {exc}") from exc

                if response.status_code == 200:
                    return response.json()
                if response.status_code in (401, 403):
                    raise OzonAuthError(f"Ozon auth error {response.status_code}: {response.text}")
                if response.status_code == 429:
                    if attempt >= max_retries:
                        raise OzonRateLimitError("Ozon rate limit exceeded")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                if response.status_code in (500, 502, 503, 504):
                    if attempt >= max_retries:
                        raise OzonAPIError(f"Ozon API {response.status_code}: {response.text}")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                raise OzonAPIError(f"Ozon API {response.status_code}: {response.text}")

        raise OzonAPIError(f"Ozon API request failed after retries: {endpoint}")

    async def check_credentials(self) -> tuple[bool, str]:
        """
        Быстрая проверка пары Client-Id / Api-Key: запрашиваем один товар.

        Интерактивная проверка из UI, поэтому без ретраев по 429.
        Возвращает (ок, человекочитаемая причина).
        """
        payload = {"filter": {"visibility": "ALL"}, "limit": 1}
        try:
            await self._make_request("/v3/product/list", payload, max_retries=0)
        except OzonAuthError:
            return False, (
                "Ozon отклонил ключ. Проверьте Client-Id и Api-Key "
                "в личном кабинете Ozon → Настройки → Seller API."
            )
        except OzonRateLimitError:
            return False, "Ozon временно ограничил запросы. Повторите проверку через минуту."
        except OzonAPIError as e:
            return False, f"Ozon вернул ошибку: {e}"
        return True, "Ключ рабочий — Ozon отвечает на запросы"

    async def get_product_info(
        self,
        *,
        product_id: Optional[int] = None,
        offer_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up a single product via /v3/product/info/list.

        Returns a dict with at least: id, name, offer_id, primary_image, images.
        primary_image from Ozon is a list of URLs (not a string).
        """
        list_payload: Dict[str, Any] = {}
        if product_id:
            list_payload["product_id"] = [int(product_id)]
        if offer_id:
            list_payload["offer_id"] = [str(offer_id)]
        if not list_payload:
            raise ValueError("product_id or offer_id is required")

        try:
            data = await self._make_request("/v3/product/info/list", list_payload)
            result = data.get("result") or data
            items = result.get("items") or []
            if items:
                return items[0]
        except OzonAPIError as e:
            logger.warning("Ozon /v3/product/info/list failed for %s: %s", list_payload, e)

        return None

    @staticmethod
    def extract_primary_image(product_info: Dict[str, Any]) -> Optional[str]:
        """Extract the first image URL from Ozon product info.

        Ozon returns primary_image as a list of URLs, not a single string.
        """
        primary = product_info.get("primary_image")
        if isinstance(primary, list) and primary:
            return primary[0]
        if isinstance(primary, str) and primary:
            return primary
        images = product_info.get("images") or []
        if images:
            return images[0] if isinstance(images[0], str) else None
        return None

    async def get_product_list(
        self,
        *,
        product_ids: Optional[List[int]] = None,
        offer_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Look up products via /v3/product/info/list."""
        payload: Dict[str, Any] = {}
        if product_ids:
            payload["product_id"] = [int(v) for v in product_ids]
        if offer_ids:
            payload["offer_id"] = [str(v) for v in offer_ids]
        if not payload:
            raise ValueError("product_ids or offer_ids is required")
        try:
            data = await self._make_request("/v3/product/info/list", payload)
            result = data.get("result") or data
            items = result.get("items") or []
            return items
        except OzonAPIError as e:
            logger.warning("Ozon /v3/product/info/list failed: %s", e)
            return []

    async def get_prices(
        self,
        *,
        product_ids: Optional[List[int]] = None,
        offer_ids: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> List[OzonPriceData]:
        product_ids = [int(v) for v in (product_ids or []) if v]
        offer_ids = [str(v) for v in (offer_ids or []) if v]
        if not product_ids and not offer_ids:
            raise ValueError("product_ids or offer_ids are required")

        payload = {
            "filter": {
                "visibility": "ALL",
            },
            "limit": min(max(limit, 1), 1000),
        }
        if product_ids:
            payload["filter"]["product_id"] = product_ids
        if offer_ids:
            payload["filter"]["offer_id"] = offer_ids

        data = await self._make_request("/v5/product/info/prices", payload)
        result = data.get("result") or data
        items = result.get("items") or []
        return [OzonPriceData(item) for item in items]

    async def update_prices(self, price_updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not price_updates:
            raise ValueError("price_updates must not be empty")

        prices = []
        for update in price_updates:
            item: Dict[str, Any] = {
                "currency_code": update.get("currency_code") or "RUB",
                "price": str(int(Decimal(str(update["price"])))),
            }
            if update.get("product_id"):
                item["product_id"] = int(update["product_id"])
            elif update.get("offer_id"):
                item["offer_id"] = str(update["offer_id"])
            if update.get("old_price") is not None:
                item["old_price"] = str(update["old_price"])
            if update.get("min_price") is not None:
                item["min_price"] = str(update["min_price"])
            prices.append(item)

        data = await self._make_request("/v1/product/import/prices", {"prices": prices})
        return data

    @staticmethod
    def parse_update_statuses(response: Dict[str, Any], updates: List[Dict[str, Any]]) -> Dict[str, tuple[bool, str]]:
        result = response.get("result") or response
        items = result if isinstance(result, list) else result.get("items") or result.get("prices") or []
        statuses: Dict[str, tuple[bool, str]] = {}

        if isinstance(items, list):
            for item in items:
                product_id = item.get("product_id")
                offer_id = item.get("offer_id")
                key = str(product_id or offer_id or "")
                errors = item.get("errors") or []
                updated = item.get("updated")
                if errors:
                    messages = []
                    for error in errors:
                        if isinstance(error, dict):
                            messages.append(str(error.get("message") or error.get("code") or error))
                        else:
                            messages.append(str(error))
                    statuses[key] = (False, "; ".join(messages))
                elif updated is False:
                    statuses[key] = (False, item.get("error") or "Ozon rejected price update")
                elif key:
                    statuses[key] = (True, "")

        for update in updates:
            key = str(update.get("product_id") or update.get("offer_id") or "")
            statuses.setdefault(key, (True, ""))
        return statuses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


def create_ozon_client(client_id: str, api_key: str) -> OzonAPIClient:
    return OzonAPIClient(client_id, api_key)
