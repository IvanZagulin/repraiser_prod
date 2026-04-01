import logging
from typing import List, Dict, Any, Optional
from io import BytesIO
from datetime import datetime
from decimal import Decimal
import openpyxl
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
            "Стратегия * (rrp/competitor)",
            "Активен (да/нет)",
            "МРЦ — мин цена * (₽)",
            "Макс цена (₽)",
            "РРЦ (₽)",
            "Допуск РРЦ (₽)",
            "Кошелёк в РРЦ (да/нет)",
            "nmID конкурента",
            "Тип смещения (rub/pct)",
            "Направление (above/below)",
            "Значение смещения",
            "Кабинет (название)",
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

        ex_rrp        = ["ваш-арт-001", "rrp",        "да", 500, 3000, 1999, 5,  "нет", "",         "",      "",       "", "Кассандра"]
        ex_competitor = ["ваш-арт-002", "competitor",  "да", 300, "",   "",   "",  "",    987654321, "rub",  "below",  50, "Астарта"]
        wi.append(ex_rrp)
        wi.append(ex_competitor)
        for row_num in (4, 5):
            for cell in wi[row_num]:
                cell.font = Font(italic=True, color=COLOR_EXAMPLE)

        wi.append([])
        wi.append(["Поле", "Допустимые значения / Описание"])
        ExcelExporter._style_header(wi, row=7, color=COLOR_HEADER)
        notes = [
            ("Артикул продавца *",       "vendorCode — должен уже быть в системе. Обязательно."),
            ("Кабинет (название)",           "Название кабинета WB. Обязательно если один артикул есть в нескольких кабинетах."),
            ("Стратегия *",              "rrp — удержание РРЦ; competitor — привязка к конкуренту"),
            ("Активен",                  "да / нет (по умолчанию: да)"),
            ("МРЦ — мин цена *",         "Нижняя граница цены. Репрайсер не опустит ниже. Обязательно."),
            ("Макс цена",                "Верхняя граница цены (необязательно)"),
            ("РРЦ",                      "Рекомендованная розничная цена (только для стратегии rrp)"),
            ("Допуск РРЦ",               "Диапазон ±₽ от РРЦ (по умолчанию: 5)"),
            ("Кошелёк в РРЦ",            "да — учитывать скидку кошелька WB при расчёте (только rrp)"),
            ("nmID конкурента",          "Артикул WB конкурента (только для competitor)"),
            ("Тип смещения",             "rub — в рублях; pct — в процентах"),
            ("Направление",              "above — выше конкурента; below — ниже"),
            ("Значение смещения",        "Числовое значение смещения (≥ 0)"),
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
        ]
        ws.append(headers)
        ExcelExporter._style_header(ws)
        for _ in range(5):
            ws.append(["", ""])
        ExcelExporter.auto_size_columns(ws, min_width=20)
        ws.freeze_panes = "A2"

        # ── Лист инструкции ──
        wi = wb.create_sheet("Инструкция")
        wi.append(["Примеры заполнения"])
        wi["A1"].font = Font(bold=True, size=12)
        wi.append([])
        wi.append(["Артикул продавца *", "Себестоимость (₽) *"])
        ExcelExporter._style_header(wi, row=3, color=COLOR_HEADER2)
        examples = [
            ["ваш-арт-001", 1500],
            ["ваш-арт-002", 850.50],
            ["ваш-арт-003", 3200],
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
                p.get("nm_id", ""),
                _safe_str(p.get("account_name", "")),
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
        status_fill = {
            "Активен":      PatternFill(start_color=COLOR_GREEN,  end_color=COLOR_GREEN,  fill_type="solid"),
            "Отключен":     PatternFill(start_color=COLOR_ORANGE, end_color=COLOR_ORANGE, fill_type="solid"),
            "Не настроен":  PatternFill(start_color=COLOR_GRAY,   end_color=COLOR_GRAY,   fill_type="solid"),
        }
        for row_idx in range(2, ws.max_row + 1):
            status_val = ws.cell(row=row_idx, column=5).value
            fill = status_fill.get(status_val)
            if fill:
                for cell in ws[row_idx]:
                    cell.fill = fill

        ExcelExporter.auto_size_columns(ws)
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


class ExcelImporter:
    """Импорт данных из Excel"""

    @staticmethod
    def import_strategy_config(file_content: bytes) -> List[Dict[str, Any]]:
        """
        Импорт конфигурации стратегий.

        Колонки (совпадают с шаблоном generate_strategy_template):
        A  seller_sku *
        B  strategy *           (rrp | competitor)
        C  is_active             (да/нет/true/false)
        D  min_price *
        E  max_price
        F  rrp_price
        G  rrp_tolerance
        H  rrp_consider_wallet  (да/нет/true/false)
        I  competitor_nm_id
        J  competitor_offset_type
        K  competitor_offset_direction
        L  competitor_offset_value
        M  account_name            (необязательно, если артикул уникален)
        """
        wb = openpyxl.load_workbook(BytesIO(file_content))
        # Читаем по имени листа данных; если не нашли — берём первый лист
        if "Стратегии" in wb.sheetnames:
            ws = wb["Стратегии"]
        else:
            ws = wb.worksheets[0]

        configs = []

        def to_bool(val, default=True):
            if val is None:
                return default
            return str(val).strip().lower() in ("true", "1", "да", "yes")

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row[0]:
                continue

            # Поддерживаем оба формата: старый (11 колонок) и новый (12 колонок с кошельком)
            has_wallet_col = len(row) >= 12 and row[7] is not None and str(row[7]).strip().lower() in (
                "true", "false", "да", "нет", "yes", "no", "1", "0", ""
            )

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
                        "rrp_consider_wallet":           to_bool(row[7], default=False),
                        "competitor_nm_id":              int(row[8])  if row[8]  else None,
                        "competitor_offset_type":        str(row[9]).strip().lower()  if row[9]  else None,
                        "competitor_offset_direction":   str(row[10]).strip().lower() if row[10] else None,
                        "competitor_offset_value":       Decimal(str(row[11])) if row[11] is not None and str(row[11]).strip() else None,
                        "account_name":                  str(row[12]).strip() if len(row) > 12 and row[12] else None,
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
                        "rrp_consider_wallet":           False,
                        "competitor_nm_id":              int(row[7])  if row[7]  else None,
                        "competitor_offset_type":        str(row[8]).strip().lower()  if row[8]  else None,
                        "competitor_offset_direction":   str(row[9]).strip().lower()  if row[9]  else None,
                        "competitor_offset_value":       Decimal(str(row[10])) if row[10] is not None and str(row[10]).strip() else None,
                        "account_name":                  str(row[11]).strip() if len(row) > 11 and row[11] else None,
                    }

                if not cfg["seller_sku"]:
                    raise ExcelImportError(f"Строка {row_idx}: seller_sku обязателен")
                if not cfg["strategy"]:
                    raise ExcelImportError(f"Строка {row_idx}: strategy обязателен")
                if cfg["strategy"] not in ("rrp", "competitor"):
                    raise ExcelImportError(f"Строка {row_idx}: strategy должен быть 'rrp' или 'competitor'")
                if cfg["min_price"] is None:
                    raise ExcelImportError(f"Строка {row_idx}: min_price обязателен")

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

            costs.append({
                "seller_sku": str(sku_val).strip(),
                "cost_price": cost_price,
            })

        logger.info(f"Импортировано {len(costs)} себестоимостей из Excel")
        return costs


# Singleton instances
excel_exporter = ExcelExporter()
excel_importer = ExcelImporter()
