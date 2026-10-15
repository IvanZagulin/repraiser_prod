"""Восстановление цены покупателя на Ozon из ценовых индексов.

Зачем это нужно
---------------
Seller API не отдаёт цену, которую видит покупатель на витрине (цену с Ozon
Картой): поля `price` и `marketing_seller_price` — это цена продавца, а Ozon
поверх неё добавляет собственную скидку (доинвестирование). Разрыв большой и
непостоянный: по выборке из 977 товаров кабинета «Кассандра Ozon» отношение
цены покупателя к цене продавца имеет медиану 0,638 при разбросе 0,50–1,00.
Управлять витриной, не зная этого отношения, невозможно.

Как восстанавливаем
-------------------
Ozon считает ценовой индекс именно от цены покупателя, и его можно обратить:

    индекс < 1  ->  цена_покупателя = мин_цена_конкурента * индекс
    индекс > 1  ->  цена_покупателя = мин_цена_конкурента / (2 - индекс)

В ответе приходят три независимых индекса (конкуренты на Ozon, конкуренты на
других площадках, наша же цена на других площадках). Каждый считается от своей
базы, но все три должны сойтись в одно число — и сходятся. Проверка на 977
товарах, у которых есть минимум два индекса: медианное расхождение между
оценками 0,74 %, у 97,6 % товаров оно меньше 2 %.

Контрольный пример (карточка «Кот Барсик в сумке белый 56 см», product_id
6530746274): цена продавца 5543 ₽, ozon-индекс 0,94 при минимуме конкурентов
3466 ₽ даёт 3258 ₽, внешний индекс 0,96 при минимуме 3388 ₽ даёт 3252 ₽,
среднее 3255 ₽ — на витрине в тот момент было 3251 ₽.

Расхождение между источниками возвращается наружу как мера доверия: если три
индекса разошлись на 20 %, оценке верить нельзя, и товар надо исключать из
автоматики, а не считать по нему цену.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Mapping, Optional, Tuple

# Имена блоков индексов в ответе /v5/product/info/prices.
OZON = "ozon"
EXTERNAL = "external"
SELF_MARKETPLACES = "self_marketplaces"

_CENTS = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass
class BuyerPriceEstimate:
    """Оценка цены покупателя и то, насколько ей можно верить."""

    buyer_price: Decimal
    """Среднее по всем пригодным индексам, рублей."""

    sources: int
    """Сколько индексов участвовало в оценке (1..3)."""

    spread_pct: Decimal
    """Расхождение между источниками, % от среднего. При одном источнике — 0."""

    per_source: Dict[str, Decimal] = field(default_factory=dict)
    """Оценка по каждому индексу в отдельности — для отладки и отчётов."""

    def ratio_to(self, seller_price: Decimal) -> Optional[Decimal]:
        """k = цена_покупателя / цена_продавца — доля, которая доходит до витрины."""
        if not seller_price or seller_price <= 0:
            return None
        return (self.buyer_price / seller_price).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )


def estimate_from_index(
    min_price: Optional[Decimal],
    index_value: Optional[Decimal],
) -> Optional[Decimal]:
    """Обращает один ценовой индекс в цену покупателя.

    Возвращает None, если индекс непригоден: нулевая база (у товара нет
    конкурентов по этому срезу), нулевой или отрицательный индекс, либо индекс
    >= 2 — при нём знаменатель (2 - индекс) обращается в ноль и уходит в минус,
    то есть формула перестаёт иметь смысл.
    """
    if min_price is None or index_value is None:
        return None

    min_price = Decimal(str(min_price))
    index_value = Decimal(str(index_value))

    if min_price <= 0 or index_value <= 0 or index_value >= 2:
        return None

    if index_value < 1:
        return _round(min_price * index_value)
    if index_value == 1:
        return _round(min_price)
    return _round(min_price / (Decimal(2) - index_value))


def estimate_buyer_price(
    indexes: Mapping[str, Tuple[Optional[Decimal], Optional[Decimal]]],
) -> Optional[BuyerPriceEstimate]:
    """Сводит несколько индексов в одну оценку цены покупателя.

    `indexes` — отображение «имя среза -> (мин_цена_конкурента, индекс)».
    Непригодные срезы отбрасываются. Если не осталось ни одного — None:
    по такому товару цену покупателя посчитать нельзя, и это нормальный исход
    (в кабинете таких около 1 %).
    """
    per_source: Dict[str, Decimal] = {}
    for name, (min_price, index_value) in indexes.items():
        estimate = estimate_from_index(min_price, index_value)
        if estimate is not None and estimate > 0:
            per_source[name] = estimate

    if not per_source:
        return None

    values = list(per_source.values())
    mean = sum(values) / Decimal(len(values))
    spread = (
        (max(values) - min(values)) / mean * Decimal(100)
        if len(values) > 1 and mean > 0
        else Decimal(0)
    )

    return BuyerPriceEstimate(
        buyer_price=_round(mean),
        sources=len(values),
        spread_pct=spread.quantize(_CENTS, rounding=ROUND_HALF_UP),
        per_source=per_source,
    )


def estimate_from_price_data(price_data) -> Optional[BuyerPriceEstimate]:
    """Удобная обёртка над `OzonPriceData` из `app.services.ozon_api`."""
    return estimate_buyer_price({
        OZON: (price_data.ozon_min_price, price_data.ozon_index_value),
        EXTERNAL: (price_data.external_min_price, price_data.external_index_value),
        SELF_MARKETPLACES: (price_data.self_mp_min_price, price_data.self_mp_index_value),
    })
