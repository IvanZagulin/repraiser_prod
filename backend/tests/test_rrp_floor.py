"""
RRP floor hardening: the buyer price (with wallet) must not drop below RRP,
and the engine must not act on inconsistent WB catalog data.

Redis is replaced with an in-memory dict so the tests run without a server.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.price_snapshot import PriceSnapshot
from app.models.repricer_run import RepricerRun
from app.services.repricer_engine import RepricerEngine


@pytest.fixture()
def fake_cache(engine_cache):
    """Engine cache from conftest, exposed as a plain dict of values."""
    class _View:
        def __init__(self, cache):
            self._cache = cache

        @property
        def store(self):
            return _Store(self._cache)

    class _Store(dict):
        def __init__(self, cache):
            super().__init__({k: v[0] for k, v in cache.store.items()})
            self._cache = cache

        def __setitem__(self, key, value):
            self._cache.set(key, value, ttl=3600)

    return _View(engine_cache)


def _engine(db=None):
    engine = RepricerEngine(db=db or MagicMock(), user_id=None)
    engine._user_settings = {}
    return engine


def _run(db_session):
    run = RepricerRun(run_type="manual", status="running",
                      started_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


# ── Trusted observation: stale catalog detection ─────────────────────────────

class TestTrustedObservation:
    def _obs(self, engine, seller, final, spp, discount="0"):
        product = SimpleNamespace(nm_id=777, id=1)
        return engine._trusted_observation(product, {
            "seller_price": Decimal(seller), "final_price": Decimal(final),
            "spp": Decimal(spp), "discount": Decimal(discount),
        })

    def test_first_observation_is_trusted_and_stored(self, fake_cache):
        engine = _engine()
        data = self._obs(engine, "5339", "3683", "31.02")
        assert not data.get("catalog_lag")
        assert fake_cache.store["price_obs:777"]["spp"] == 31.02

    def test_buyer_price_unchanged_after_seller_change_is_lag(self, fake_cache):
        """Каталог отдал новую цену продавца со старой ценой покупателя."""
        engine = _engine()
        self._obs(engine, "5339", "3683", "31.02")
        data = self._obs(engine, "6674", "3683", "44.82")
        assert data["catalog_lag"] is True
        assert data["spp"] == Decimal("31.02")
        assert data["final_price"] == (Decimal("6674") * Decimal("0.6898")).quantize(Decimal("0.01"))
        # недостоверное наблюдение не перезаписывает достоверное
        assert fake_cache.store["price_obs:777"]["final"] == 3683.0

    def test_spp_jump_after_seller_change_is_raise_only(self, fake_cache):
        engine = _engine()
        self._obs(engine, "6888", "4752", "31")
        data = self._obs(engine, "6889", "3683", "46.54")  # base moved <1% → not a lag
        assert not data.get("catalog_lag")
        self._obs(engine, "6888", "4752", "31")
        data = self._obs(engine, "5339", "2900", "45.68")  # base moved, SPP +14.7 п.п.
        assert data["catalog_lag"] is True
        assert data["spp"] == Decimal("45.68")  # текущий СПП, но только повышение

    def test_consistent_new_pair_is_trusted(self, fake_cache):
        engine = _engine()
        self._obs(engine, "6888", "4752", "31")
        data = self._obs(engine, "5339", "3683", "31.02")  # 1С сбросил цену, каталог свежий
        assert not data.get("catalog_lag")
        assert fake_cache.store["price_obs:777"]["final"] == 3683.0

    def test_real_spp_change_without_seller_change_is_trusted(self, fake_cache):
        engine = _engine()
        self._obs(engine, "6674", "4604", "31.02")
        data = self._obs(engine, "6674", "4450", "33.32")
        assert not data.get("catalog_lag")


# ── Decisions: out of stock, holds, step-down ────────────────────────────────

def _rrp_config(db_session, test_product_config, rrp="900"):
    test_product_config.strategy = "rrp"
    test_product_config.rrp_price = Decimal(rrp)
    test_product_config.rrp_consider_wallet = False
    test_product_config.min_price = Decimal("300")
    test_product_config.max_price = None
    db_session.commit()


def _decide(engine, product, run, current_data):
    with patch("app.services.repricer_engine.is_token_expired", return_value=False), \
         patch.object(engine, "_get_current_price_data", new=AsyncMock(return_value=current_data)):
        return asyncio.run(engine._decide_product(product, run, dry_run=False))


class TestOutOfStock:
    def test_oos_without_known_spp_is_left_alone(self, db_session, test_product, test_product_config, fake_cache):
        """Раньше СПП считался 0 и цена продавца падала до РРЦ/0.97."""
        _rrp_config(db_session, test_product_config)
        engine = _engine(db_session)
        decision = _decide(engine, test_product, _run(db_session), {
            "seller_price": Decimal("1300"), "discounted_price": Decimal("1300"),
            "discount": Decimal("0"), "final_price": Decimal("0"), "spp": Decimal("0"),
            "is_in_promo": False, "total_quantity": 0,
        })
        assert decision["needs_update"] is False
        assert decision["skip_code"] == "OUT_OF_STOCK"

    def test_oos_uses_last_snapshot_spp_and_only_raises(self, db_session, test_product, test_product_config, fake_cache):
        _rrp_config(db_session, test_product_config)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add(PriceSnapshot(
            product_id=test_product.id, seller_price=Decimal("1000"), final_price=Decimal("700"),
            discount=Decimal("0"), spp=Decimal("30"), total_quantity=3,
            snapshot_at=now - timedelta(days=2),
        ))
        # «Прогнозный» снимок старой логики для товара без остатка (СПП = 0) — игнорируется
        db_session.add(PriceSnapshot(
            product_id=test_product.id, seller_price=Decimal("928"), final_price=Decimal("928"),
            discount=Decimal("0"), spp=Decimal("0"), total_quantity=0,
            snapshot_at=now - timedelta(hours=1),
        ))
        db_session.commit()
        engine = _engine(db_session)
        run = _run(db_session)

        # Цена продавца занижена (1С / старый расчёт с СПП=0) → поднимаем
        decision = _decide(engine, test_product, run, {
            "seller_price": Decimal("1000"), "discounted_price": Decimal("1000"),
            "discount": Decimal("0"), "final_price": Decimal("0"), "spp": Decimal("0"),
            "is_in_promo": False, "total_quantity": 0,
        })
        assert decision["needs_update"] is True
        assert decision["new_price"] == Decimal("1286")  # ceil(900/0.7)

        # Цена продавца выше нужной → не снижаем, пока нет свежей цены покупателя
        decision = _decide(engine, test_product, run, {
            "seller_price": Decimal("1500"), "discounted_price": Decimal("1500"),
            "discount": Decimal("0"), "final_price": Decimal("0"), "spp": Decimal("0"),
            "is_in_promo": False, "total_quantity": 0,
        })
        assert decision["needs_update"] is False
        assert decision["skip_code"] == "NO_CHANGE"

    def test_competitor_config_uses_rrp_fallback_when_own_card_is_oos(
        self, db_session, test_product, test_product_config, fake_cache,
    ):
        test_product_config.strategy = "competitor"
        test_product_config.competitor_nm_id = 999
        test_product_config.rrp_price = Decimal("900")
        test_product_config.rrp_consider_wallet = False
        test_product_config.min_price = Decimal("300")
        test_product_config.max_price = None
        test_product_config.skip_margin_check = True
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add(PriceSnapshot(
            product_id=test_product.id, seller_price=Decimal("1000"), final_price=Decimal("700"),
            discount=Decimal("0"), spp=Decimal("30"), total_quantity=3,
            snapshot_at=now - timedelta(hours=1),
        ))
        db_session.commit()
        engine = _engine(db_session)
        engine._bypass_competitor_cooldown = True
        competitor = SimpleNamespace(
            final_price=Decimal("0"), seller_price=Decimal("0"),
            in_stock=False, catalog_stale=False,
        )
        with patch.object(engine, "_cached_parse_product", new=AsyncMock(return_value=competitor)):
            decision = _decide(engine, test_product, _run(db_session), {
                "seller_price": Decimal("1000"), "discounted_price": Decimal("1000"),
                "discount": Decimal("0"), "final_price": Decimal("0"), "spp": Decimal("0"),
                "is_in_promo": False, "total_quantity": 0,
            })
        assert decision["needs_update"] is True
        assert decision["new_price"] == Decimal("1286")


class TestLoweringHolds:
    in_stock = {
        "seller_price": Decimal("2000"), "discounted_price": Decimal("2000"),
        "discount": Decimal("0"), "final_price": Decimal("1400"), "spp": Decimal("30"),
        "is_in_promo": False, "total_quantity": 5,
    }

    def test_drop_is_limited_to_20_percent(self, db_session, test_product, test_product_config, fake_cache):
        _rrp_config(db_session, test_product_config, rrp="700")
        decision = _decide(_engine(db_session), test_product, _run(db_session), dict(self.in_stock))
        assert decision["needs_update"] is True
        assert decision["new_price"] == Decimal("1600")  # 2000 * 0.8, цель 1003

    def test_quarantine_hold_blocks_lowering(self, db_session, test_product, test_product_config, fake_cache):
        _rrp_config(db_session, test_product_config, rrp="700")
        fake_cache.store[f"wb_quarantine_hold:{test_product.nm_id}"] = {"run_id": 1}
        decision = _decide(_engine(db_session), test_product, _run(db_session), dict(self.in_stock))
        assert decision["needs_update"] is False
        assert "карантин" in decision["reason_msg"]

    def test_spp_guard_cooldown_blocks_lowering_but_not_raising(self, db_session, test_product, test_product_config, fake_cache):
        fake_cache.store[f"spp_guard_cooldown:{test_product.id}"] = {"run_id": 1}
        _rrp_config(db_session, test_product_config, rrp="700")
        decision = _decide(_engine(db_session), test_product, _run(db_session), dict(self.in_stock))
        assert decision["needs_update"] is False

        _rrp_config(db_session, test_product_config, rrp="1500")
        decision = _decide(_engine(db_session), test_product, _run(db_session), dict(self.in_stock))
        assert decision["needs_update"] is True
        assert decision["new_price"] > Decimal("2000")

    def test_current_price_at_margin_floor_reports_margin_constraint(
        self, db_session, test_product, test_product_config, fake_cache,
    ):
        _rrp_config(db_session, test_product_config, rrp="300")
        engine = _engine(db_session)
        engine._user_settings = {
            "min_margin_pct": "20",
            "commission_type": "pct", "commission_value": "0",
            "logistics_type": "rub", "logistics_value": "0",
            "acquiring_type": "pct", "acquiring_value": "0",
            "brand_min_margin_rules_map": {}, "subject_commissions": {},
        }
        decision = _decide(engine, test_product, _run(db_session), {
            "seller_price": Decimal("600"), "discounted_price": Decimal("600"),
            "discount": Decimal("0"), "final_price": Decimal("420"), "spp": Decimal("30"),
            "is_in_promo": False, "total_quantity": 5,
        })
        assert decision["needs_update"] is False
        assert decision["skip_code"] == "CLAMPED_TO_MARGIN"


# ── Misc ─────────────────────────────────────────────────────────────────────

class TestAuthFailures:
    def test_account_disabled_only_after_repeated_401(self, fake_cache):
        engine = _engine()
        account = SimpleNamespace(id=5, name="Кабинет", is_active=True)
        with patch("app.services.repricer_engine.telegram_notifier.notify_token_expired",
                   new=AsyncMock()) as notify:
            assert asyncio.run(engine._handle_wb_auth_failure(account, "401")) is False
            assert asyncio.run(engine._handle_wb_auth_failure(account, "401")) is False
            assert account.is_active is True
            assert asyncio.run(engine._handle_wb_auth_failure(account, "401")) is True
            assert account.is_active is False
        assert notify.await_count == 1  # одно уведомление, а не на каждый отказ


class TestLimitsAndRules:
    def test_fractional_min_price_rounded_up(self):
        engine = _engine()
        config = SimpleNamespace(min_price=Decimal("999.50"), max_price=Decimal("1499.99"),
                                 skip_margin_check=True, product=None)
        price, reason = engine._apply_limits(Decimal("500"), config)
        assert (price, reason) == (Decimal("1000"), "CLAMPED_TO_MIN")
        price, reason = engine._apply_limits(Decimal("3000"), config)
        assert (price, reason) == (Decimal("1499"), "CLAMPED_TO_MAX")

    def test_brand_margin_rule_matches_rule_inside_brand_only(self):
        engine = _engine()
        engine._user_settings = {"brand_min_margin_rules_map": {"просвещение": Decimal("20")}}

        def pct(brand):
            return engine._effective_min_margin_pct(
                SimpleNamespace(product=SimpleNamespace(brand=brand)), Decimal("27"))

        assert pct("Просвещение") == Decimal("20")
        assert pct("Издательство Просвещение") == Decimal("20")
        assert pct("Про") == Decimal("27")  # раньше «Про» получал маржу «Просвещения»

    def test_competitor_corridor(self):
        engine = _engine()
        target = Decimal("1250.5")
        assert engine._competitor_in_corridor(Decimal("1251"), target)
        assert engine._competitor_in_corridor(Decimal("1254"), target)
        assert not engine._competitor_in_corridor(Decimal("1250"), target)
        assert not engine._competitor_in_corridor(Decimal("1260"), target)

    def test_stale_catalog_cache_entry_is_marked(self, fake_cache):
        engine = _engine()
        entry = {"final_price": 100, "seller_price": 150, "spp": 33.3, "is_in_promo": False,
                 "total_quantity": 1, "in_stock": True}
        fake_cache.store["wb_catalog:1"] = dict(entry, fetched_at=time.time() - 50 * 60)
        fake_cache.store["wb_catalog:2"] = dict(entry, fetched_at=time.time() - 60)
        assert asyncio.run(engine._cached_parse_product(1)).catalog_stale is True
        assert asyncio.run(engine._cached_parse_product(2)).catalog_stale is False

