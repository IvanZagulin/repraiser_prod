"""
Unit tests for the `_strategy_competitor` method and competitor selection rules.

Mocks `_cached_parse_product` so no Redis/WB API is touched: the tests
focus purely on how the engine picks the winning competitor from a list and
how it applies offsets (rub/pct, above/below) to produce a target price.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest

from app.services.repricer_engine import RepricerEngine


def _make_engine_with_settings():
    engine = RepricerEngine(db=MagicMock(), user_id=None)
    engine._user_settings = {}
    # Не использовать map → _get_competitor_price пойдёт в БД-фолбэк
    engine._competitor_entries_map = None
    return engine


def _comp(final_price, in_stock=True, seller_price=None):
    """Фейковый снапшот конкурента, имитирующий результат _cached_parse_product."""
    return SimpleNamespace(
        final_price=Decimal(str(final_price)),
        seller_price=Decimal(str(seller_price or final_price)),
        in_stock=in_stock,
        catalog_stale=False,
    )


def _make_config(
    config_id=1,
    competitor_nm_id=None,
    competitor_select_rule="min_price",
    offset_type="rub",
    offset_direction="below",
    offset_value="0",
):
    return SimpleNamespace(
        id=config_id,
        competitor_nm_id=competitor_nm_id,
        competitor_select_rule=competitor_select_rule,
        competitor_offset_type=offset_type,
        competitor_offset_direction=offset_direction,
        competitor_offset_value=Decimal(offset_value),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# _get_competitor_price — выбор конкурента из списка по правилу
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCompetitorPriceMinPrice:
    """rule = 'min_price': берём самого дешёвого in_stock конкурента."""

    def test_picks_cheapest_in_stock(self):
        engine = _make_engine_with_settings()
        # Map с тремя конкурентами для config_id=1
        engine._competitor_entries_map = {1: [111, 222, 333]}
        config = _make_config(competitor_select_rule="min_price")

        snapshots = {
            111: _comp(1000),
            222: _comp(800),   # cheapest
            333: _comp(1200),
        }

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, basic = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("800")
        assert winner == 222

    def test_skips_out_of_stock(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222]}
        config = _make_config(competitor_select_rule="min_price")

        snapshots = {
            111: _comp(500, in_stock=False),   # дешевле, но out of stock
            222: _comp(900, in_stock=True),
        }

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("900")
        assert winner == 222

    def test_all_out_of_stock_returns_none(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222]}
        config = _make_config(competitor_select_rule="min_price")

        snapshots = {nm: _comp(500, in_stock=False) for nm in (111, 222)}

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price is None
        assert winner is None


class TestGetCompetitorPriceCheapestInstock:
    """rule = 'cheapest_instock': аналогично, но строже к статусу в наличии."""

    def test_picks_cheapest_among_in_stock(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222, 333]}
        config = _make_config(competitor_select_rule="cheapest_instock")

        snapshots = {
            111: _comp(400, in_stock=False),   # цена ниже но не в наличии
            222: _comp(700, in_stock=True),    # самый дешёвый из in_stock
            333: _comp(1100, in_stock=True),
        }

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("700")
        assert winner == 222

    def test_no_in_stock_returns_none(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111]}
        config = _make_config(competitor_select_rule="cheapest_instock")

        async def fake_parse(nm_id):
            return _comp(500, in_stock=False)

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price is None
        assert winner is None


class TestGetCompetitorPriceAvgPrice:
    """rule = 'avg_price': среднее по всем валидным снапшотам."""

    def test_averages_finals(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222, 333]}
        config = _make_config(competitor_select_rule="avg_price")

        snapshots = {
            111: _comp(900),
            222: _comp(1000),
            333: _comp(1100),  # avg = 1000
        }

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("1000")
        # avg_price не выбирает конкретного победителя
        assert winner is None


class TestGetCompetitorPriceFallbacks:
    """Fallback-поведение: пустой список, legacy single competitor."""

    def test_no_competitors_returns_none(self):
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: []}  # пусто
        config = _make_config(competitor_nm_id=None)

        price, winner, _ = asyncio.run(engine._get_competitor_price(config))
        assert price is None
        assert winner is None

    def test_legacy_competitor_nm_id_used_when_no_entries(self):
        """Если competitor_entries пуст, но есть legacy competitor_nm_id — используем его."""
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: []}
        config = _make_config(competitor_nm_id=555, competitor_select_rule="min_price")

        async def fake_parse(nm_id):
            assert nm_id == 555
            return _comp(750)

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("750")
        assert winner == 555
