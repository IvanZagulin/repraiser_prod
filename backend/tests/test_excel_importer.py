from io import BytesIO

import openpyxl

from app.utils.excel import ExcelImporter


def _xlsx_with_strategy_row(row):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "seller_sku",
        "strategy",
        "is_active",
        "min_price",
        "max_price",
        "rrp_price",
        "rrp_tolerance",
        "wallet",
        "competitor_nm_id",
        "competitor_offset_type",
        "competitor_offset_direction",
        "competitor_offset_value",
        "account_id",
        "night_price_enabled",
        "night_start_msk",
        "night_end_msk",
        "night_seller_price",
    ])
    ws.append(row)
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def test_strategy_import_allows_blank_min_price_when_disabling():
    data = _xlsx_with_strategy_row([
        "SKU-1",
        "rrp",
        "нет",
        None,
        None,
        "1000",
        None,
        "нет",
        None,
        None,
        None,
        None,
        "1",
        None,
        None,
        None,
        None,
    ])

    configs = ExcelImporter.import_strategy_config(data)

    assert len(configs) == 1
    assert configs[0]["seller_sku"] == "SKU-1"
    assert configs[0]["is_active"] is False
    assert configs[0]["min_price"] is None


def test_strategy_import_allows_blank_strategy_when_disabling():
    data = _xlsx_with_strategy_row([
        "SKU-1",
        None,
        "нет",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "1",
        None,
        None,
        None,
        None,
    ])

    configs = ExcelImporter.import_strategy_config(data)

    assert len(configs) == 1
    assert configs[0]["seller_sku"] == "SKU-1"
    assert configs[0]["strategy"] is None
    assert configs[0]["is_active"] is False
