from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.repricer_engine import RepricerEngine
from app.services.wb_api import WBAPIClient


def _response(status_code: int, json_data=None, text: str = "") -> httpx.Response:
    kwargs = {"json": json_data} if json_data is not None else {"text": text}
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://example.test"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_make_request_retries_transient_502():
    request = AsyncMock(
        side_effect=[
            _response(502, text="bad gateway"),
            _response(200, json_data={"data": {"listGoods": []}}),
        ]
    )
    client = MagicMock()
    client.request = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.wb_api.httpx.AsyncClient", return_value=context), \
         patch("app.services.wb_api.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await WBAPIClient("token")._make_request(
            "GET",
            "/api/v2/list/goods/filter",
        )

    assert result == {"data": {"listGoods": []}}
    assert request.await_count == 2
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_get_prices_by_articles_uses_post_batches():
    wb = WBAPIClient("token")
    wb._make_request = AsyncMock(
        side_effect=[
            {"data": {"listGoods": [{"nmID": 1, "sizes": []}]}},
            {"data": {"listGoods": [{"nmID": 1001, "sizes": []}]}},
        ]
    )

    prices = await wb.get_prices_by_articles(list(range(1, 1002)))

    assert [price.nm_id for price in prices] == [1, 1001]
    assert wb._make_request.await_count == 2
    first_call = wb._make_request.await_args_list[0]
    second_call = wb._make_request.await_args_list[1]
    assert first_call.args[:2] == ("POST", "/api/v2/list/goods/filter")
    assert len(first_call.kwargs["json_data"]["nmList"]) == 1000
    assert second_call.kwargs["json_data"]["nmList"] == [1001]


@pytest.mark.asyncio
async def test_batch_failure_blocks_single_item_get_fallback():
    engine = RepricerEngine(db=MagicMock(), user_id=12)
    account = SimpleNamespace(
        name="Test account",
        is_active=True,
        wb_api_token="token",
    )
    product = SimpleNamespace(nm_id=123, account=account)

    wb = MagicMock()
    wb.get_prices_by_articles = AsyncMock(side_effect=RuntimeError("WB 502"))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=wb)
    context.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.repricer_engine.create_wb_client",
        return_value=context,
    ):
        await engine._prefetch_missing_prices([product])

    assert 123 in engine._price_fetch_failed

    with patch(
        "app.services.repricer_engine.create_wb_client"
    ) as create_client:
        result = await engine._get_current_price_data(product, account)

    assert result is None
    create_client.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_cache_miss_uses_limited_single_fallback():
    engine = RepricerEngine(db=MagicMock(), user_id=12)
    account = SimpleNamespace(
        name="Test account",
        is_active=True,
        wb_api_token="token",
    )
    product = SimpleNamespace(nm_id=1135666168, account=account)
    engine._prefetched_prices[product.nm_id] = SimpleNamespace(
        seller_price=Decimal("730"),
        discounted_price=Decimal("730"),
        discount=Decimal("0"),
    )
    catalog_data = MagicMock()
    catalog_data.to_dict.return_value = {
        "final_price": 357,
        "seller_price": 730,
        "spp": 51,
        "is_in_promo": True,
        "total_quantity": 7,
        "in_stock": True,
    }

    with patch("app.services.repricer_engine.cache_get", return_value=None), \
         patch("app.services.repricer_engine.cache_set") as cache_set, \
         patch(
             "app.services.repricer_engine.catalog_parser.parse_product",
             new=AsyncMock(return_value=catalog_data),
         ) as parse_product:
        result = await engine._get_current_price_data(product, account)

    assert result is not None
    assert result["final_price"] == Decimal("357")
    assert result["spp"] == Decimal("51")
    parse_product.assert_awaited_once_with(product.nm_id)
    cache_set.assert_called_once_with(
        f"wb_catalog:{product.nm_id}",
        catalog_data.to_dict.return_value,
        ttl=3600,
    )
