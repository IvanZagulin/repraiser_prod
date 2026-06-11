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


    def test_avg_excludes_oos(self):
        """avg_price должен исключать OOS конкурентов из среднего."""
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222, 333]}
        config = _make_config(competitor_select_rule="avg_price")

        snapshots = {
            111: _comp(900),
            222: _comp(100, in_stock=False),  # OOS — не должен входить в среднее
            333: _comp(1100),  # avg(900, 1100) = 1000
        }

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price == Decimal("1000")

    def test_avg_all_oos_returns_none(self):
        """avg_price при всех OOS возвращает None."""
        engine = _make_engine_with_settings()
        engine._competitor_entries_map = {1: [111, 222]}
        config = _make_config(competitor_select_rule="avg_price")

        snapshots = {nm: _comp(500, in_stock=False) for nm in (111, 222)}

        async def fake_parse(nm_id):
            return snapshots[nm_id]

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            price, winner, _ = asyncio.run(engine._get_competitor_price(config))

        assert price is None


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


# ═══════════════════════════════════════════════════════════════════════════════
# _strategy_competitor — расчёт целевой цены продавца с учётом эффективного СПП
# ═══════════════════════════════════════════════════════════════════════════════

def _run_strategy(engine, config, current_data, competitor_final):
    """Прогоняет _strategy_competitor с замоканным конкурентом."""
    engine._competitor_entries_map = {config.id: [999]}

    async def fake_parse(nm_id):
        return _comp(competitor_final)

    product = SimpleNamespace(nm_id=111, seller_sku="SKU", config=config)
    with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
        return asyncio.run(engine._strategy_competitor(product, config, current_data))


class TestStrategyCompetitorCorridor:
    """Пол + мягкий потолок + эффективный СПП (регрессия артикулов 3891012/3874932).

    Пол = конкурент + max(0.5%, 1₽). Ниже пола — поднимаем. Выше пола (включая
    выше потолка) — НЕ трогаем: мягкий потолок, т.к. снижение может провалить
    цену в "мёртвую зону" СПП ниже конкурента.
    """

    def test_raises_from_under_floor(self):
        """final=1588 < конкурент=1593 → под полом, поднимаем к полу через eff_spp.

        Пол = 1593 + max(1593*0.5%=7.965, 1) = 1600.965. eff_spp=34.22%.
        round_up(1600.965/0.657829)=2434 (> текущей 2414).
        """
        engine = _make_engine_with_settings()
        config = _make_config(offset_type="rub", offset_direction="above", offset_value="0")
        current_data = {
            "seller_price": Decimal("2414"),
            "discounted_price": Decimal("2414"),  # discount=0 → discounted==seller
            "final_price": Decimal("1588"),
            "discount": Decimal("0"),
            "spp": Decimal("34"),  # округлённый true_spp
        }

        new_seller, reason_code, _msg, comp_price, winner = _run_strategy(
            engine, config, current_data, competitor_final=1593
        )

        assert reason_code == "CALCULATED"
        assert new_seller > Decimal("2414")  # обязан подняться из-под пола
        assert new_seller == Decimal("2434")
        assert comp_price == Decimal("1593")
        assert winner == 999

    def test_no_change_inside_corridor(self):
        """Финальная в коридоре [floor; ceil] → не трогаем."""
        engine = _make_engine_with_settings()
        config = _make_config(offset_type="rub", offset_direction="above", offset_value="0")
        # конкурент 1593 → коридор [1600.965; 1672.65]; берём 1620 (внутри)
        current_data = {
            "seller_price": Decimal("2460"),
            "discounted_price": Decimal("2460"),
            "final_price": Decimal("1620"),
            "discount": Decimal("0"),
            "spp": Decimal("34"),
        }

        new_seller, reason_code, _msg, _comp_price, _winner = _run_strategy(
            engine, config, current_data, competitor_final=1593
        )

        assert reason_code == "NO_CHANGE"
        assert new_seller is None

    def test_no_change_above_floor_soft_ceiling(self):
        """Финальная выше потолка → НЕ снижаем (мягкий потолок).

        Из-за ступенчатого СПП снижение может провалить цену в "мёртвую зону"
        ниже конкурента. Безопаснее оставить как есть.
        """
        engine = _make_engine_with_settings()
        config = _make_config(offset_type="rub", offset_direction="above", offset_value="0")
        current_data = {
            "seller_price": Decimal("2800"),
            "discounted_price": Decimal("2800"),
            "final_price": Decimal("1800"),
            "discount": Decimal("0"),
            "spp": Decimal("36"),
        }

        new_seller, reason_code, _msg, _comp_price, _winner = _run_strategy(
            engine, config, current_data, competitor_final=1593
        )

        assert reason_code == "NO_CHANGE"
        assert new_seller is None

    def test_falls_back_to_stored_spp_without_discounted(self):
        """Если discounted_price отсутствует (напр. dry_run) — берём хранимый СПП."""
        engine = _make_engine_with_settings()
        config = _make_config(offset_type="rub", offset_direction="above", offset_value="0")
        current_data = {
            "seller_price": Decimal("2000"),
            "final_price": Decimal("1300"),
            "discount": Decimal("0"),
            "spp": Decimal("35"),
            # discounted_price намеренно отсутствует
        }

        new_seller, reason_code, _msg, _comp_price, _winner = _run_strategy(
            engine, config, current_data, competitor_final=1500
        )

        assert reason_code == "CALCULATED"
        # под полом 1507.5; round_up(1507.5 / 0.65) = round_up(2319.23) = 2320
        assert new_seller == Decimal("2320")


# ═══════════════════════════════════════════════════════════════════════════════
# _verify_and_correct_competitor — closed-loop verify+correct
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import datetime, timedelta, timezone
from app.models.repricer_run import RepricerRun
from app.models.price_change import PriceChange
from app.models.price_snapshot import PriceSnapshot


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def update_prices(self, updates):
        self.calls.append(updates)
        return {"data": {"id": "task-1"}}


class TestCorrectUnstableProducts:
    def test_corrects_when_actual_below_floor(self, db_session, test_product, test_account):
        engine = RepricerEngine(db=db_session, user_id=None)
        engine._user_settings = {}
        engine._CLOSED_LOOP_SETTLE_SEC = 0  # не ждём в тесте

        run = RepricerRun(run_type="manual", status="running",
                          started_at=datetime(2026, 6, 10, 14, 0, 0))
        db_session.add(run); db_session.commit(); db_session.refresh(run)

        config = SimpleNamespace(min_price=Decimal("80"), max_price=None,
                                 skip_margin_check=True, product=test_product)
        items = [{
            "product": test_product,
            "nm_id": test_product.nm_id,
            "token": "tok",
            "config": config,
            "new_price": Decimal("2414"),
            "current_data": {"seller_price": Decimal("2414"), "final_price": Decimal("1588"),
                             "discount": Decimal("0"), "spp": Decimal("34")},
            "competitor_price": Decimal("1593"),
            "competitor_nm_id": 948560176,
        }]

        # Факт после первого шага: 1588 — всё ещё под полом 1600.965 → коррекция.
        fresh = {test_product.nm_id: SimpleNamespace(final_price=Decimal("1588"), spp=Decimal("34"))}
        fake_client = _FakeClient()

        async def fake_parse_batch(nm_ids, **kw):
            return fresh

        with patch("app.services.repricer_engine.catalog_parser.parse_batch_ids",
                   side_effect=fake_parse_batch), \
             patch("app.services.repricer_engine.create_wb_client",
                   return_value=fake_client):
            n = asyncio.run(engine._verify_and_correct_competitor(run, items))

        assert n == 1
        # отправлена коррекция вверх: round_up(1600.965 / (1588/2414)) = 2434
        assert fake_client.calls == [[{"nmID": test_product.nm_id, "price": 2434}]]
        changes = db_session.query(PriceChange).filter(
            PriceChange.run_id == run.id, PriceChange.reason_code == "CORRECTED"
        ).all()
        assert len(changes) == 1
        assert changes[0].new_seller_price == Decimal("2434")

    def test_no_correction_when_actual_in_corridor(self, db_session, test_product):
        engine = RepricerEngine(db=db_session, user_id=None)
        engine._user_settings = {}
        engine._CLOSED_LOOP_SETTLE_SEC = 0

        run = RepricerRun(run_type="manual", status="running",
                          started_at=datetime(2026, 6, 10, 14, 0, 0))
        db_session.add(run); db_session.commit(); db_session.refresh(run)

        config = SimpleNamespace(min_price=Decimal("80"), max_price=None,
                                 skip_margin_check=True, product=test_product)
        items = [{
            "product": test_product, "nm_id": test_product.nm_id, "token": "tok",
            "config": config, "new_price": Decimal("2460"),
            "current_data": {"seller_price": Decimal("2460"), "final_price": Decimal("1588"),
                             "discount": Decimal("0"), "spp": Decimal("34")},
            "competitor_price": Decimal("1593"), "competitor_nm_id": 1,
        }]
        # Факт 1620 >= пол 1600.965 → коррекция не нужна (мягкий потолок).
        fresh = {test_product.nm_id: SimpleNamespace(final_price=Decimal("1620"), spp=Decimal("34"))}
        fake_client = _FakeClient()

        async def fake_parse_batch(nm_ids, **kw):
            return fresh

        with patch("app.services.repricer_engine.catalog_parser.parse_batch_ids",
                   side_effect=fake_parse_batch), \
             patch("app.services.repricer_engine.create_wb_client",
                   return_value=fake_client):
            n = asyncio.run(engine._verify_and_correct_competitor(run, items))

        assert n == 0
        assert fake_client.calls == []


class TestCompetitorCooldownSnapshots:
    def test_oos_during_cooldown_saves_fresh_snapshot(self, db_session, test_product, test_product_config):
        engine = RepricerEngine(db=db_session, user_id=None)
        engine._user_settings = {}

        test_product_config.strategy = "competitor"
        test_product_config.competitor_nm_id = 577768295
        test_product_config.competitor_offset_type = "rub"
        test_product_config.competitor_offset_direction = "above"
        test_product_config.competitor_offset_value = Decimal("0")
        test_product_config.min_price = Decimal("300")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        run = RepricerRun(run_type="manual", status="running", started_at=now)
        db_session.add(run)
        db_session.flush()

        db_session.add(PriceChange(
            run_id=run.id,
            product_id=test_product.id,
            old_seller_price=Decimal("709"),
            new_seller_price=Decimal("789"),
            old_final_price=Decimal("466"),
            new_final_price=Decimal("520.74"),
            reason_code="UPDATED",
            reason_message="recent competitor update",
            changed_at=now - timedelta(minutes=10),
        ))
        db_session.add(PriceSnapshot(
            product_id=test_product.id,
            seller_price=Decimal("789"),
            final_price=Decimal("466"),
            spp=Decimal("34.27"),
            competitor_price=Decimal("516"),
            competitor_nm_id=577768295,
            total_quantity=1,
            snapshot_at=now - timedelta(minutes=9),
        ))
        db_session.commit()

        async def fake_parse(_nm_id):
            return SimpleNamespace(
                final_price=Decimal("0"),
                seller_price=Decimal("0"),
                spp=None,
                total_quantity=0,
                is_in_promo=False,
                catalog_stale=False,
            )

        with patch.object(engine, "_cached_parse_product", side_effect=fake_parse):
            decision = asyncio.run(engine._decide_product(test_product, run, dry_run=False))

        assert decision["skip_code"] == "OUT_OF_STOCK"
        assert decision["current_data"]["total_quantity"] == 0

        reason = engine._finalize_decision(run, decision, success=False, failed=False)
        db_session.commit()

        assert reason == "OUT_OF_STOCK"
        latest = db_session.query(PriceSnapshot).filter(
            PriceSnapshot.product_id == test_product.id
        ).order_by(PriceSnapshot.snapshot_at.desc(), PriceSnapshot.id.desc()).first()
        assert latest.final_price == Decimal("0.00")
        assert latest.total_quantity == 0
        assert latest.competitor_price is None
