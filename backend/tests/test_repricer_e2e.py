"""
End-to-end test for the full repricing cycle.

Uses dry_run=True mode so the engine:
  - bypasses real WB API calls (token check, price fetch, price update)
  - injects synthetic current_data per product
  - still goes through: product query → strategy → limit application →
    RepricerRun creation and finalisation

This proves the orchestration glue (run_repricer) works end-to-end, complementing
the unit tests of individual math helpers in test_repricer_engine.py.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.product import Product
from app.models.product_config import ProductConfig
from app.models.repricer_run import RepricerRun
from app.services.repricer_engine import RepricerEngine


class TestRunRepricerDryRun:
    """Full cycle of `run_repricer` against an in-memory SQLite DB."""

    def test_completes_with_status_completed(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """Один активный продукт + RRP-конфиг → run завершается без ошибок."""
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="manual",
                dry_run=True,
            )
        )
        assert isinstance(run, RepricerRun)
        assert run.status == "completed"
        assert run.user_id == test_user.id
        assert run.run_type == "manual"
        assert run.completed_at is not None

    def test_dry_run_does_not_error_on_clean_path(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """Регрессия: dry_run-блок в _decide_product должен использовать Decimal.

        Если кто-то снова заменит Decimal на int в фиктивных current_data,
        арифметика `float / Decimal` в _calculate_seller_price_from_final
        упадёт TypeError и весь товар улетит в error_count. Этот тест ловит
        такую регрессию до прода.
        """
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="manual",
                dry_run=True,
            )
        )
        assert run.error_count == 0, (
            f"dry_run упал {run.error_count} раз — проверь типы в current_data "
            "(должны быть Decimal, не int/float)"
        )

    def test_total_products_at_least_one(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """RepricerRun.total_products считает обработанные продукты.

        Точная цифра зависит от внутренней логики чанкования / повторных проходов
        движка — поэтому проверяем «хотя бы 1», не равенство.
        """
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="manual",
                dry_run=True,
            )
        )
        assert run.total_products >= 1
        # Каждый продукт идёт в одно из трёх вёдер
        assert run.updated_count + run.skipped_count + run.error_count >= run.total_products

    def test_no_products_returns_no_run(self, db_session, test_user):
        """Если у пользователя нет активных продуктов — run не создаётся."""
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[999999],  # несуществующий id
                run_type="manual",
                dry_run=True,
            )
        )
        # Контракт: либо None (нет продуктов), либо total=0
        assert run is None or run.total_products == 0

    def test_inactive_config_excluded(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """Деактивированный ProductConfig не должен попадать в выборку."""
        test_product_config.is_active = False
        db_session.commit()

        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="manual",
                dry_run=True,
            )
        )
        # Никакой работы не должно быть сделано
        assert run is None or run.total_products == 0
        assert run is None or run.updated_count == 0

    def test_scheduled_run_type_persisted(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """run_type='scheduled' должен сохраняться в RepricerRun."""
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="scheduled",
                dry_run=True,
            )
        )
        assert run is not None
        assert run.run_type == "scheduled"

    def test_mass_catalog_unavailable_marks_run_failed(
        self, db_session, test_user, test_account, monkeypatch
    ):
        """A tunnel/catalog outage must not look like a successful zero-update run."""
        product_ids = []
        for idx in range(101):
            product = Product(
                account_id=test_account.id,
                nm_id=900_000_000 + idx,
                seller_sku=f"OUTAGE-{idx}",
                name=f"Outage product {idx}",
                cost_price=Decimal("100.00"),
            )
            db_session.add(product)
            db_session.flush()
            db_session.add(ProductConfig(
                product_id=product.id,
                strategy="rrp",
                is_active=True,
                min_price=Decimal("100.00"),
                max_price=Decimal("2000.00"),
                rrp_price=Decimal("1000.00"),
            ))
            product_ids.append(product.id)
        db_session.commit()

        async def _catalog_unavailable(self, product, config, dry_run=False):
            return {
                "product": product,
                "needs_update": False,
                "skip_code": "CATALOG_UNAVAILABLE",
                "current_data": None,
                "new_price": None,
                "limit_reason": None,
                "reason_msg": "catalog unavailable",
            }

        monkeypatch.setattr(RepricerEngine, "_decide_product", _catalog_unavailable)
        monkeypatch.setattr(RepricerEngine, "_prefetch_all_prices_once", AsyncMock())

        engine = RepricerEngine(db_session, user_id=test_user.id)
        with patch("app.services.repricer_engine.catalog_parser.parse_batch_ids", new=AsyncMock(return_value={})), \
             patch("app.services.repricer_engine.telegram_notifier.notify_catalog_parse_errors", new=AsyncMock()), \
             patch("app.services.repricer_engine.telegram_notifier.notify_zero_updates", new=AsyncMock()):
            run = asyncio.run(
                engine.run_repricer(
                    product_ids=product_ids,
                    run_type="scheduled",
                    dry_run=False,
                )
            )

        assert run.status == "failed"
        assert run.updated_count == 0
        assert run.skipped_count == 101
        assert "WB catalog unavailable" in run.error_message
