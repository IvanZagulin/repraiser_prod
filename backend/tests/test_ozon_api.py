from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ozon_api import OzonAPIClient
from app.services.repricer_engine import RepricerEngine


@pytest.mark.asyncio
async def test_ozon_get_prices_uses_v5_price_endpoint():
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(return_value={
        "items": [
            {
                "product_id": 123,
                "offer_id": "SKU-123",
                "price": {
                    "price": "999",
                    "marketing_price": "949",
                    "currency_code": "RUB",
                },
            }
        ]
    })

    prices = await client.get_prices(product_ids=[123], offer_ids=["SKU-123"])

    assert prices[0].product_id == 123
    assert prices[0].offer_id == "SKU-123"
    assert prices[0].seller_price == 999
    client._make_request.assert_awaited_once_with(
        "/v5/product/info/prices",
        {
            "filter": {
                "visibility": "ALL",
                "product_id": [123],
                "offer_id": ["SKU-123"],
            },
            "limit": 1000,
        },
    )


@pytest.mark.asyncio
async def test_ozon_update_prices_uses_import_prices_payload():
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(return_value={"result": []})

    await client.update_prices([
        {"product_id": 123, "offer_id": "SKU-123", "price": 1001},
    ])

    client._make_request.assert_awaited_once_with(
        "/v1/product/import/prices",
        {
            "prices": [
                {
                    "currency_code": "RUB",
                    "price": "1001",
                    "product_id": 123,
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_repricer_reads_ozon_price_without_wb_catalog():
    engine = RepricerEngine(db=MagicMock(), user_id=1)
    account = SimpleNamespace(
        marketplace="ozon",
        ozon_client_id="client-id",
        ozon_api_key="api-key",
    )
    product = SimpleNamespace(nm_id=123, seller_sku="SKU-123")
    ozon_price = SimpleNamespace(
        price=999,
        seller_price=999,
        final_price=949,
        name="Ozon item",
        offer_id="SKU-123",
        marketing_price=949,
        marketing_seller_price=949,
        ozon_discount_pct=5,
        price_index=0,
        ozon_min_price=0,
        ozon_index_value=0,
        external_min_price=0,
        external_index_value=0,
        self_mp_min_price=0,
        self_mp_index_value=0,
        color_index="",
        old_price=1998,
        min_price=800,
    )
    ozon = MagicMock()
    ozon.get_prices = AsyncMock(return_value=[ozon_price])
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=ozon)
    context.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.repricer_engine.create_ozon_client", return_value=context), \
         patch("app.services.repricer_engine.catalog_parser.parse_product") as parse_product:
        current = await engine._get_current_price_data(product, account)

    assert current["seller_price"] == 999
    assert current["final_price"] == 949
    assert current["spp"] == 0
    parse_product.assert_not_called()


# ---------------------------------------------------------------------------
# iter_all_prices — курсорная выгрузка всего кабинета
# ---------------------------------------------------------------------------

def _page(product_ids, cursor):
    return {
        "items": [{"product_id": pid, "price": {"price": 100}} for pid in product_ids],
        "cursor": cursor,
        "total": len(product_ids),
    }


async def _drain(client, **kwargs):
    pages = []
    async for page in client.iter_all_prices(pause=0, **kwargs):
        pages.append(page)
    return pages


@pytest.mark.asyncio
async def test_iter_all_prices_follows_cursor():
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(side_effect=[
        _page([1, 2], "cur-1"),
        _page([3], "cur-2"),  # короткая страница — выгрузка заканчивается
    ])

    pages = await _drain(client, page_size=2)

    assert [[p.product_id for p in page] for page in pages] == [[1, 2], [3]]
    first_call, second_call = client._make_request.await_args_list
    assert first_call.args[0] == "/v5/product/info/prices"
    assert "cursor" not in first_call.args[1]
    assert first_call.args[1]["filter"] == {"visibility": "IN_SALE"}
    assert second_call.args[1]["cursor"] == "cur-1"


@pytest.mark.asyncio
async def test_iter_all_prices_stops_on_empty_page():
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(side_effect=[
        _page([1, 2], "cur-1"),
        _page([], "cur-2"),
    ])

    pages = await _drain(client, page_size=2)

    assert len(pages) == 1
    assert client._make_request.await_count == 2


@pytest.mark.asyncio
async def test_iter_all_prices_stops_without_cursor():
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(side_effect=[_page([1, 2], "")])

    pages = await _drain(client, page_size=2)

    assert len(pages) == 1
    assert client._make_request.await_count == 1


@pytest.mark.asyncio
async def test_iter_all_prices_breaks_cursor_loop():
    """Если Ozon вернёт тот же курсор — выгрузка обязана оборваться, а не зациклиться."""
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(return_value=_page([1, 2], "same-cursor"))

    pages = await _drain(client, page_size=2)

    assert len(pages) == 2
    assert client._make_request.await_count == 2


@pytest.mark.asyncio
async def test_iter_all_prices_unwraps_result_envelope():
    """Часть методов Ozon заворачивает ответ в result — поддерживаем оба вида."""
    client = OzonAPIClient("client-id", "api-key")
    client._make_request = AsyncMock(side_effect=[{"result": _page([7], "")}])

    pages = await _drain(client, page_size=1000)

    assert [p.product_id for p in pages[0]] == [7]
