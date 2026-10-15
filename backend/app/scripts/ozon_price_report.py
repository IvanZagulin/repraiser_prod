"""Разовый отчёт по кабинету Ozon: где витрина сильнее всего уходит от нашей цены.

Только чтение: скрипт ничего не пишет ни в Ozon, ни в БД (из БД берёт лишь
учётные данные аккаунта).

Запуск внутри контейнера бэкенда:

    python -m app.scripts.ozon_price_report --account-id 12 --top 30
    python -m app.scripts.ozon_price_report --account-id 12 --csv /app/ozon_report.csv
"""

import argparse
import asyncio
import csv
import statistics
import sys
from decimal import Decimal
from typing import List, Optional

from app.database import SessionLocal
from app.models.account import Account
from app.services.ozon_api import create_ozon_client
from app.services.ozon_buyer_price import estimate_from_price_data


class Row:
    __slots__ = ("product_id", "offer_id", "name", "seller_price", "buyer_price",
                 "ratio", "spread", "sources", "ozon_min", "external_min", "commission")

    def __init__(self, price_data, estimate):
        self.product_id = price_data.product_id
        self.offer_id = price_data.offer_id
        self.name = price_data.name
        self.seller_price = price_data.price
        self.buyer_price = estimate.buyer_price if estimate else None
        self.ratio = estimate.ratio_to(price_data.price) if estimate else None
        self.spread = estimate.spread_pct if estimate else None
        self.sources = estimate.sources if estimate else 0
        self.ozon_min = price_data.ozon_min_price or None
        self.external_min = price_data.external_min_price or None
        self.commission = price_data.sales_percent_fbs or None


async def collect(client_id: str, api_key: str, visibility: str) -> List[Row]:
    rows: List[Row] = []
    async with create_ozon_client(client_id, api_key) as client:
        async for page in client.iter_all_prices(visibility=visibility):
            for price_data in page:
                rows.append(Row(price_data, estimate_from_price_data(price_data)))
            print(f"  ...выгружено {len(rows)}", file=sys.stderr)
    return rows


async def fill_names(client_id: str, api_key: str, rows: List[Row]) -> None:
    """Догружает названия: /v5/product/info/prices их не отдаёт.

    Вызывается только для строк, которые реально попадут в вывод, — на
    весь кабинет это были бы лишние два десятка запросов ради косметики.
    """
    ids = [r.product_id for r in rows if r.product_id and not r.name]
    if not ids:
        return
    names = {}
    async with create_ozon_client(client_id, api_key) as client:
        for chunk_start in range(0, len(ids), 1000):
            chunk = ids[chunk_start:chunk_start + 1000]
            for item in await client.get_product_list(product_ids=chunk):
                pid = item.get("id") or item.get("product_id")
                if pid:
                    names[int(pid)] = item.get("name") or ""
    for row in rows:
        if not row.name:
            row.name = names.get(row.product_id, "")


def _fmt(value: Optional[Decimal], width: int = 9) -> str:
    return "—".rjust(width) if value is None else f"{value:>{width}.2f}"


def pick_worst(rows: List[Row], top: int) -> List[Row]:
    reliable = [r for r in rows if r.ratio is not None
                and r.spread is not None and r.spread <= 5]
    return sorted(reliable, key=lambda r: r.ratio)[:top]


def report(rows: List[Row], worst: List[Row]) -> None:
    total = len(rows)
    priced = [r for r in rows if r.ratio is not None]
    reliable = [r for r in priced if r.spread is not None and r.spread <= 5]

    print(f"\nТоваров в кабинете:              {total}")
    print(f"Цена покупателя посчитана:        {len(priced)} ({len(priced)/total*100:.1f} %)"
          if total else "")
    print(f"  из них оценка надёжна (<=5 %):  {len(reliable)}")
    print(f"  индексов нет вовсе:             {total - len(priced)}")

    if not reliable:
        print("\nНадёжных оценок нет — отчёт по отрыву цен построить не на чем.")
        return

    ratios = sorted(float(r.ratio) for r in reliable)
    print("\nk = цена покупателя / наша цена (по надёжным оценкам):")
    print(f"  медиана {statistics.median(ratios):.3f}   "
          f"min {ratios[0]:.3f}   max {ratios[-1]:.3f}")
    for q in (0.10, 0.25, 0.75, 0.90):
        print(f"  p{int(q*100):<3} {ratios[int(len(ratios)*q)]:.3f}")

    if not worst:
        return
    print(f"\nТоп-{len(worst)} товаров с наибольшим отрывом витрины от нашей цены:\n")
    print(f"{'offer_id':<12} {'наша':>9} {'витрина':>9} {'k':>6} "
          f"{'min Ozon':>9} {'min внеш':>9} {'разбр%':>7}  название")
    print("-" * 110)
    for r in worst:
        print(f"{r.offer_id:<12} {_fmt(r.seller_price)} {_fmt(r.buyer_price)} "
              f"{float(r.ratio):>6.3f} {_fmt(r.ozon_min)} {_fmt(r.external_min)} "
              f"{float(r.spread):>7.2f}  {(r.name or '')[:40]}")


def write_csv(rows: List[Row], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "product_id", "offer_id", "name", "seller_price", "buyer_price_estimate",
            "coinvest_ratio", "spread_pct", "sources",
            "ozon_competitor_min", "external_competitor_min", "commission_fbs_pct",
        ])
        for r in rows:
            writer.writerow([
                r.product_id, r.offer_id, r.name, r.seller_price, r.buyer_price,
                r.ratio, r.spread, r.sources, r.ozon_min, r.external_min, r.commission,
            ])
    print(f"\nCSV записан: {path} ({len(rows)} строк)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--visibility", default="IN_SALE",
                        help="IN_SALE (по умолчанию) или ALL")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--csv", dest="csv_path", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == args.account_id).first()
        if not account:
            print(f"Аккаунт {args.account_id} не найден", file=sys.stderr)
            return 1
        if account.marketplace != "ozon":
            print(f"Аккаунт {args.account_id} — {account.marketplace}, не Ozon", file=sys.stderr)
            return 1
        if not account.ozon_client_id or not account.ozon_api_key:
            print(f"У аккаунта {args.account_id} нет учётных данных Ozon", file=sys.stderr)
            return 1
        client_id, api_key, name = account.ozon_client_id, account.ozon_api_key, account.name
    finally:
        db.close()

    print(f"Аккаунт {args.account_id} ({name}), visibility={args.visibility}", file=sys.stderr)
    rows = asyncio.run(collect(client_id, api_key, args.visibility))
    if not rows:
        print("Кабинет пуст или Ozon ничего не вернул", file=sys.stderr)
        return 1

    worst = pick_worst(rows, args.top)
    # Названия нужны только для того, что увидит человек; для CSV — по всем.
    asyncio.run(fill_names(client_id, api_key, rows if args.csv_path else worst))

    report(rows, worst)
    if args.csv_path:
        write_csv(rows, args.csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
