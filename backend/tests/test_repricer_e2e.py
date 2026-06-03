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

import pytest

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

    def test_total_products_equals_input(
        self, db_session, test_user, test_account, test_product, test_product_config
    ):
        """RepricerRun.total_products считает обработанные продукты."""
        engine = RepricerEngine(db_session, user_id=test_user.id)
        run = asyncio.run(
            engine.run_repricer(
                product_ids=[test_product.id],
                run_type="manual",
                dry_run=True,
            )
        )
        assert run.total_products == 1
        # updated + skipped + errors не превышает total
        assert run.updated_count + run.skipped_count + run.error_count == run.total_products

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
