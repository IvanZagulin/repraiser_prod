import logging
from typing import List, Dict, Any, Optional
from io import BytesIO
from datetime import datetime
from decimal import Decimal
import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment

logger = logging.getLogger(__name__)


def _safe_str(value: Any) -> Any:
    """Экранирует строки, начинающиеся с символов формул Excel (=, +, -, @).
    Предотвращает CSV/Excel-инъекции при открытии файла."""
    if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


# Цвета бренда
COLOR_HEADER   = "4C1D95"   # фиолетовый
COLOR_HEADER2  = "1E3A5F"   # синий (для второго листа)
COLOR_GREEN    = "E8F5E9"
COLOR_ORANGE   = "FFF3E0"
COLOR_GRAY     = "F5F5F5"
COLOR_EXAMPLE  = "A0A0B0"


class ExcelImportError(Exception):
    """Ошибка импорта из Excel"""
    pass


class ExcelExporter:
    """Экспорт данных в Excel"""

    @staticmethod
    def create_workbook():
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        return wb

    @staticmethod
    def _style_header(ws, row: int = 1, color: str = COLOR_HEADER):
        fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
        font = Font(bold=True, color="FFFFFF", size=11)
        for cell in ws[row]:
            cell.fill = fill
            cell.font = font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 30

    @staticmethod
    def style_header(ws, row: int = 1):
        ExcelExporter._style_header(ws, row)

    @staticmethod
    def auto_size_columns(ws, min_width=10, max_width=40):
        for column in ws.columns:
            max_length = 0
            col_letter = get_column_letter(column[0].column)
            for cell in column:
                try:
                    v = str(cell.value) if cell.value is not None else ""
                    if len(v) > max_length:
                        max_length = len(v)
                except Exception as e:
                    logger.debug(f"Error processing cell in column {col_letter}: {e}")
            ws.column_dimensions[col_letter].width = max(min_width, min(max_length + 3, max_width))

    # ─────────────────── ШАБЛОНЫ ───────────────────

    @staticmethod
    def generate_products_template() -> BytesIO:
        """Пустой шаблон для массового импорта товаров.
        Лист «Товары» — только заголовок + пустые строки для данных.
        Лист «Инструкция» — примеры и описание полей.
        """
        wb = ExcelExporter.create_workbook()

        # ── Лист данных ──
        ws = wb.create_sheet("Товары")
        headers = [
            "nmID (либо A, либо B)",
            "Артикул продавца (либо A, либо B)",
            "ID аккаунта *",
            "Название товара (авто)",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        # 5 пустых строк-подсказок
        for _ in range(5):
            ws.append(["", "", "", ""])
        ExcelExporter.auto_size_columns(ws)
        ws.freeze_panes = "A2"

        # ── Лист инструкции ──
        wi = wb.create_sheet("Инструкция")
        wi.append(["Примеры заполнения"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(["nmID (A)", "Артикул продавца (B)", "ID аккаунта *", "Название (авто)"])
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)

        examples = [
            [123456789, "",             1, ""],
            ["",        "ваш-арт-001",  1, ""],
            [987654321, "ваш-арт-002",  2, "Можно вписать название вручную"],
        ]
        for ex in examples:
            wi.append(ex)
        for row_num in (4, 5, 6):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)

        wi.append([])
        wi.append(["Поле", "Описание"])
        ExcelExporter._style_header(wi, row=8, color=COLOR_HEADER)
        notes = [
            ("A — nmID",                 "Числовой артикул WB. Обязательно если не заполнена колонка B."),
            ("B — Артикул продавца",     "vendorCode. Обязательно если не заполнена колонка A. Система сама найдёт nmID через WB API."),
            ("C — ID аккаунта *",        "Числовой ID аккаунта из раздела Аккаунты. Всегда обязательно."),
            ("D — Название товара",      "Необязательно. Подтянется из WB автоматически если не заполнено."),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        # Делаем лист данных активным
        wb.active = ws

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    @staticmethod
    def generate_strategy_template() -> BytesIO:
        """Пустой шаблон для настройки стратегий.
        Лист «Стратегии» — только заголовок + пустые строки для данных.
        Лист «Инструкция» — примеры и описание полей.
        """
        wb = ExcelExporter.create_workbook()

        headers = [
            "Артикул продавца *",
            "Стратегия * (rrp=РРЦ / competitor=Конкурент)",
            "Активен (да/нет)",
            "МРЦ — мин цена * (₽)",
            "Макс цена (₽)",
            "РРЦ (₽)",
            "Допуск РРЦ (₽)",
            "Кошелёк в РРЦ (да/нет)",
            "nmID конкурента",
            "Тип смещения (rub=руб / pct=%%)",
            "Направление (above=выше / below=ниже / equal=как у конкурента)",
            "Значение смещения",
            "ID кабинета",
            "Ночная цена (да/нет)",
            "Ночь — начало МСК (HH:MM)",
            "Ночь — конец МСК (HH:MM)",
            "Ночная цена продавца (₽)",
        ]

        # ── Лист данных ──
        ws = wb.create_sheet("Стратегии")
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append([""] * len(headers))
        ExcelExporter.auto_size_columns(ws, min_width=12)
        ws.freeze_panes = "A2"

        # ── Лист инструкции ──
        wi = wb.create_sheet("Инструкция")
        wi.append(["Примеры заполнения"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(headers)
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)

        ex_rrp        = ["ваш-арт-001", "rrp",        "да", 500, 3000, 1999, 5,  "нет", "",         "",      "",       "", "Кассандра", "да", "01:00", "07:00", 4999]
        ex_competitor = ["ваш-арт-002", "competitor",  "да", 300, "",   "",   "",  "",    987654321, "rub",  "below",  50, "Астарта",  "нет", "",      "",      ""]
        ex_equal      = ["ваш-арт-003", "competitor",  "да", 100, "",   "",   "",  "",    987654322, "rub",  "equal",  0,  "Астарта",  "нет", "",      "",      ""]
        wi.append(ex_rrp)
        wi.append(ex_competitor)
        wi.append(ex_equal)
        for row_num in (4, 5, 6):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)

        wi.append([])
        wi.append(["Поле", "Допустимые значения / Описание"])
        ExcelExporter._style_header(wi, row=8, color=COLOR_HEADER)
        notes = [
            ("Артикул продавца *",       "vendorCode — должен уже быть в системе. Обязательно."),
            ("ID кабинета",           "Название или числовой ID аккаунта из раздела Аккаунты. Обязательно если один артикул есть в нескольких кабинетах."),
            ("Стратегия *",              "rrp (РРЦ) — удержание рекомендованной цены; competitor (Конкурент) — привязка к цене конкурента"),
            ("Активен",                  "да / нет (по умолчанию: да)"),
            ("МРЦ — мин цена *",         "Нижняя граница цены. Репрайсер не опустит ниже. Обязательно."),
            ("Макс цена",                "Верхняя граница цены (необязательно)"),
            ("РРЦ",                      "Рекомендованная розничная цена (только для стратегии rrp)"),
            ("Допуск РРЦ",               "Диапазон ±₽ от РРЦ (по умолчанию: 5)"),
            ("Кошелёк в РРЦ",            "да — учитывать скидку кошелька WB при расчёте (только rrp)"),
            ("nmID конкурента",          "Артикул WB конкурента (только для competitor)"),
            ("Тип смещения",             "rub — в рублях; pct — в процентах"),
            ("Направление",              "above — выше конкурента; below — ниже; equal — как у конкурента (значение игнорируется, ставится 0)"),
            ("Значение смещения",        "Числовое значение смещения (≥ 0). Для equal — 0 или пусто."),
            ("Ночная цена",              "да — включить ночное повышение цены для поднятия средней (перед акциями WB)"),
            ("Ночь — начало МСК",        "Начало ночного окна по Москве, формат HH:MM. Пример: 01:00"),
            ("Ночь — конец МСК",         "Конец ночного окна по Москве, формат HH:MM. Пример: 07:00"),
            ("Ночная цена продавца",     "Завышенная цена продавца на WB в ночные часы. Пример: 4999"),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        # Делаем лист данных активным
        wb.active = ws

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    @staticmethod
    def generate_costs_template() -> BytesIO:
        """Пустой шаблон для загрузки себестоимостей.
        Лист «Себестоимость» — заголовок + пустые строки.
        Лист «Инструкция» — примеры и описание.
        """
        wb = ExcelExporter.create_workbook()

        # ── Лист данных ──
        ws = wb.create_sheet("Себестоимость")
        headers = [
            "Артикул продавца *",
            "Себестоимость (₽) *",
            "ID кабинета",
            "Не учитывать маржу (да/нет)",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append(["", "", ""])
        ExcelExporter.auto_size_columns(ws, min_width=20)
        ws.freeze_panes = "A2"

        # ── Лист инструкции ──
        wi = wb.create_sheet("Инструкция")
        wi.append(["Примеры заполнения"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(["Артикул продавца *", "Себестоимость (₽) *", "ID кабинета"])
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)
        examples = [
            ["ваш-арт-001", 1500,   1],
            ["ваш-арт-002", 850.50, ""],
            ["ваш-арт-003", 3200,   2],
        ]
        for ex in examples:
            wi.append(ex)
        for row_num in (4, 5, 6):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)
        wi.append([])
        wi.append(["Поле", "Описание"])
        ExcelExporter._style_header(wi, row=8, color=COLOR_HEADER)
        notes = [
            ("A — Артикул продавца *", "vendorCode товара. Должен уже быть в системе. Обязательно."),
            ("B — Себестоимость *",    "Закупочная цена товара в рублях. Используется для расчёта маржи."),
            ("C — ID кабинета",        "Числовой ID кабинета из раздела Аккаунты. Необязательно — если пусто, применится ко всем кабинетам где есть артикул."),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        wb.active = ws
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output


    @staticmethod
    def generate_min_prices_template() -> BytesIO:
        """Пустой шаблон для загрузки минимальных цен.
        Лист «Мин цены» — заголовок + пустые строки.
        Лист «Инструкция» — примеры и описание.
        """
        wb = ExcelExporter.create_workbook()

        ws = wb.create_sheet("Мин цены")
        headers = [
            "Артикул продавца *",
            "Минимальная цена (₽) *",
            "ID кабинета",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append(["", "", ""])
        ExcelExporter.auto_size_columns(ws, min_width=20)
        ws.freeze_panes = "A2"

        wi = wb.create_sheet("Инструкция")
        wi.append(["Примеры заполнения"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(["Артикул продавца *", "Минимальная цена (₽) *", "ID кабинета"])
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)
        examples = [
            ["ваш-арт-001", 500,     1],
            ["ваш-арт-002", 1200.50, ""],
            ["ваш-арт-003", 300,     2],
        ]
        for ex in examples:
            wi.append(ex)
        for row_num in (4, 5, 6):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)
        wi.append([])
        wi.append(["Поле", "Описание"])
        ExcelExporter._style_header(wi, row=8, color=COLOR_HEADER)
        notes = [
            ("A — Артикул продавца *", "vendorCode товара. Обязательно."),
            ("B — Минимальная цена *",  "Нижняя граница цены в рублях. Репрайсер никогда не опустит цену ниже этого значения."),
            ("C — ID кабинета",         "Числовой ID кабинета из раздела Аккаунты. Необязательно — если пусто, применится ко всем кабинетам где есть артикул."),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        wb.active = ws
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output


    @staticmethod
    def generate_characteristics_template() -> BytesIO:
        """Шаблон для загрузки характеристик товаров: бренд, себестоимость, МРЦ.
        Все поля кроме артикула необязательны — заполняйте только нужные.
        """
        wb = ExcelExporter.create_workbook()

        ws = wb.create_sheet("Характеристики")
        headers = [
            "Артикул продавца *",
            "Бренд",
            "Себестоимость (₽)",
            "Мин. цена (₽)",
            "ID кабинета",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append(["", "", "", "", "", "", ""])
        ExcelExporter.auto_size_columns(ws, min_width=18)
        ws.freeze_panes = "A2"

        wi = wb.create_sheet("Инструкция")
        wi.append(["Шаблон характеристик товаров"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(headers)
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)
        examples = [
            ["ваш-арт-001", "Nike",   1500,   500,  1],
            ["ваш-арт-002", "Adidas", "",     300,  ""],
            ["ваш-арт-003", "",       850.50, "",   2],
            ["ваш-арт-004", "Nike",   "",     "",   ""],
        ]
        for ex in examples:
            wi.append(ex)
        for row_num in (4, 5, 6, 7):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)
        wi.append([])
        wi.append(["Поле", "Описание"])
        ExcelExporter._style_header(wi, row=9, color=COLOR_HEADER)
        notes = [
            ("A — Артикул продавца *", "vendorCode товара. Обязательно."),
            ("B — Бренд",              "Бренд товара. Необязательно."),
            ("C — Себестоимость",      "Закупочная цена в рублях. Необязательно."),
            ("D — Мин. цена",          "Нижняя граница цены в рублях. Необязательно."),
            ("E — ID кабинета",        "Числовой ID кабинета. Необязательно — если пусто, применится ко всем кабинетам."),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        wb.active = ws
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output


    @staticmethod
    def generate_rrp_spp_template() -> BytesIO:
        """Template for loading SPP-based RRP tiers.

        Each row defines one tier for one SKU.
        Tiers are matched by range [spp_min, spp_max).
        The last tier for a SKU may have empty spp_max (= no upper limit).
        """
        wb = ExcelExporter.create_workbook()

        ws = wb.create_sheet("РРЦ по СПП")
        headers = [
            "Артикул продавца *",
            "МРЦ — мин. цена (₽)",
            "СПП от (%) *",
            "СПП до (%) — пусто = без верхней границы",
            "Цена продавца (₽) — до скидок и СПП",
            "РРЦ — финальная цена покупателя (₽)",
            "ID кабинета",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append(["", "", "", "", "", "", "", ""])
        ExcelExporter.auto_size_columns(ws, min_width=18)
        ws.freeze_panes = "A2"

        wi = wb.create_sheet("Инструкция")
        wi.append(["Шаблон: РРЦ по уровням СПП"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append([
            "Как работает:",
            "Репрайсер выбирает РРЦ по текущему СПП товара.",
            "",
            "Диапазон: от <= СПП < до. Если «до» пусто — нет верхней границы.",
        ])
        wi.merge_cells("B3:D3")
        wi.append([])
        wi.append(headers)
        ExcelExporter._style_header(wi, row=5, color=COLOR_HEADER2)

        examples = [
            ["ваш-арт-001", 500,  0,  5,  1500,     "",   "", "да"],
            ["ваш-арт-001",  "",  5, 15,  1700,     "",   "",   ""],
            ["ваш-арт-001",  "", 15, "",  1900,     "",   "",   ""],
            ["ваш-арт-002", 300,  0, 10,  "",    3500,   1,    ""],
            ["ваш-арт-002",  "", 10, "",  "",    4000,   1,    ""],
        ]
        for ex in examples:
            wi.append(ex)
        for row_num in range(6, 6 + len(examples)):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)

        wi.append([])
        wi.append(["Поле", "Описание"])
        ExcelExporter._style_header(wi, row=6 + len(examples) + 1, color=COLOR_HEADER)
        notes = [
            ("A — Артикул продавца *",
             "vendorCode товара. Обязательно."),
            ("B — МРЦ (₽)",
             "Минимальная цена. Указывается только в первой строке артикула. "
             "Если пусто — МРЦ=0 (без ограничения снизу). "
             "Если стратегия уже настроена, МРЦ из файла игнорируется."),
            ("C — СПП от (%) *",
             "Нижняя граница СПП, включительно. Например: 0, 5, 15. Обязательно."),
            ("D — СПП до (%)",
             "Верхняя граница СПП, не включая. Пусто — ступень без верхней границы."),
            ("E — Цена продавца (₽)",
             "Прямая цена продавца на WB (до скидок и СПП). "
             "Передаётся на WB как есть. Заполните D или E, не оба сразу."),
            ("F — РРЦ — финальная цена покупателя (₽)",
             "Целевая финальная цена. Движок сам посчитает цену продавца обратным расчётом. "
             "Заполните D или E, не оба сразу."),
            ("G — ID кабинета",
             "Числовой ID кабинета. Если пусто — применится ко всем кабинетам."),
            ("H — Не учитывать маржу",
             "да — отключить защиту маржи для этого товара. "
             "Указывается только в первой строке артикула. "
             "Если пусто — защита маржи работает (по умолчанию)."),
        ]
        for note in notes:
            wi.append(list(note))
        ExcelExporter.auto_size_columns(wi)

        wb.active = ws
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    # ─────────────────── ЭКСПОРТ ТОВАРОВ ───────────────────

    @staticmethod
    def export_products_with_config(products_data: List[Dict[str, Any]]) -> BytesIO:
        """
        Экспорт всех товаров с конфигурацией и текущими ценами.
        Строки раскрашиваются по статусу репрайсера.
        """
        wb = ExcelExporter.create_workbook()
        ws = wb.create_sheet("Товары и стратегии")

        headers = [
            "Артикул продавца",
            "Название",
            "Бренд",
            "Предмет",
            "nmID",
            "Аккаунт",
            "Статус",
            "Стратегия",
            "МРЦ (₽)",
            "Макс цена (₽)",
            "РРЦ (₽)",
            "Допуск РРЦ (₽)",
            "Кошелёк в РРЦ",
            "nmID конкурента",
            "Тип смещения",
            "Направление",
            "Смещение",
            "Цена продавца (₽)",
            "Цена с СПП (₽)",
            "Обновлено",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)

        STRATEGY_LABELS = {"rrp": "РРЦ", "competitor": "Конкурент"}

        for p in products_data:
            cfg = p.get("config") or {}

            if not cfg:
                status = "Не настроен"
            elif cfg.get("is_active"):
                status = "Активен"
            else:
                status = "Отключен"

            row = [
                _safe_str(p.get("seller_sku", "")),
                _safe_str(p.get("name", "")),
                _safe_str(p.get("brand", "")),
                _safe_str(p.get("subject_name", "")),
                p.get("nm_id", ""),
                _safe_str(p.get("account_id", "")),
                status,
                STRATEGY_LABELS.get(cfg.get("strategy", ""), ""),
                float(cfg["min_price"])           if cfg.get("min_price")           else "",
                float(cfg["max_price"])           if cfg.get("max_price")           else "",
                float(cfg["rrp_price"])           if cfg.get("rrp_price")           else "",
                float(cfg["rrp_tolerance"])       if cfg.get("rrp_tolerance")       else "",
                "Да" if cfg.get("rrp_consider_wallet") else ("Нет" if cfg else ""),
                cfg.get("competitor_nm_id")       or "",
                cfg.get("competitor_offset_type") or "",
                cfg.get("competitor_offset_direction") or "",
                float(cfg["competitor_offset_value"]) if cfg.get("competitor_offset_value") else "",
                float(p["current_seller_price"])  if p.get("current_seller_price")  else "",
                float(p["current_final_price"])   if p.get("current_final_price")   else "",
                p.get("last_updated", ""),
            ]
            ws.append(row)

        # Раскраска строк по статусу
        fill_active      = PatternFill(start_color=COLOR_GREEN,  end_color=COLOR_GREEN,  fill_type="solid")
        fill_inactive    = PatternFill(start_color=COLOR_ORANGE, end_color=COLOR_ORANGE, fill_type="solid")
        fill_unconfigured = PatternFill(start_color=COLOR_GRAY,  end_color=COLOR_GRAY,   fill_type="solid")
        status_fill = {"Активен": fill_active, "Отключен": fill_inactive, "Не настроен": fill_unconfigured}
        num_cols = len(headers)
        for row_idx, p in enumerate(products_data, start=2):
            cfg = p.get("config") or {}
            if not cfg:
                status_val = "Не настроен"
            elif cfg.get("is_active"):
                status_val = "Активен"
            else:
                status_val = "Отключен"
            fill = status_fill.get(status_val)
            if fill:
                for col_idx in range(1, num_cols + 1):
                    ws.cell(row=row_idx, column=col_idx).fill = fill

        # Фиксированные ширины — быстрее auto_size для тысяч строк
        col_widths = [20, 30, 15, 20, 12, 18, 12, 14, 10, 12, 10, 12, 14, 16, 14, 14, 12, 18, 16, 18]
        for i, width in enumerate(col_widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = "A2"

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    # ─────────────────── ИСТОРИЯ / ЗАПУСК ───────────────────

    @staticmethod
    def export_price_history(
        history_data: List[Dict[str, Any]],
        filename: Optional[str] = None
    ) -> BytesIO:
        wb = ExcelExporter.create_workbook()
        ws = wb.create_sheet("История цен")

        headers = [
            "Артикул продавца", "Название товара", "nmID",
            "Цена продавца (₽)", "Финальная цена (₽)", "СПП (%)",
            "В наличии", "В промо", "Дата/время",
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)

        for item in history_data:
            ws.append([
                _safe_str(item.get("seller_sku", "")),
                _safe_str(item.get("product_name", "")),
                item.get("nm_id", ""),
                float(item.get("seller_price", 0)),
                float(item.get("final_price", 0)),
                float(item.get("spp", 0)) if item.get("spp") else "",
                "Да" if item.get("in_stock") else "Нет",
                "Да" if item.get("is_in_promo") else "Нет",
                item.get("snapshot_at", ""),
            ])

        ExcelExporter.auto_size_columns(ws)
        ws.freeze_panes = "A2"

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    @staticmethod
    def export_repricer_run(
        run_data: Dict[str, Any],
        changes: List[Dict[str, Any]]
    ) -> BytesIO:
        wb = ExcelExporter.create_workbook()

        ws_summary = wb.create_sheet("Сводка")
        ws_summary.append(["Запуск репрайсера"])
        ws_summary.append([])
        ws_summary.append(["Параметр", "Значение"])
        ExcelExporter._style_header(ws_summary, row=3)
        for k, v in [
            ("ID запуска",      run_data.get("id")),
            ("Тип",             run_data.get("run_type")),
            ("Статус",          run_data.get("status")),
            ("Начало",          run_data.get("started_at")),
            ("Завершение",      run_data.get("completed_at")),
            ("Всего товаров",   run_data.get("total_products", 0)),
            ("Обновлено",       run_data.get("updated_count", 0)),
            ("Пропущено",       run_data.get("skipped_count", 0)),
            ("Ошибок",          run_data.get("error_count", 0)),
        ]:
            ws_summary.append([k, v])

        ws_changes = wb.create_sheet("Изменения")
        headers = [
            "Артикул", "Название",
            "Старая цена продавца", "Новая цена продавца",
            "Старая финальная", "Новая финальная",
            "Код причины", "Описание", "Время",
        ]
        ws_changes.append(headers)
        ExcelExporter._style_header(ws_changes)

        for change in changes:
            ws_changes.append([
                _safe_str(change.get("seller_sku", "")),
                _safe_str(change.get("product_name", "")),
                float(change.get("old_seller_price", 0)),
                float(change.get("new_seller_price", 0)) if change.get("new_seller_price") else "",
                float(change.get("old_final_price", 0)),
                float(change.get("new_final_price", 0)) if change.get("new_final_price") else "",
                change.get("reason_code", ""),
                change.get("reason_message", ""),
                change.get("changed_at", ""),
            ])

        ExcelExporter.auto_size_columns(ws_summary)
        ExcelExporter.auto_size_columns(ws_changes)

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output


    @staticmethod
    def export_brand_report(rows):
        """Отчёт по брендам: товар, цена, маржа, РРЦ, цена до СПП, цена конкурента."""
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet("Отчёт по брендам")

        STRATEGY_LABELS = {"rrp": "РРЦ", "competitor": "Конкурент"}

        headers = [
            "Артикул",
            "Название",
            "Бренд",
            "nmID",
            "Аккаунт",
            "Стратегия",
            "Цена продавца (₽)",
            "Цена до СПП (₽)",
            "СПП (%)",
            "Остаток",
            "Цена конкурента (₽)",
            "РРЦ (₽)",
            "МРЦ (₽)",
            "Себестоимость (₽)",
            "Маржа (%)",
        ]
        header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_row = []
        for title in headers:
            cell = WriteOnlyCell(ws, value=title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            header_row.append(cell)
        ws.append(header_row)

        margin_font_red   = Font(bold=True, color="CC0000")
        margin_font_amber = Font(bold=True, color="CC7700")
        margin_font_green = Font(bold=True, color="006600")
        margin_font_blue  = Font(bold=True, color="0055BB")
        money_format = '#,##0.00'
        margin_format = '0.00"%"'
        stock_format = '0'
        money_columns = {7, 8, 11, 12, 13, 14}

        for row_data in rows:
            margin = row_data.get("margin")
            strategy = row_data.get("strategy") or ""
            values = [
                _safe_str(row_data.get("seller_sku", "")),
                _safe_str(row_data.get("name", "")),
                _safe_str(row_data.get("brand", "")),
                row_data.get("nm_id", ""),
                _safe_str(row_data.get("account", "")),
                STRATEGY_LABELS.get(strategy, strategy),
                row_data.get("seller_price") or "",
                row_data.get("final_price") or "",
                row_data.get("spp") or "",
                row_data.get("stock_quantity") or "",
                row_data.get("competitor_price") or "",
                row_data.get("rrp_price") or "",
                row_data.get("min_price") or "",
                row_data.get("cost_price") or "",
                margin if margin is not None else "",
            ]

            excel_row = []
            for idx, value in enumerate(values, start=1):
                cell = WriteOnlyCell(ws, value=value)
                if idx in money_columns and isinstance(value, (int, float)) and value != "":
                    cell.number_format = money_format
                elif idx == 10 and isinstance(value, (int, float)) and value != "":
                    cell.number_format = stock_format
                elif idx == 15 and isinstance(value, (int, float)) and value != "":
                    cell.number_format = margin_format
                    if margin < 0:
                        cell.font = margin_font_red
                    elif margin < 15:
                        cell.font = margin_font_amber
                    elif margin < 30:
                        cell.font = margin_font_green
                    else:
                        cell.font = margin_font_blue
                excel_row.append(cell)
            ws.append(excel_row)

        col_widths = [20, 30, 18, 12, 16, 12, 18, 16, 8, 10, 18, 12, 12, 16, 12]
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output


class ExcelImporter:
    """Импорт данных из Excel"""

    @staticmethod
    def import_strategy_config(file_content: bytes) -> List[Dict[str, Any]]:
        """
        Импорт конфигурации стратегий.

        Колонки (совпадают с шаблоном generate_strategy_template):
        A  seller_sku *
        B  strategy             (rrp | competitor; optional when disabling)
        C  is_active             (да/нет/true/false)
        D  min_price            (optional when only disabling an existing strategy)
        E  max_price
        F  rrp_price
        G  rrp_tolerance
        H  rrp_consider_wallet  (да/нет/true/false)
        I  competitor_nm_id
        J  competitor_offset_type
        K  competitor_offset_direction
        L  competitor_offset_value
        M  account_id            (необязательно, если артикул уникален)
        N  night_price_enabled   (да/нет, необязательно)
        O  night_start_msk       (HH:MM, необязательно)
        P  night_end_msk         (HH:MM, необязательно)
        Q  night_seller_price    (₽, необязательно)
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        # Читаем по имени листа данных; если не нашли — берём первый лист
        if "Стратегии" in wb.sheetnames:
            ws = wb["Стратегии"]
        else:
            ws = wb.worksheets[0]

        configs = []

        def to_bool(val, default=None):
            # Пустая ячейка = «не менять» (None). Раньше пустое «активен»
            # включало выключенные товары, а пустой «кошелёк» выключал учёт
            # кошелька — РРЦ начинала выдерживаться на 3% ниже.
            if val is None or not str(val).strip():
                return default
            return str(val).strip().lower() in ("true", "1", "да", "yes")

        # Определяем формат по заголовку: новый формат имеет колонку "кошелёк" (H)
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        has_wallet_col = any(
            h and ("кошелёк" in str(h).lower() or "wallet" in str(h).lower())
            for h in header_row
        )

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row[0]:
                continue

            try:
                if has_wallet_col:
                    cfg = {
                        "seller_sku":                   str(row[0]).strip(),
                        "strategy":                      str(row[1]).strip().lower() if row[1] else None,
                        "is_active":                     to_bool(row[2]),
                        "min_price":                     Decimal(str(row[3])) if row[3] is not None and str(row[3]).strip() else None,
                        "max_price":                     Decimal(str(row[4])) if row[4] is not None and str(row[4]).strip() else None,
                        "rrp_price":                     Decimal(str(row[5])) if row[5] is not None and str(row[5]).strip() else None,
                        "rrp_tolerance":                 Decimal(str(row[6])) if row[6] is not None and str(row[6]).strip() else None,
                        "rrp_consider_wallet":           to_bool(row[7]),
                        "competitor_nm_id":              int(row[8])  if row[8]  else None,
                        "competitor_offset_type":        str(row[9]).strip().lower()  if row[9]  else None,
                        "competitor_offset_direction":   str(row[10]).strip().lower() if row[10] else None,
                        "competitor_offset_value":       Decimal(str(row[11])) if row[11] is not None and str(row[11]).strip() else None,
                        "account_name_or_id":          str(row[12]).strip() if len(row) > 12 and row[12] is not None and str(row[12]).strip() else None,
                        "night_price_enabled":         to_bool(row[13]) if len(row) > 13 else None,
                        "night_start_msk":             str(row[14]).strip() if len(row) > 14 and row[14] is not None and str(row[14]).strip() else None,
                        "night_end_msk":               str(row[15]).strip() if len(row) > 15 and row[15] is not None and str(row[15]).strip() else None,
                        "night_seller_price":          Decimal(str(row[16])) if len(row) > 16 and row[16] is not None and str(row[16]).strip() else None,
                    }
                else:
                    # Старый формат без колонки кошелька (11 колонок)
                    cfg = {
                        "seller_sku":                   str(row[0]).strip(),
                        "strategy":                      str(row[1]).strip().lower() if row[1] else None,
                        "is_active":                     to_bool(row[2]),
                        "min_price":                     Decimal(str(row[3])) if row[3] is not None and str(row[3]).strip() else None,
                        "max_price":                     Decimal(str(row[4])) if row[4] is not None and str(row[4]).strip() else None,
                        "rrp_price":                     Decimal(str(row[5])) if row[5] is not None and str(row[5]).strip() else None,
                        "rrp_tolerance":                 Decimal(str(row[6])) if row[6] is not None and str(row[6]).strip() else None,
                        "rrp_consider_wallet":           None,  # колонки нет — не меняем
                        "competitor_nm_id":              int(row[7])  if row[7]  else None,
                        "competitor_offset_type":        str(row[8]).strip().lower()  if row[8]  else None,
                        "competitor_offset_direction":   str(row[9]).strip().lower()  if row[9]  else None,
                        "competitor_offset_value":       Decimal(str(row[10])) if row[10] is not None and str(row[10]).strip() else None,
                        "account_name_or_id":          str(row[11]).strip() if len(row) > 11 and row[11] is not None and str(row[11]).strip() else None,
                        "night_price_enabled":         to_bool(row[12]) if len(row) > 12 else None,
                        "night_start_msk":             str(row[13]).strip() if len(row) > 13 and row[13] is not None and str(row[13]).strip() else None,
                        "night_end_msk":               str(row[14]).strip() if len(row) > 14 and row[14] is not None and str(row[14]).strip() else None,
                        "night_seller_price":          Decimal(str(row[15])) if len(row) > 15 and row[15] is not None and str(row[15]).strip() else None,
                    }

                if not cfg["seller_sku"]:
                    raise ExcelImportError(f"Строка {row_idx}: seller_sku обязателен")
                if not cfg["strategy"] and cfg.get("is_active", True):
                    raise ExcelImportError(f"Строка {row_idx}: strategy обязателен для активной стратегии")
                if cfg["strategy"] and cfg["strategy"] not in ("rrp", "competitor"):
                    raise ExcelImportError(f"Строка {row_idx}: strategy должен быть 'rrp' или 'competitor'")
                # equal — синоним: «как у конкурента». БД хранит как above + 0.
                if cfg.get("competitor_offset_direction") == "equal":
                    cfg["competitor_offset_direction"] = "above"
                    cfg["competitor_offset_value"] = Decimal(0)

                configs.append(cfg)

            except (ValueError, TypeError) as e:
                raise ExcelImportError(f"Строка {row_idx}: ошибка парсинга — {e}")

        logger.info(f"Импортировано {len(configs)} конфигураций из Excel")
        return configs

    @staticmethod
    def import_products(file_content: bytes) -> List[Dict[str, Any]]:
        """
        Импорт товаров из Excel (шаблон generate_products_template).

        A  nm_id       (обяз. если не заполнен B)
        B  seller_sku  (обяз. если не заполнен A)
        C  account_id *
        D  name        (опционально — подтянется из WB)

        Хотя бы одно из A или B должно быть заполнено.
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        # Читаем по имени листа данных; если не нашли — берём первый лист
        if "Товары" in wb.sheetnames:
            ws = wb["Товары"]
        else:
            ws = wb.worksheets[0]

        products = []

        def _clean_str(v) -> Optional[str]:
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None

        def _clean_int(v) -> Optional[int]:
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            # Пропускаем полностью пустые строки
            if all(v is None or str(v).strip() == "" for v in row[:4]):
                continue

            # Пропускаем строки-описания (оба поля A и B нечисловые И C нечисловое)
            nm_id_raw   = _clean_str(row[0]) if len(row) > 0 else None
            sku_raw     = _clean_str(row[1]) if len(row) > 1 else None
            acc_id_raw  = row[2] if len(row) > 2 else None

            nm_id_int  = _clean_int(nm_id_raw)   # None если нечисловой
            acc_id_int = _clean_int(acc_id_raw)

            # Строка описания: колонка C не является числом и колонка A не число
            if acc_id_int is None and nm_id_int is None:
                continue

            try:
                product = {
                    "nm_id":      nm_id_int,
                    "seller_sku": sku_raw if not nm_id_int else (_clean_str(row[1]) if len(row) > 1 else None),
                    "account_id": acc_id_int,
                    "name":       _clean_str(row[3]) if len(row) > 3 else None,
                }

                # Нужен хотя бы один идентификатор
                if not product["nm_id"] and not product["seller_sku"]:
                    raise ExcelImportError(f"Строка {row_idx}: заполните nmID (колонка A) или артикул продавца (колонка B)")
                if not product["account_id"]:
                    raise ExcelImportError(f"Строка {row_idx}: ID аккаунта обязателен (колонка C)")

                products.append(product)

            except ExcelImportError:
                raise
            except (ValueError, TypeError) as e:
                raise ExcelImportError(f"Строка {row_idx}: ошибка парсинга — {e}")

        logger.info(f"Импортировано {len(products)} товаров из Excel")
        return products


    @staticmethod
    def import_costs(file_content: bytes) -> List[Dict[str, Any]]:
        """
        Импорт себестоимостей из Excel (шаблон generate_costs_template).

        A  seller_sku *
        B  cost_price *
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        if "Себестоимость" in wb.sheetnames:
            ws = wb["Себестоимость"]
        else:
            ws = wb.worksheets[0]

        costs = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or len(row) < 2:
                continue
            sku_val = row[0]
            cost_val = row[1]
            acc_val = row[2] if len(row) > 2 else None
            if sku_val is None or str(sku_val).strip() == "":
                continue
            if cost_val is None or str(cost_val).strip() == "":
                continue
            try:
                cost_price = Decimal(str(cost_val))
                if cost_price < 0:
                    raise ExcelImportError(f"Строка {row_idx}: себестоимость не может быть отрицательной")
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат себестоимости — {cost_val!r}")
            try:
                account_id = int(acc_val) if acc_val is not None and str(acc_val).strip() != "" else None
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат ID кабинета — {acc_val!r}")

            costs.append({
                "seller_sku": str(sku_val).strip(),
                "cost_price": cost_price,
                "account_id": account_id,
            })

        logger.info(f"Импортировано {len(costs)} себестоимостей из Excel")
        return costs



    @staticmethod
    def import_min_prices(file_content: bytes) -> list:
        """
        Импорт минимальных цен из Excel (шаблон generate_min_prices_template).

        A  seller_sku *
        B  min_price *
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        if "Мин цены" in wb.sheetnames:
            ws = wb["Мин цены"]
        else:
            ws = wb.worksheets[0]

        items = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or len(row) < 2:
                continue
            sku_val = row[0]
            price_val = row[1]
            acc_val = row[2] if len(row) > 2 else None
            if sku_val is None or str(sku_val).strip() == "":
                continue
            if price_val is None or str(price_val).strip() == "":
                continue
            try:
                min_price = Decimal(str(price_val))
                if min_price < 0:
                    raise ExcelImportError(f"Строка {row_idx}: минимальная цена не может быть отрицательной")
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат цены — {price_val!r}")
            try:
                account_id = int(acc_val) if acc_val is not None and str(acc_val).strip() != "" else None
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат ID кабинета — {acc_val!r}")

            items.append({
                "seller_sku": str(sku_val).strip(),
                "min_price": min_price,
                "account_id": account_id,
            })

        logger.info(f"Импортировано {len(items)} минимальных цен из Excel")
        return items


    @staticmethod
    def import_characteristics(file_content: bytes) -> list:
        """
        Импорт характеристик товаров из единого шаблона.

        A  seller_sku *
        B  brand        (необязательно)
        C  cost_price   (необязательно)
        D  min_price    (необязательно)
        E  account_id   (необязательно)
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        if "Характеристики" in wb.sheetnames:
            ws = wb["Характеристики"]
        else:
            ws = wb.worksheets[0]

        items = []

        def _dec(v):
            if v is None or str(v).strip() == "":
                return None
            try:
                return Decimal(str(v))
            except Exception:
                raise ValueError(f"не число: {v!r}")

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or len(row) < 1:
                continue
            sku_val = row[0]
            if sku_val is None or str(sku_val).strip() == "":
                continue
            brand_val    = row[1] if len(row) > 1 else None
            cost_val     = row[2] if len(row) > 2 else None
            min_val      = row[3] if len(row) > 3 else None
            acc_val      = row[4] if len(row) > 4 else None

            brand = str(brand_val).strip() if brand_val is not None and str(brand_val).strip() else None
            try:
                cost_price = _dec(cost_val)
                if cost_price is not None and cost_price < 0:
                    raise ExcelImportError(f"Строка {row_idx}: себестоимость не может быть отрицательной")
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат себестоимости — {cost_val!r}")
            try:
                min_price = _dec(min_val)
                if min_price is not None and min_price < 0:
                    raise ExcelImportError(f"Строка {row_idx}: мин. цена не может быть отрицательной")
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат мин. цены — {min_val!r}")
            try:
                account_id = int(acc_val) if acc_val is not None and str(acc_val).strip() != "" else None
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат ID кабинета — {acc_val!r}")

            if brand is None and cost_price is None and min_price is None:
                continue  # пустая строка — нечего обновлять

            items.append({
                "seller_sku": str(sku_val).strip(),
                "brand": brand,
                "cost_price": cost_price,
                "min_price": min_price,
                "account_id": account_id,
            })

        logger.info(f"Импортировано {len(items)} характеристик из Excel")
        return items


    @staticmethod
    def import_rrp_spp_tiers(file_content: bytes) -> list:
        """Import SPP-based RRP tiers from Excel (generate_rrp_spp_template).

        Supports two formats (auto-detected by column B header):

        NEW (8-col, col B header contains "МРЦ"):
        A  seller_sku *
        B  min_price        (optional, МРЦ — first row of SKU only)
        C  spp_min *
        D  spp_max          (optional, None = no upper limit)
        E  seller_price     (optional — direct WB price)
        F  rrp_price        (optional — target final buyer price)
        G  account_id       (optional)
        H  skip_margin_check (optional, да/нет — first row of SKU only)

        OLD (6-col, col B header contains "СПП"):
        A  seller_sku *
        B  spp_min *
        C  spp_max          (optional)
        D  seller_price     (optional)
        E  rrp_price        (optional)
        F  account_id       (optional)
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        if "РРЦ по СПП" in wb.sheetnames:
            ws = wb["РРЦ по СПП"]
        else:
            ws = wb.worksheets[0]

        # Auto-detect format by column B header
        header_row = [cell.value for cell in ws[1]]
        col_b_header = str(header_row[1]).lower() if len(header_row) > 1 and header_row[1] else ""
        new_format = "мрц" in col_b_header or "min" in col_b_header
        logger.info(f"import_rrp_spp_tiers: detected format = {'new' if new_format else 'old (6-col compat)'}")

        items = []

        def _dec(v, field):
            if v is None or str(v).strip() == "":
                return None
            try:
                return Decimal(str(v))
            except Exception:
                raise ExcelImportError(f"Неверный формат {field}: {v!r}")

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or len(row) < 4:
                continue
            sku_val = row[0]
            if sku_val is None or str(sku_val).strip() == "":
                continue

            if new_format:
                # New 8-column format: A=sku, B=min_price, C=spp_min, D=spp_max,
                #                     E=seller_price, F=rrp, G=account, H=skip_margin
                min_price_val    = row[1] if len(row) > 1 else None
                spp_min_val      = row[2] if len(row) > 2 else None
                spp_max_val      = row[3] if len(row) > 3 else None
                seller_price_val = row[4] if len(row) > 4 else None
                rrp_val          = row[5] if len(row) > 5 else None
                acc_val          = row[6] if len(row) > 6 else None
                skip_margin_val  = row[7] if len(row) > 7 else None
            else:
                # Old 6-column format (no МРЦ, no skip_margin): A=sku, B=spp_min,
                #                     C=spp_max, D=seller_price, E=rrp, F=account
                min_price_val    = None
                spp_min_val      = row[1] if len(row) > 1 else None
                spp_max_val      = row[2] if len(row) > 2 else None
                seller_price_val = row[3] if len(row) > 3 else None
                rrp_val          = row[4] if len(row) > 4 else None
                acc_val          = row[5] if len(row) > 5 else None
                skip_margin_val  = None

            min_price_str = str(min_price_val).strip() if min_price_val is not None else ""
            if spp_min_val is None or str(spp_min_val).strip() == "":
                raise ExcelImportError(f"Строка {row_idx}: 'СПП от' обязательно")
            seller_price_str = str(seller_price_val).strip() if seller_price_val is not None else ""
            rrp_str = str(rrp_val).strip() if rrp_val is not None else ""
            if not seller_price_str and not rrp_str:
                raise ExcelImportError(
                    f"Строка {row_idx}: заполните 'Цена продавца' (колонка D) "
                    f"или 'РРЦ' (колонка E) — одно из двух"
                )
            if seller_price_str and rrp_str:
                raise ExcelImportError(
                    f"Строка {row_idx}: заполните только одно — 'Цена продавца' или 'РРЦ', не оба"
                )

            try:
                spp_min = _dec(spp_min_val, "СПП от")
                if spp_min < 0:
                    raise ExcelImportError(f"Строка {row_idx}: СПП от не может быть отрицательным")
            except ExcelImportError:
                raise
            except Exception:
                raise ExcelImportError(f"Строка {row_idx}: неверный формат 'СПП от': {spp_min_val!r}")

            spp_max_str = str(spp_max_val).strip() if spp_max_val is not None else ""
            spp_max = _dec(spp_max_val, "СПП до") if spp_max_str else None
            if spp_max is not None and spp_max <= spp_min:
                raise ExcelImportError(
                    f"Строка {row_idx}: 'СПП до' ({spp_max}) должно быть больше 'СПП от' ({spp_min})"
                )

            seller_price = None
            rrp_price = None
            try:
                if seller_price_str:
                    seller_price = _dec(seller_price_val, "Цена продавца")
                    if seller_price <= 0:
                        raise ExcelImportError(f"Строка {row_idx}: цена продавца должна быть > 0")
                elif rrp_str:
                    rrp_price = _dec(rrp_val, "РРЦ")
                    if rrp_price <= 0:
                        raise ExcelImportError(f"Строка {row_idx}: РРЦ должен быть > 0")
            except ExcelImportError:
                raise
            except Exception:
                raise ExcelImportError(f"Строка {row_idx}: неверный формат цены в строке")

            try:
                account_id = int(acc_val) if acc_val is not None and str(acc_val).strip() != "" else None
            except (ValueError, TypeError):
                raise ExcelImportError(f"Строка {row_idx}: неверный формат ID кабинета: {acc_val!r}")

            try:
                min_price_parsed = Decimal(str(min_price_val)) if min_price_str else None
            except Exception:
                min_price_parsed = None
            skip_margin_str = str(skip_margin_val).strip().lower() if skip_margin_val is not None else ""
            skip_margin_check = skip_margin_str in ("да", "yes", "true", "1")
            items.append({
                "seller_sku":      str(sku_val).strip(),
                "skip_margin_check": skip_margin_check,
                "min_price":       min_price_parsed,
                "spp_min":      spp_min,
                "spp_max":      spp_max,
                "seller_price": seller_price,
                "rrp_price":    rrp_price,
                "account_id":   account_id,
            })

        logger.info(f"Импортировано {len(items)} ступеней РРЦ по СПП из Excel")
        return items

# Singleton instances
excel_exporter = ExcelExporter()
excel_importer = ExcelImporter()
