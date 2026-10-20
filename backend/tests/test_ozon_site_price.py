"""Разбор отчёта Ozon с колонкой «Цена на сайте»."""

from decimal import Decimal

import pytest

from app.services.ozon_site_price import _to_decimal, parse_site_report_csv


# Шапка — как её реально отдаёт Ozon (обрезана до значимых колонок, но порядок
# и написание сохранены, включая BOM и точку с запятой).
HEADER = (
    "﻿\"Артикул\";\"Ozon Product ID\";\"Название товара\";\"Статус товара\";"
    "\"Доступно к продаже по схеме FBO, шт.\";\"Зарезервировано, шт\";"
    "\"Доступно к продаже по схеме FBS, шт.\";"
    "\"Доступно к продаже по схеме realFBS, шт.\";"
    "\"Цена на сайте, ₽\";\"Зачёркнутая цена, ₽\""
)


def _csv(*rows):
    return "\n".join([HEADER] + list(rows)) + "\n"


def test_parses_price_and_stock():
    text = _csv(
        '"3884648";"3550387955";"Альтернатива";"Продается";"1";"1";"71";"0";"2122,00";"5988,00"',
        '"3884974";"5158086412";"Звездный английский";"Готов к продаже";"0";"0";"0";"0";"1680,00";"2634,00"',
    )
    rows = parse_site_report_csv(text)
    assert rows["3884648"].price == Decimal("2122.00")
    # FBO + FBS + realFBS; «Зарезервировано» в сумму не идёт
    assert rows["3884648"].stock == 72
    assert rows["3884974"].price == Decimal("1680.00")
    assert rows["3884974"].stock == 0


def test_column_found_by_name_not_position():
    """Ozon дописывает колонки в середину — позиционный разбор бы сломался."""
    header = "﻿\"Артикул\";\"Метки\";\"Причины скрытия\";\"Цена на сайте, ₽\""
    text = header + "\n" + '"3884648";"";"";"2122,00"' + "\n"
    rows = parse_site_report_csv(text)
    assert rows["3884648"].price == Decimal("2122.00")
    # Колонок с остатком в отчёте нет — остаток неизвестен, а не ноль
    assert rows["3884648"].stock is None


def test_missing_price_column_is_an_error():
    text = "\"Артикул\";\"Зачёркнутая цена, ₽\"\n\"3884648\";\"5988,00\"\n"
    with pytest.raises(ValueError):
        parse_site_report_csv(text)


def test_rows_without_price_are_skipped():
    text = _csv(
        '"3884648";"";"";"Продается";"0";"0";"0";"0";"";"5988,00"',
        '"3884974";"";"";"Архивный";"0";"0";"0";"0";"0,00";"2634,00"',
        '"3887328";"";"";"Продается";"0";"0";"4";"0";"5358,00";"15254,00"',
    )
    # Пустая цена и ноль — это «цены нет», а не «цена 0».
    assert list(parse_site_report_csv(text)) == ["3887328"]


@pytest.mark.parametrize("raw,expected", [
    ("2122,00", Decimal("2122.00")),
    ("2 122,00", Decimal("2122.00")),
    ("2 122,00", Decimal("2122.00")),
    ("'1680", Decimal("1680")),
    ("", None),
    (None, None),
    ("0,00", None),
    ("нет", None),
])
def test_to_decimal(raw, expected):
    assert _to_decimal(raw) == expected
