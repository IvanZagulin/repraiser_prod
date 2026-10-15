"""
Защиты из аудита 2026-10-05:

- closed-loop ограничивает шаг от цены до прогона, проверяет отказ WB и
  соблюдает те же удержания снижения, что и основной расчёт;
- причины пропуска стратегии (каталог недоступен, конкуренты без остатка)
  не подменяются на NO_CHANGE;
- если загрузились не все конкуренты, цель по части списка только снижает цену;
- каталог: бюджет времени батча, «карточки нет» отличается от сбоя, таймаут
  на зависший запрос.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.price_change import PriceChange
from app.models.price_snapshot import PriceSnapshot
from app.models.repricer_run import RepricerRun
from app.services.catalog_parser import CatalogParser
from app.services.repricer_engine import RepricerEngine


def _engine(db_session):
    engine = RepricerEngine(db=db_session, user_id=None)
    engine._user_settings = {}
    engine._CLOSED_LOOP_SETTLE_SEC = 0
    engine._CLOSED_LOOP_CORRECTION_SETTLE_SEC = 0
    return engine


def _run(db_session):
    run = RepricerRun(run_type="manual", status="running",
                      started_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


class _FakeClient:
    def __init__(self, rejected=None):
        self.calls = []
        self.rejected = rejected or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def update_prices(self, updates):
        self.calls.append(updates)
        return {"data": {"id": "task-1"}}

    async def verify_batch_update(self, upload_id, expected_nm_ids=None, **kw):
        nm_ids = expected_nm_ids or [u["nmID"] for u in self.calls[-1]]
        return {
            nm: (False, self.rejected[nm]) if nm in self.rejected else (True, "")
            for nm in nm_ids
        }


def _competitor_config(product):
    return SimpleNamespace(
        id=1, strategy="competitor",
        min_price=Decimal("80"), max_price=None,
        skip_margin_check=True, product=product,
        competitor_offset_type="rub",
        competitor_offset_direction="above",
        competitor_offset_value=Decimal("0"),
    )


def _item(product, *, old, new, competitor="1000", current_data=None, partial=False):
    return {
        "product": product,
        "nm_id": product.nm_id,
        "token": "tok",
        "config": _competitor_config(product),
        "new_price": Decimal(new),
        "old_seller_price": Decimal(old),
        "current_data": current_data or {
            "seller_price": Decimal(old), "final_price": Decimal("1500"),
            "discount": Decimal("0"), "spp": Decimal("30"),
        },
        "strategy": "competitor",
        "competitor_price": Decimal(competitor),
        "competitor_nm_id": 1,
        "competitor_partial": partial,
    }


def _closed_loop(engine, run, items, actual_final, client):
    fresh = {items[0]["nm_id"]: SimpleNamespace(final_price=Decimal(actual_final), spp=Decimal("30"))}

    async def fake_parse_batch(nm_ids, **kw):
        return fresh

    with patch("app.services.repricer_engine.catalog_parser.parse_batch_ids", side_effect=fake_parse_batch), \
         patch("app.services.repricer_engine.create_wb_client", return_value=client):
        return asyncio.run(engine._verify_and_correct_competitor(run, items))


# ── Closed-loop ──────────────────────────────────────────────────────────────

class TestClosedLoopLimits:
    def test_correction_step_counts_from_price_before_run(self, db_session, test_product):
        """Основной шаг уже снизил 3000 → 2400 (−20%). Доправка не режет ещё 20%."""
        engine = _engine(db_session)
        client = _FakeClient()
        # Факт 1680 при цели 1000: доправка хотела бы ~1429, это ниже 0.8 × 3000
        n = _closed_loop(engine, _run(db_session), [_item(test_product, old="3000", new="2400")], "1680", client)
        assert n == 0
        assert client.calls == []

    def test_correction_floor_is_80_percent_of_old_price(self, db_session, test_product):
        engine = _engine(db_session)
        client = _FakeClient()
        # Основной шаг снизил только до 2800; доправка — не ниже 0.8 × 3000 = 2400
        n = _closed_loop(engine, _run(db_session), [_item(test_product, old="3000", new="2800")], "1960", client)
        assert n == 1
        assert client.calls == [[{"nmID": test_product.nm_id, "price": 2400}]]

    def test_lowering_correction_respects_quarantine_hold(self, db_session, test_product, engine_cache):
        engine = _engine(db_session)
        engine_cache.set(f"wb_quarantine_hold:{test_product.nm_id}", {"run_id": 1}, ttl=3600)
        client = _FakeClient()
        n = _closed_loop(engine, _run(db_session), [_item(test_product, old="3000", new="2800")], "1960", client)
        assert n == 0
        assert client.calls == []

    def test_wb_rejection_is_not_recorded_as_applied(self, db_session, test_product, engine_cache):
        engine = _engine(db_session)
        run = _run(db_session)
        client = _FakeClient(rejected={test_product.nm_id: "Price Quarantine"})
        n = _closed_loop(engine, run, [_item(test_product, old="3000", new="2800")], "1960", client)

        assert n == 0
        reasons = [c.reason_code for c in db_session.query(PriceChange).filter(PriceChange.run_id == run.id)]
        assert reasons == ["WB_API_ERROR"]
        # Снимок с непринятой ценой не пишется
        corrected = db_session.query(PriceSnapshot).filter(
            PriceSnapshot.product_id == test_product.id,
            PriceSnapshot.seller_price == Decimal("2400"),
        ).count()
        assert corrected == 0
        assert engine_cache.get(f"wb_quarantine_hold:{test_product.nm_id}") is not None
        assert engine._quarantine_hits and engine._quarantine_hits[0][2] == test_product.nm_id

    def test_spp_guard_cooldown_only_after_applied_revert(self, db_session, test_product, engine_cache):
        engine = _engine(db_session)
        client = _FakeClient(rejected={test_product.nm_id: "WB отклонил цену"})
        # Факт 700 сильно ниже цели 1000 → откат вверх, но WB его отклонил
        _closed_loop(engine, _run(db_session), [_item(test_product, old="1000", new="1000")], "700", client)
        assert engine_cache.get(f"spp_guard_cooldown:{test_product.id}") is None

    def test_partial_competitors_revert_not_above_price_before_run(self, db_session, test_product):
        engine = _engine(db_session)
        client = _FakeClient()
        # Цена снижена 1200 → 1000, факт 700 ниже цели 1000: откат не выше 1200
        n = _closed_loop(engine, _run(db_session),
                         [_item(test_product, old="1200", new="1000", partial=True)], "700", client)
        assert n == 1
        assert client.calls == [[{"nmID": test_product.nm_id, "price": 1200}]]


# ── Причина пропуска не теряется ─────────────────────────────────────────────

class TestSkipReasonKept:
    def _decide(self, engine, product, run, strategy_result):
        current = {
            "seller_price": Decimal("1300"), "discounted_price": Decimal("1300"),
            "discount": Decimal("0"), "final_price": Decimal("900"), "spp": Decimal("30.77"),
            "is_in_promo": False, "total_quantity": 5,
        }
        with patch("app.services.repricer_engine.is_token_expired", return_value=False), \
             patch.object(engine, "_get_current_price_data", new=AsyncMock(return_value=current)), \
             patch.object(engine, "_strategy_competitor", new=AsyncMock(return_value=strategy_result)):
            return asyncio.run(engine._decide_product(product, run, dry_run=False))

    @pytest.fixture()
    def competitor_product(self, db_session, test_product, test_product_config):
        test_product_config.strategy = "competitor"
        test_product_config.rrp_price = None
        test_product_config.min_price = Decimal("300")
        test_product_config.max_price = None
        test_product_config.skip_margin_check = True
        db_session.commit()
        db_session.refresh(test_product)
        return test_product

    def test_catalog_unavailable_is_not_masked(self, db_session, competitor_product):
        engine = _engine(db_session)
        engine._COMPETITOR_COOLDOWN_HOURS = 0
        decision = self._decide(engine, competitor_product, _run(db_session),
                                (None, "CATALOG_UNAVAILABLE", "Каталог WB не отдал цены конкурентов", None, None))
        assert decision["needs_update"] is False
        assert decision["skip_code"] == "CATALOG_UNAVAILABLE"

    def test_unavailable_is_logged_once_per_window(self, db_session, competitor_product):
        engine = _engine(db_session)
        engine._COMPETITOR_COOLDOWN_HOURS = 0
        run = _run(db_session)
        for _ in range(2):
            dec = self._decide(engine, competitor_product, run,
                               (None, "CATALOG_UNAVAILABLE", "Каталог WB не отдал цены конкурентов", None, None))
            assert engine._finalize_decision(run, dec, False, False) == "CATALOG_UNAVAILABLE"
        db_session.commit()
        logged = db_session.query(PriceChange).filter(
            PriceChange.run_id == run.id, PriceChange.reason_code == "CATALOG_UNAVAILABLE"
        ).count()
        assert logged == 1

    def test_min_price_still_enforced_when_strategy_skips(self, db_session, competitor_product, test_product_config):
        test_product_config.min_price = Decimal("1400")
        db_session.commit()
        engine = _engine(db_session)
        engine._COMPETITOR_COOLDOWN_HOURS = 0
        decision = self._decide(engine, competitor_product, _run(db_session),
                                (None, "COMPETITOR_OOS", "Все конкуренты не в наличии", None, None))
        assert decision["needs_update"] is True
        assert decision["new_price"] == Decimal("1400")


# ── Частично загруженные конкуренты ──────────────────────────────────────────

def _comp(final_price, in_stock=True):
    return SimpleNamespace(
        final_price=Decimal(str(final_price)),
        seller_price=Decimal(str(final_price)),
        in_stock=in_stock,
        catalog_stale=False,
    )


def _config(rule="min_price"):
    return SimpleNamespace(
        id=7, competitor_nm_id=None, competitor_select_rule=rule,
        competitor_offset_type="rub", competitor_offset_direction="above",
        competitor_offset_value=Decimal("0"),
    )


def _pick(engine, config, snapshots):
    engine._competitor_entries_map = {config.id: list(snapshots)}

    async def fake_parse(nm_id):
        return snapshots[nm_id]

    with patch.object(engine, "_cached_parse_product", side_effect=fake_parse), \
         patch.object(engine, "_parse_product_single_fallback", new=AsyncMock(return_value=None)):
        return asyncio.run(engine._get_competitor_price(config))


class TestPartialCompetitors:
    def test_min_with_failed_competitor_is_partial(self, db_session):
        engine = _engine(db_session)
        price, winner, _ = _pick(engine, _config(), {111: None, 222: _comp(1000)})
        assert (price, winner) == (Decimal("1000"), 222)
        assert 7 in engine._competitor_partial

    def test_avg_with_failed_competitor_is_unavailable(self, db_session):
        engine = _engine(db_session)
        price, _, _ = _pick(engine, _config("avg_price"), {111: None, 222: _comp(1000)})
        assert price is None
        assert 7 in engine._competitor_unavailable

    def test_in_stock_without_price_is_a_failure_not_oos(self, db_session):
        engine = _engine(db_session)
        price, _, _ = _pick(engine, _config(), {111: _comp(0, in_stock=True)})
        assert price is None
        assert 7 in engine._competitor_unavailable

    def test_card_missing_from_catalog_counts_as_absent(self, db_session, engine_cache):
        engine = _engine(db_session)
        engine_cache.set("wb_catalog_missing:111", {"ts": 1}, ttl=3600)
        price, _, _ = _pick(engine, _config(), {111: None, 222: _comp(900, in_stock=False)})
        assert price is None
        assert 7 not in engine._competitor_unavailable  # это «нет в наличии», не сбой

    def _strategy(self, engine, seller_price):
        current = {"seller_price": Decimal(seller_price), "discounted_price": Decimal(seller_price),
                   "discount": Decimal("0"), "final_price": Decimal(seller_price) * Decimal("0.7"),
                   "spp": Decimal("30")}
        config = _config()
        engine._competitor_partial.add(config.id)
        with patch.object(engine, "_get_competitor_price",
                          new=AsyncMock(return_value=(Decimal("1000"), 222, Decimal("1000")))):
            return asyncio.run(engine._strategy_competitor(SimpleNamespace(nm_id=1), config, current))

    def test_partial_blocks_raise(self, db_session):
        new_price, code, *_ = self._strategy(_engine(db_session), "1000")  # факт 700 < цели 1000
        assert new_price is None
        assert code == "COMPETITOR_PARTIAL"

    def test_partial_allows_lowering(self, db_session):
        new_price, code, *_ = self._strategy(_engine(db_session), "2000")  # факт 1400 > цели 1000
        assert code == "CALCULATED"
        assert new_price < Decimal("2000")


# ── Каталог ──────────────────────────────────────────────────────────────────

class TestCatalogParser:
    def test_missing_card_is_reported_and_not_retried(self):
        parser = CatalogParser()
        proxy = AsyncMock(return_value={"products": [{"id": 1, "sizes": [], "totalQuantity": 0}]})
        not_found: set = set()
        with patch.object(parser, "_request_via_catalog_proxy", new=proxy):
            result = asyncio.run(parser.parse_batch_ids([1, 2], delay_between_chunks=0, not_found=not_found))
        assert result[2] is None
        assert not_found == {2}
        assert proxy.await_count == 1  # «карточки нет» не повторяем ретраями

    def test_budget_exhausted_skips_requests(self):
        parser = CatalogParser()
        proxy = AsyncMock(return_value={"products": []})
        # Дедлайн уже в прошлом: ни один чанк не должен уйти в каталог
        with patch.object(parser, "_request_via_catalog_proxy", new=proxy):
            result = asyncio.run(parser.parse_batch_ids([1, 2, 3], chunk_size=1, delay_between_chunks=0,
                                                        deadline_sec=-1))
        assert result == {1: None, 2: None, 3: None}
        proxy.assert_not_awaited()

    def test_single_parse_checks_card_id(self):
        parser = CatalogParser()
        not_found: set = set()
        data = {"products": [{"id": 999, "sizes": [], "totalQuantity": 0}]}
        assert parser._parse_response(1, data, not_found) is None
        assert not_found == {1}

    def test_hung_request_is_cut_by_hard_timeout(self):
        class _HangingSession:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                await asyncio.sleep(3600)

        parser = CatalogParser()
        parser._HARD_TIMEOUT_PAD_SEC = 0
        with patch("app.services.catalog_parser.AsyncSession", _HangingSession), \
             patch("app.services.catalog_parser.get_headers", return_value={}), \
             patch("app.services.catalog_parser.get_cookies", return_value={}), \
             patch("app.services.catalog_parser.settings.CATALOG_PROXY_URL", "http://proxy"):
            # У прокси timeout=15 — проверяем сам механизм на коротком таймауте
            with pytest.raises(asyncio.TimeoutError):
                asyncio.run(parser._fetch("http://x", timeout=0.05))


# ── СПП из одной пары цен каталога ───────────────────────────────────────────

class TestSppSingleSource:
    def _price_data(self, engine, nm_id, seller, catalog_basic, catalog_final):
        engine._prefetched_prices[nm_id] = SimpleNamespace(
            seller_price=Decimal(seller), discounted_price=Decimal(seller), discount=Decimal("0"),
        )
        catalog = SimpleNamespace(
            final_price=Decimal(catalog_final), seller_price=Decimal(catalog_basic), spp=None,
            is_in_promo=False, total_quantity=5, in_stock=True, catalog_stale=False,
        )
        product = SimpleNamespace(nm_id=nm_id, id=1)
        account = SimpleNamespace(name="Кабинет", is_active=True, wb_api_token="t", marketplace="wb")
        with patch.object(engine, "_cached_parse_product", new=AsyncMock(return_value=catalog)):
            return product, asyncio.run(engine._get_current_price_data(product, account))

    def test_exact_spp_and_mismatch_flag(self, db_session):
        engine = _engine(db_session)
        # 1С сменила цену 2000 → 1500, каталог ещё отдаёт пару для 2000
        _, data = self._price_data(engine, 5, seller="1500", catalog_basic="2000", catalog_final="1400")
        assert data["spp_exact"] == Decimal("30")
        assert data["catalog_price_mismatch"] is True

    def test_consistent_pair_has_no_mismatch(self, db_session):
        engine = _engine(db_session)
        _, data = self._price_data(engine, 6, seller="2000", catalog_basic="2000", catalog_final="1400")
        assert "catalog_price_mismatch" not in data

    def test_mismatch_estimates_buyer_price_at_api_price(self, db_session):
        engine = _engine(db_session)
        product, data = self._price_data(engine, 7, seller="1500", catalog_basic="2000", catalog_final="1400")
        trusted = engine._trusted_observation(product, data)
        assert trusted["final_price"] == Decimal("1050.00")  # 1500 × (1 − 30%)
        assert trusted["catalog_lag"] is True  # снижать по оценке нельзя

    def test_competitor_strategy_uses_catalog_pair_spp(self, db_session):
        engine = _engine(db_session)
        config = SimpleNamespace(
            id=9, competitor_nm_id=None, competitor_select_rule="min_price",
            competitor_offset_type="rub", competitor_offset_direction="above",
            competitor_offset_value=Decimal("0"),
        )
        current = {
            "seller_price": Decimal("1500"), "discounted_price": Decimal("1200"),  # несогласовано с final
            "discount": Decimal("0"), "final_price": Decimal("1050"),
            "spp": Decimal("30"), "spp_exact": Decimal("30"),
        }
        with patch.object(engine, "_get_competitor_price",
                          new=AsyncMock(return_value=(Decimal("1300"), 1, Decimal("1300")))):
            new_price, code, *_ = asyncio.run(
                engine._strategy_competitor(SimpleNamespace(nm_id=1), config, current))
        assert code == "CALCULATED"
        # 1300 / 0.7 = 1857.14 → 1858; по старой формуле (1050/1200) вышло бы 1486
        assert new_price == Decimal("1858")


# ── Допуск РРЦ ───────────────────────────────────────────────────────────────

class TestRrpTolerance:
    def _rrp(self, final, tolerance, wallet=False):
        engine = RepricerEngine(db=None, user_id=None)
        engine._user_settings = {}
        config = SimpleNamespace(
            rrp_price=Decimal("1000"), rrp_tolerance=Decimal(tolerance),
            rrp_consider_wallet=wallet, rrp_spp_tiers=[],
        )
        current = {"seller_price": Decimal(final), "final_price": Decimal(final),
                   "discount": Decimal("0"), "spp": Decimal("0")}
        return asyncio.run(engine._strategy_rrp(
            SimpleNamespace(nm_id=1, seller_sku="S"), config, current,
            Decimal("3") if wallet else Decimal("0")))

    def test_price_inside_tolerance_is_left_alone(self):
        new_price, code, _ = self._rrp("1015", "20")
        assert (new_price, code) == (None, "NO_CHANGE")

    def test_price_above_tolerance_returns_to_rrp(self):
        new_price, code, _ = self._rrp("1025", "20")
        assert code == "CALCULATED"
        assert new_price == Decimal("1000")

    def test_configured_tolerance_is_used_exactly(self):
        assert self._rrp("1001", "1")[1] == "NO_CHANGE"
        assert self._rrp("1002", "1")[1] == "CALCULATED"

    def test_never_below_rrp_whatever_tolerance(self):
        new_price, code, _ = self._rrp("995", "50")
        assert code == "CALCULATED"
        assert new_price == Decimal("1000")

    def test_wallet_rrp_812_reprices_840_to_exact_corridor(self):
        engine = RepricerEngine(db=None, user_id=None)
        engine._user_settings = {}
        config = SimpleNamespace(
            rrp_price=Decimal("812"), rrp_tolerance=Decimal("1"),
            rrp_consider_wallet=True, rrp_spp_tiers=[],
        )
        current = {
            "seller_price": Decimal("840"), "final_price": Decimal("840"),
            "discount": Decimal("0"), "spp": Decimal("0"),
        }
        new_price, code, _ = asyncio.run(engine._strategy_rrp(
            SimpleNamespace(nm_id=1, seller_sku="3892624"),
            config,
            current,
            Decimal("3"),
        ))
        assert code == "CALCULATED"
        assert new_price == Decimal("838")

    def test_wallet_rrp_removes_double_rounding_overage(self):
        engine = RepricerEngine(db=None, user_id=None)
        engine._user_settings = {}
        config = SimpleNamespace(
            rrp_price=Decimal("4789"), rrp_tolerance=Decimal("1"),
            rrp_consider_wallet=True, rrp_spp_tiers=[],
        )
        current = {
            "seller_price": Decimal("5951"), "final_price": Decimal("4939"),
            "discount": Decimal("0"), "spp": Decimal("17.01"),
        }
        new_price, code, _ = asyncio.run(engine._strategy_rrp(
            SimpleNamespace(nm_id=1, seller_sku="3887331"),
            config,
            current,
            Decimal("3"),
        ))
        assert code == "CALCULATED"
        assert new_price == Decimal("5950")

    def test_wallet_rrp_does_not_nudge_below_floor(self):
        engine = RepricerEngine(db=None, user_id=None)
        engine._user_settings = {}
        config = SimpleNamespace(
            rrp_price=Decimal("812"), rrp_tolerance=Decimal("1"),
            rrp_consider_wallet=True, rrp_spp_tiers=[],
        )
        current = {
            "seller_price": Decimal("1118"), "final_price": Decimal("838"),
            "discount": Decimal("0"), "spp": Decimal("25.04"),
        }
        new_price, code, _ = asyncio.run(engine._strategy_rrp(
            SimpleNamespace(nm_id=1, seller_sku="3892624"),
            config,
            current,
            Decimal("3"),
        ))
        assert code == "NO_CHANGE"
        assert new_price is None

    def test_wallet_price_is_checked_against_corridor(self):
        # 1040 × 0.97 = 1008.8 ≤ 1000 + 10
        assert self._rrp("1040", "10", wallet=True)[1] == "NO_CHANGE"
        # 1060 × 0.97 = 1028.2 > 1010
        assert self._rrp("1060", "10", wallet=True)[1] == "CALCULATED"
