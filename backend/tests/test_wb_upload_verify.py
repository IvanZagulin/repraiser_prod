"""WB upload verification and catalog size selection."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.catalog_parser import ProductData
from app.services.wb_api import WBAPIClient, WBAPIError


def _client():
    return WBAPIClient("token")


def _no_sleep():
    return patch("app.services.wb_api.asyncio.sleep", new=AsyncMock())


def _goods(*items):
    return {"data": {"historyGoods": [
        {"nmID": nm, "status": status, "errorText": err} for nm, status, err in items
    ]}}


class TestVerifyBatchUpdate:
    def test_per_item_statuses(self):
        client = _client()
        client._make_request = AsyncMock(return_value=_goods(
            (1, 2, None), (2, 3, "New price is several times lower. Item has been moved to Price Quarantine"),
        ))
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55))
        assert result[1] == (True, "")
        assert result[2][0] is False and "Quarantine" in result[2][1]

    def test_waits_while_processing(self):
        client = _client()
        client._make_request = AsyncMock(side_effect=[
            {"data": {"historyGoods": None}},
            {"data": None},  # /history/tasks: ещё нет итога
            _goods((1, 1, None)),
            _goods((1, 2, None)),
        ])
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55, expected_nm_ids=[1]))
        assert result == {1: (True, "")}

    def test_task_summary_confirms_all_expected(self):
        client = _client()
        client._make_request = AsyncMock(side_effect=[
            {"data": {"historyGoods": None}},
            {"data": {"uploadID": 55, "status": 3, "overAllGoodsNumber": 2, "successGoodsNumber": 2}},
        ])
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55, expected_nm_ids=[1, 2]))
        assert result == {1: (True, ""), 2: (True, "")}

    def test_paginates_history_goods(self):
        client = _client()
        page1 = _goods(*[(i, 2, None) for i in range(1000)])
        page2 = _goods((1000, 2, None))
        client._make_request = AsyncMock(side_effect=[page1, page2])
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55))
        assert len(result) == 1001
        assert client._make_request.await_args_list[1].kwargs["params"]["offset"] == 1000

    def test_deadline_stops_polling(self):
        """history-эндпоинты WB часто висят: проверка не должна тянуться десятки минут."""
        client = _client()
        client._make_request = AsyncMock(return_value={"data": {"historyGoods": None}})
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55, max_wait=0))
        assert result == {}
        client._make_request.assert_not_awaited()

    def test_unknown_after_all_attempts(self):
        client = _client()
        client._make_request = AsyncMock(return_value={"data": {"historyGoods": None}})
        with _no_sleep():
            result = asyncio.run(client.verify_batch_update(55, max_attempts=3))
        assert result == {}


class TestUploadNotRetried:
    def test_post_timeout_is_not_retried(self):
        client = _client()
        calls = []

        async def fake_request(self, method, url, json=None, params=None):
            calls.append(url)
            raise httpx.ReadTimeout("read timeout")

        with patch.object(httpx.AsyncClient, "request", new=fake_request), \
             patch.object(client, "_wait_for_limit", new=AsyncMock()), _no_sleep():
            with pytest.raises(WBAPIError):
                asyncio.run(client.update_prices([{"nmID": 1, "price": 100}]))
        assert len(calls) == 1  # WB мог уже принять задачу — повтор создал бы дубль


class TestCatalogSizeSelection:
    def test_uses_priced_in_stock_size(self):
        raw = {"id": 1, "totalQuantity": 3, "sizes": [
            {"price": {}, "stocks": []},  # распроданный размер без цены
            {"price": {"basic": 300000, "product": 210000}, "stocks": [{"qty": 0}]},
            {"price": {"basic": 250000, "product": 190000}, "stocks": [{"qty": 3}]},
        ]}
        pd = ProductData(raw)
        assert pd.final_price == Decimal("1900")
        assert pd.seller_price == Decimal("2500")

    def test_single_size_unchanged(self):
        raw = {"id": 1, "totalQuantity": 1, "sizes": [
            {"price": {"basic": 533900, "product": 368300}},
        ]}
        pd = ProductData(raw)
        assert pd.final_price == Decimal("3683")
        assert pd.seller_price == Decimal("5339")
