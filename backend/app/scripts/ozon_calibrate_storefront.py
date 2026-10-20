"""Калибровка поправки индекс→реальная витрина для Ozon.

ОСТОРОЖНО, скрипт почти не нужен и легко делает хуже. Цена покупателя теперь
берётся прямо из отчёта Ozon («Цена на сайте», app/services/ozon_site_price.py),
и пока отчёт доступен, поправка не применяется вовсе. Она осталась только для
запасного пути — когда отчёт не собрался и витрину приходится восстанавливать
из ценового индекса.

И главная ловушка: **цена на карточке зависит от региона, а «Цена на сайте»
в отчёте — нет**. Калибровка 15.10.2026 сняла карточки не в том регионе и
записала factor 0.9774 Барсику и 0.9952 Lucky Sophie. Индекс при этом считал
верно (3786 против 3784 по отчёту), а поправка утягивала оценку на 83 ₽ вниз —
из-за чего реальный перебор над РРЦ в 104 ₽ выглядел «в коридоре» и никогда
не исправлялся. 20.10.2026 обе поправки удалены.

Так что замерять витрину можно только в том же регионе, цену которого отдаёт
отчёт (сверьтесь: «Цена на сайте» из отчёта должна совпасть с карточкой).

    ozon_storefront_factor = реальная_витрина / витрина_по_индексу

Запуск внутри контейнера бэкенда:

    python -m app.scripts.ozon_calibrate_storefront --account-id 12 \
        3887328=4880 3883678=3325

где слева offer_id (seller_sku), справа — реальная цена с витрины (с картой).
"""

import argparse
import asyncio
import sys
from decimal import Decimal, InvalidOperation

from app.database import SessionLocal
from app.models.account import Account
from app.models.product import Product
from app.models.product_config import ProductConfig
from app.services.ozon_api import create_ozon_client
from app.services.ozon_buyer_price import estimate_from_price_data

# Поправка за пределами этого коридора — почти наверняка ошибка замера
# (не та карточка, регион, опечатка), такую не записываем.
FACTOR_MIN, FACTOR_MAX = Decimal("0.80"), Decimal("1.20")


def _parse_pairs(items):
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"Ожидался формат offer=price, получено: {it!r}")
        offer, price = it.split("=", 1)
        try:
            out[offer.strip()] = Decimal(price.strip())
        except InvalidOperation:
            raise SystemExit(f"Не цена: {price!r}")
    return out


async def _index_storefronts(account, offers):
    async with create_ozon_client(account.ozon_client_id, account.ozon_api_key) as c:
        prices = await c.get_prices(offer_ids=offers)
    result = {}
    for pd in prices:
        est = estimate_from_price_data(pd)
        if est is not None:
            result[pd.offer_id] = est.buyer_price
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("pairs", nargs="+", help="offer_id=реальная_витрина")
    args = parser.parse_args()

    targets = _parse_pairs(args.pairs)

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == args.account_id).first()
        if not account or account.marketplace != "ozon":
            print(f"Аккаунт {args.account_id} не Ozon", file=sys.stderr)
            return 1

        index_sf = asyncio.run(_index_storefronts(account, list(targets)))

        updated = 0
        for offer, real in targets.items():
            index = index_sf.get(offer)
            if not index or index <= 0:
                print(f"{offer}: витрина по индексу недоступна — пропуск")
                continue
            factor = (real / index).quantize(Decimal("0.0001"))
            if not (FACTOR_MIN <= factor <= FACTOR_MAX):
                print(f"{offer}: фактор {factor} вне [{FACTOR_MIN};{FACTOR_MAX}] — "
                      f"похоже на ошибку замера, не записываю "
                      f"(реальная {real}, индекс {index})")
                continue

            product = (db.query(Product)
                       .filter(Product.account_id == args.account_id,
                               Product.seller_sku == offer)
                       .first())
            if not product:
                print(f"{offer}: товар не заведён в репрайсере — пропуск")
                continue
            cfg = (db.query(ProductConfig)
                   .filter(ProductConfig.product_id == product.id).first())
            if not cfg:
                print(f"{offer}: нет конфигурации — пропуск")
                continue

            old = cfg.ozon_storefront_factor
            cfg.ozon_storefront_factor = factor
            print(f"{offer}: реальная {real}, индекс {index} -> factor {factor} "
                  f"(было {old})")
            updated += 1

        db.commit()
        print(f"\nЗаписано поправок: {updated}")
        return 0
    except Exception as exc:
        db.rollback()
        print("ОШИБКА, откат:", exc, file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
