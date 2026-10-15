"""Тесты восстановления цены покупателя Ozon из ценовых индексов."""

from decimal import Decimal

import pytest

from app.services.ozon_api import OzonPriceData
from app.services.ozon_buyer_price import (
    EXTERNAL,
    OZON,
    SELF_MARKETPLACES,
    estimate_buyer_price,
    estimate_from_index,
    estimate_from_price_data,
)


# ---------------------------------------------------------------------------
# estimate_from_index — обращение одного индекса
# ---------------------------------------------------------------------------

def test_index_below_one_multiplies():
    # Ozon-индекс Кота Барсика: минимум конкурентов 3466, индекс 0.94
    assert estimate_from_index(Decimal("3466"), Decimal("0.94")) == Decimal("3258.04")


def test_index_above_one_divides():
    # При индексе > 1 формула зеркальная: min / (2 - index)
    assert estimate_from_index(Decimal("855"), Decimal("1.02")) == Decimal("872.45")


def test_index_exactly_one_returns_min_price():
    assert estimate_from_index(Decimal("1000"), Decimal("1")) == Decimal("1000.00")


@pytest.mark.parametrize("min_price, index_value", [
    (Decimal("0"), Decimal("0.94")),      # нет конкурентов по этому срезу
    (Decimal("3466"), Decimal("0")),      # индекс не посчитан
    (Decimal("3466"), Decimal("-0.5")),   # мусор
    (Decimal("3466"), Decimal("2")),      # знаменатель обращается в ноль
    (Decimal("3466"), Decimal("2.5")),    # знаменатель уходит в минус
    (None, Decimal("0.94")),
    (Decimal("3466"), None),
    (None, None),
])
def test_unusable_index_returns_none(min_price, index_value):
    assert estimate_from_index(min_price, index_value) is None


def test_accepts_plain_numbers():
    """Значения могут прийти из JSON как int/float — не должно падать."""
    assert estimate_from_index(3466, 0.94) == Decimal("3258.04")


# ---------------------------------------------------------------------------
# estimate_buyer_price — сведение нескольких индексов
# ---------------------------------------------------------------------------

def test_control_example_kot_barsik():
    """Контрольный пример: product_id 6530746274, на витрине было 3251 руб.

    Цена продавца 5543, ozon-индекс 0.94 при минимуме 3466,
    внешний индекс 0.96 при минимуме 3388.
    """
    estimate = estimate_buyer_price({
        OZON: (Decimal("3466"), Decimal("0.94")),
        EXTERNAL: (Decimal("3388"), Decimal("0.96")),
    })

    assert estimate is not None
    assert estimate.sources == 2
    # Два независимых индекса сошлись в пределах процента
    assert estimate.spread_pct < Decimal("1")
    # И попали в фактическую витринную цену с ошибкой меньше 0.5 %
    assert abs(estimate.buyer_price - Decimal("3251")) / Decimal("3251") < Decimal("0.005")
    assert estimate.per_source[OZON] == Decimal("3258.04")
    assert estimate.per_source[EXTERNAL] == Decimal("3252.48")


def test_three_indexes_converge():
    """Товар 3523916: три индекса, все три дают ~622 руб."""
    estimate = estimate_buyer_price({
        OZON: (Decimal("808"), Decimal("0.77")),
        EXTERNAL: (Decimal("298"), Decimal("1.52")),
        SELF_MARKETPLACES: (Decimal("412"), Decimal("1.34")),
    })

    assert estimate is not None
    assert estimate.sources == 3
    assert estimate.spread_pct < Decimal("1")
    assert Decimal("620") < estimate.buyer_price < Decimal("625")


def test_unusable_indexes_are_dropped_not_counted():
    estimate = estimate_buyer_price({
        OZON: (Decimal("0"), Decimal("0")),                 # нет данных
        EXTERNAL: (Decimal("3388"), Decimal("0.96")),
        SELF_MARKETPLACES: (Decimal("2544"), Decimal("5")),  # индекс вне диапазона
    })

    assert estimate is not None
    assert estimate.sources == 1
    assert estimate.buyer_price == Decimal("3252.48")
    # Единственный источник не с чем сравнивать — расхождения нет
    assert estimate.spread_pct == Decimal("0.00")


def test_no_usable_index_returns_none():
    assert estimate_buyer_price({
        OZON: (Decimal("0"), Decimal("0")),
        EXTERNAL: (Decimal("0"), Decimal("0")),
        SELF_MARKETPLACES: (Decimal("0"), Decimal("0")),
    }) is None


def test_empty_mapping_returns_none():
    assert estimate_buyer_price({}) is None


def test_spread_flags_disagreement():
    """Разошедшиеся индексы должны дать большой spread — это сигнал не доверять."""
    estimate = estimate_buyer_price({
        OZON: (Decimal("1000"), Decimal("0.9")),      # 900
        EXTERNAL: (Decimal("1000"), Decimal("0.5")),  # 500
    })

    assert estimate is not None
    assert estimate.buyer_price == Decimal("700.00")
    assert estimate.spread_pct > Decimal("50")


def test_ratio_to_seller_price():
    estimate = estimate_buyer_price({
        OZON: (Decimal("3466"), Decimal("0.94")),
        EXTERNAL: (Decimal("3388"), Decimal("0.96")),
    })

    k = estimate.ratio_to(Decimal("5543"))
    # Ozon доводит до витрины около 59 % выставленной цены
    assert Decimal("0.58") < k < Decimal("0.60")


@pytest.mark.parametrize("seller_price", [Decimal("0"), None])
def test_ratio_to_without_seller_price(seller_price):
    estimate = estimate_buyer_price({OZON: (Decimal("1000"), Decimal("1"))})
    assert estimate.ratio_to(seller_price) is None


# ---------------------------------------------------------------------------
# Разбор ответа /v5/product/info/prices целиком
# ---------------------------------------------------------------------------

V5_ITEM = {
    "product_id": 6530746274,
    "offer_id": "3883678",
    "acquiring": 55.43,
    "commissions": {"sales_percent_fbo": 52, "sales_percent_fbs": 52},
    "marketing_actions": {
        "actions": [{"title": "РК. Рассрочка 0-0-4", "value": 4}],
        "ozon_actions_exist": False,
    },
    "price": {
        "price": 5543,
        "old_price": 11086,
        "min_price": 5543,
        "marketing_seller_price": 5543,
        "currency_code": "RUB",
        "vat": 0.1,
        "auto_action_enabled": False,
    },
    "price_indexes": {
        "ozon_index_data": {"min_price": 3466, "price_index_value": 0.94},
        "external_index_data": {"min_price": 3388, "price_index_value": 0.96},
        "self_marketplaces_index_data": {"min_price": 0, "price_index_value": 0},
        "color_index": "GREEN",
    },
}


def test_price_data_parses_v5_fields():
    pd = OzonPriceData(V5_ITEM)

    assert pd.product_id == 6530746274
    assert pd.offer_id == "3883678"
    assert pd.price == Decimal("5543")
    assert pd.ozon_min_price == Decimal("3466")
    assert pd.ozon_index_value == Decimal("0.94")
    assert pd.external_min_price == Decimal("3388")
    assert pd.external_index_value == Decimal("0.96")
    assert pd.self_mp_min_price == Decimal("0")
    assert pd.sales_percent_fbs == Decimal("52")
    assert pd.acquiring == Decimal("55.43")
    assert pd.vat == Decimal("0.1")
    assert pd.auto_action_enabled is False
    assert pd.ozon_actions_exist is False
    assert len(pd.marketing_actions) == 1
    # Легаси-имя, на него завязан repricer_engine
    assert pd.price_index == pd.ozon_index_value


def test_price_data_parses_v3_index_field_names():
    """/v3/product/info/list называет то же поле minimal_price."""
    pd = OzonPriceData({
        "product_id": 1,
        "price": {"price": 100},
        "price_indexes": {
            "ozon_index_data": {"minimal_price": "3466.00", "price_index_value": 0.94},
        },
    })

    assert pd.ozon_min_price == Decimal("3466.00")
    assert pd.ozon_index_value == Decimal("0.94")


def test_estimate_from_price_data_end_to_end():
    estimate = estimate_from_price_data(OzonPriceData(V5_ITEM))

    assert estimate is not None
    assert estimate.sources == 2  # self_marketplaces пустой и отброшен
    assert abs(estimate.buyer_price - Decimal("3251")) / Decimal("3251") < Decimal("0.005")


def test_estimate_from_price_data_without_indexes():
    pd = OzonPriceData({"product_id": 1, "price": {"price": 100}})
    assert estimate_from_price_data(pd) is None
