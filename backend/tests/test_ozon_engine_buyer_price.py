"""Движок должен считать ценой покупателя витрину Ozon, а не нашу цену.

До этой правки `final_price` для Ozon равнялся нашей же цене, а СПП был нулём.
Из-за этого стратегия РРЦ, целясь в РРЦ, опускала цену продавца до уровня РРЦ —
и покупатель платил примерно на треть меньше цели.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ozon_api import OzonPriceData
from app.services.repricer_engine import RepricerEngine


def _engine():
    return RepricerEngine(db=MagicMock(), user_id=1)


def _price_data(**overrides):
    raw = {
        "product_id": 337367974,
        "offer_id": "3636639",
        "price": {"price": 1272, "old_price": 2544, "min_price": 1272,
                  "marketing_seller_price": 1272, "currency_code": "RUB"},
        "price_indexes": {
            # реальные данные карточки: витрина 873 ₽ при нашей цене 1272 ₽
            "ozon_index_data": {"min_price": 855, "price_index_value": 1.02},
            "external_index_data": {"min_price": 480, "price_index_value": 1.45},
            "self_marketplaces_index_data": {"min_price": 0, "price_index_value": 0},
        },
    }
    raw.update(overrides)
    return OzonPriceData(raw)


# ---------------------------------------------------------------------------
# _ozon_buyer_price
# ---------------------------------------------------------------------------

def test_buyer_price_and_implied_spp():
    buyer, spp, estimate = _engine()._ozon_buyer_price(_price_data())

    assert buyer is not None
    assert abs(buyer - Decimal("873")) < Decimal("2")
    # k = 873/1272 = 0.686 -> доинвестирование около 31 %
    assert Decimal("30") < spp < Decimal("32")
    assert estimate.sources == 2


def test_no_indexes_means_no_buyer_price():
    pd = _price_data(price_indexes={})
    buyer, spp, estimate = _engine()._ozon_buyer_price(pd)

    assert buyer is None
    assert spp == Decimal(0)
    assert estimate is None


def test_contradicting_indexes_rejected():
    """Если индексы расходятся сильнее порога — оценке не верим."""
    pd = _price_data(price_indexes={
        "ozon_index_data": {"min_price": 1000, "price_index_value": 0.9},    # 900
        "external_index_data": {"min_price": 1000, "price_index_value": 0.5},  # 500
    })
    buyer, spp, estimate = _engine()._ozon_buyer_price(pd)

    assert buyer is None
    assert spp == Decimal(0)
    # оценка есть, но отвергнута по расхождению
    assert estimate is not None and estimate.spread_pct > Decimal("5")


def test_implied_spp_out_of_range_rejected():
    """k заметно больше 1 (витрина выше нашей цены) — данные подозрительные."""
    pd = _price_data(
        price={"price": 100, "marketing_seller_price": 100},
        price_indexes={
            "ozon_index_data": {"min_price": 200, "price_index_value": 0.9},
            "external_index_data": {"min_price": 180, "price_index_value": 1.0},
        },
    )
    buyer, spp, _ = _engine()._ozon_buyer_price(pd)

    assert buyer is None
    assert spp == Decimal(0)


# ---------------------------------------------------------------------------
# _get_ozon_current_price_data
# ---------------------------------------------------------------------------

def _account():
    return SimpleNamespace(id=12, name="Ozon", marketplace="ozon",
                           ozon_client_id="cid", ozon_api_key="key")


async def _current_data(price_data):
    client = MagicMock()
    client.get_prices = AsyncMock(return_value=[price_data])
    client.get_product_info = AsyncMock(return_value=None)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)

    product = SimpleNamespace(id=1, nm_id=337367974, seller_sku="3636639",
                              name="item", image_url=None)
    with patch("app.services.repricer_engine.create_ozon_client", return_value=context):
        return await _engine()._get_ozon_current_price_data(product, _account())


@pytest.mark.asyncio
async def test_current_data_uses_buyer_price_not_our_own():
    data = await _current_data(_price_data())

    assert data["seller_price"] == Decimal("1272")
    # Главное: цена покупателя — витрина, а не наша цена
    assert abs(data["final_price"] - Decimal("873")) < Decimal("2")
    assert data["final_price"] < data["seller_price"]
    assert Decimal("30") < data["spp"] < Decimal("32")
    # Скидка обнулена: доля доинвестирования уже внутри СПП
    assert data["discount"] == Decimal(0)
    # Витрина известна — снижение не блокируем
    assert "catalog_fallback" not in data
    assert data["ozon_buyer_price_sources"] == 2


@pytest.mark.asyncio
async def test_current_data_blocks_lowering_without_indexes():
    data = await _current_data(_price_data(price_indexes={}))

    # Прежнее поведение сохранено...
    assert data["final_price"] == Decimal("1272")
    assert data["spp"] == Decimal(0)
    # ...но снижать по неизвестной витрине запрещено
    assert data["catalog_fallback"] is True
    assert data["ozon_buyer_price"] is None


@pytest.mark.asyncio
async def test_rrp_target_accounts_for_coinvestment():
    """РРЦ 872 ₽ при k=0.686 требует цены продавца около 1272 ₽, а не 872 ₽."""
    data = await _current_data(_price_data())
    engine = _engine()

    seller_price = engine._calculate_seller_price_from_final(
        Decimal("872"), data["spp"], data["discount"],
    )

    assert Decimal("1250") < seller_price < Decimal("1290")
