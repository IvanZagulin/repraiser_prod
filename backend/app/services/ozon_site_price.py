"""«Цена на сайте» Ozon — прямое измерение витрины вместо догадки по индексу.

Зачем
-----
/v5/product/info/prices отдаёт только цену продавца: `price` и
`marketing_seller_price` — это то, что выставили мы, а не то, что видит
покупатель. Доинвестирование Ozon поверх нашей цены в этот ответ не попадает
(поле `marketing_price` в v5 больше не приходит, v4 удалён).

До сих пор витрину восстанавливали обратным счётом из ценовых индексов
(`ozon_buyer_price`). Оценка неплохая, но индекс округлён до сотых, пересчёт
у Ozon идёт с задержкой, и ошибка в пару процентов целиком съедала коридор
РРЦ: по кабинету «Кассандра Ozon» все товары стояли выше РРЦ на 22…170 ₽.

Отчёт по товарам (`/v1/report/products/create`) отдаёт колонку
**«Цена на сайте, ₽»** — ровно ту цену, которую Ozon показывает на карточке
(цена с картой банка, большая зелёная). Это прямое измерение: ни антибота, ни
браузера, одна выгрузка на весь кабинет.

Сверка 2026-10-20 по кабинету 12 (цена продавца / индекс / отчёт / карточка,
прочитанная браузером):

    Альтернатива   3691 / 2116.85 / 2122 / 2133
    Lucky Sophie   8831 / 5352.61 / 5358 / 5332
    Кот Барсик     6350 / 3786.28 / 3784 / 3707
    Звездный англ. 1680 /    нет  / 1680 / нет в наличии

Отчёт совпадает с индексом там, где индекс вообще есть (±5 ₽), и покрывает
товары без индексов, по которым раньше витрина была неизвестна совсем.
Расхождение с карточкой — это региональная цена: карточка читалась из
Санкт-Петербурга, отчёт отдаёт цену сайта как таковую. Управляем по отчёту:
он один для всех регионов и доступен по API.

Остаток берём из того же отчёта не для красоты: у товара без остатка Ozon
не доинвестирует, и цена на сайте равна цене продавца. Принять такой замер
за правду — значит снизить цену продавца до РРЦ и получить витрину сильно
ниже РРЦ в тот момент, когда товар вернётся в продажу.

Цена ограничения
----------------
Отчёт строится не мгновенно: на кабинете в 22 тысячи товаров — около минуты,
файл ~10 МБ. Поэтому дёргаем его не на каждый товар, а один раз на прогон
(кеш в `RepricerEngine`), и обязательно проверяем свежесть: сразу после смены
нашей цены Ozon ещё не пересчитал витрину, и измерение по ней врёт.
"""

import csv
import io
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Заголовки колонок ищем по подстроке: Ozon меняет хвост («, ₽», «, шт.»)
# и регистр, но корень названия держится годами.
OFFER_COLUMN_HINT = "артикул"
SITE_PRICE_COLUMN_HINT = "цена на сайте"
STOCK_COLUMN_HINT = "доступно к продаже"

# Сколько ждать готовности отчёта. Кабинет на 22 тысячи товаров строится
# около минуты; пять минут — запас на очередь у Ozon.
REPORT_TIMEOUT_S = 300.0
REPORT_POLL_S = 5.0


@dataclass
class SiteRow:
    """Строка отчёта по одному товару."""

    price: Decimal
    """«Цена на сайте, ₽» — цена покупателя."""

    stock: Optional[int]
    """Суммарный остаток, доступный к продаже. None — колонок в отчёте нет."""


def _to_decimal(raw: Optional[str]) -> Optional[Decimal]:
    """«2 122,00» -> Decimal('2122.00'). Пустое, ноль и мусор -> None."""
    if not raw:
        return None
    cleaned = (
        raw.replace("\xa0", "")
        .replace(" ", "")
        .replace("'", "")
        .replace(",", ".")
        .strip()
    )
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _to_int(raw: Optional[str]) -> int:
    value = _to_decimal(raw)
    return int(value) if value is not None else 0


def _find_column(fieldnames, hint: str) -> Optional[str]:
    for name in fieldnames or ():
        if name and hint in name.strip().lower().lstrip("﻿"):
            return name
    return None


def _find_columns(fieldnames, hint: str) -> List[str]:
    return [
        name for name in (fieldnames or ())
        if name and hint in name.strip().lower().lstrip("﻿")
    ]


def parse_site_report_csv(text: str) -> Dict[str, SiteRow]:
    """Достаёт из CSV отчёта «артикул -> (цена на сайте, остаток)».

    Колонки ищем по имени, а не по номеру: Ozon регулярно вставляет новые
    (так в отчёт приехали «Метки» и «Причины скрытия»), и позиционный разбор
    молча начал бы читать не тот столбец.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    offer_col = _find_column(reader.fieldnames, OFFER_COLUMN_HINT)
    price_col = _find_column(reader.fieldnames, SITE_PRICE_COLUMN_HINT)
    if not offer_col or not price_col:
        raise ValueError(
            "В отчёте Ozon нет нужных колонок: "
            f"артикул={offer_col!r}, цена на сайте={price_col!r}. "
            f"Заголовки: {reader.fieldnames}"
        )
    # Остатки лежат в нескольких колонках (FBO, FBS, realFBS) — складываем.
    # Колонки «Зарезервировано» в подсказку не попадают и в сумму не идут.
    stock_cols = _find_columns(reader.fieldnames, STOCK_COLUMN_HINT)

    rows: Dict[str, SiteRow] = {}
    for row in reader:
        offer = (row.get(offer_col) or "").strip().lstrip("'")
        price = _to_decimal(row.get(price_col))
        if not offer or price is None:
            continue
        stock = sum(_to_int(row.get(c)) for c in stock_cols) if stock_cols else None
        rows[offer] = SiteRow(price=price, stock=stock)
    return rows


async def fetch_site_rows(
    client,
    *,
    visibility: str = "ALL",
    timeout_s: float = REPORT_TIMEOUT_S,
    poll_s: float = REPORT_POLL_S,
) -> Dict[str, SiteRow]:
    """Строит отчёт по товарам кабинета и возвращает его разобранным.

    `client` — `OzonAPIClient`. Ошибки наружу не глушим: вызывающий код сам
    решает, продолжать ли по оценке из индексов.
    """
    code = await client.create_products_report(visibility=visibility)
    url = await client.wait_report(code, timeout_s=timeout_s, poll_s=poll_s)

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as http:
        response = await http.get(url)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig", errors="replace")

    rows = parse_site_report_csv(text)
    logger.info(
        "[ozon.site_price] отчёт %s: товаров с ценой на сайте — %d",
        code, len(rows),
    )
    return rows
