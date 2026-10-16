"""Замкнутый цикл РРЦ для Ozon: доводим цену продавца по реальному k."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.repricer_engine import RepricerEngine


def _engine(dry_run=False):
    e = RepricerEngine(db=MagicMock(), user_id=1)
    e._dry_run = dry_run
    return e


def _config(**kw):
    base = dict(ozon_realized_k=None, ozon_k_updated_at=None,
                ozon_storefront_factor=None, rrp_tolerance=Decimal("5"))
    base.update(kw)
    return SimpleNamespace(**base)


def _data(seller, buyer):
    return {"seller_price": Decimal(str(seller)), "final_price": Decimal(str(buyer))}


# ---------------------------------------------------------------------------
# EMA realized k
# ---------------------------------------------------------------------------

def test_first_observation_sets_k():
    cfg = _config()
    k = _engine()._ozon_update_realized_k(cfg, Decimal("0.58"))
    assert k == Decimal("0.58")
    assert cfg.ozon_realized_k == Decimal("0.58")
    assert cfg.ozon_k_updated_at is not None


def test_ema_blends_with_previous():
    cfg = _config(ozon_realized_k=Decimal("0.60"))
    k = _engine()._ozon_update_realized_k(cfg, Decimal("0.50"))
    # alpha=0.5 -> среднее
    assert k == Decimal("0.5500")


def test_implausible_observation_keeps_previous():
    cfg = _config(ozon_realized_k=Decimal("0.60"))
    # k=1.9 вне диапазона (индекс отстал) -> держим прежнее
    k = _engine()._ozon_update_realized_k(cfg, Decimal("1.9"))
    assert k == Decimal("0.60")
    assert cfg.ozon_realized_k == Decimal("0.60")


def test_dry_run_does_not_persist():
    cfg = _config()
    _engine(dry_run=True)._ozon_update_realized_k(cfg, Decimal("0.58"))
    assert cfg.ozon_realized_k is None


# ---------------------------------------------------------------------------
# Замкнутый цикл: расчёт цели и коридор
# ---------------------------------------------------------------------------

def test_within_deadband_no_change():
    # РРЦ 3409, допуск ±1.5% -> коридор [3357.9; 3460.1].
    # seller*k = 5804*0.588 ≈ 3413 внутри -> не трогаем.
    cfg = _config(ozon_realized_k=Decimal("0.588"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3413")), Decimal("3409"))
    assert code == "NO_CHANGE"
    assert price is None


def test_small_jitter_does_not_churn():
    """Дрейф витрины на ~1% вокруг РРЦ не должен дёргать цену."""
    eng = _engine()
    for sf in (Decimal("3380"), Decimal("3409"), Decimal("3440")):
        cfg = _config(ozon_realized_k=sf / Decimal("5804"))
        _, code, _ = eng._ozon_rrp_closed_loop(
            SimpleNamespace(), cfg, _data(5804, sf), Decimal("3409"))
        assert code == "NO_CHANGE", f"витрина {sf} внутри ±1.5% не должна менять цену"


def test_below_deadband_raises():
    # витрина явно ниже коридора (< 3357.9) -> поднять цену продавца
    cfg = _config(ozon_realized_k=Decimal("0.570"))  # 5804*0.57 = 3308
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3308")), Decimal("3409"))
    assert code == "CALCULATED"
    assert price > 5804


def test_above_deadband_computes_seller():
    # витрина выше коридора (> 3460.1) -> снизить до РРЦ.
    # k=0.62 -> est 5804*0.62=3598 -> target 3409/0.62 ≈ 5498
    cfg = _config(ozon_realized_k=Decimal("0.62"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3598")), Decimal("3409"))
    assert code == "CALCULATED"
    assert 5496 <= price <= 5500


def test_step_is_capped():
    # k резко занижен -> цель улетает, но шаг ограничен +15%
    cfg = _config(ozon_realized_k=Decimal("0.30"))
    price, code, msg = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5000, Decimal("1500")), Decimal("3000"))
    assert code == "CALCULATED"
    assert price == 5750  # 5000 * 1.15
    assert "шаг ограничен" in msg


def test_convergence_over_iterations():
    """Эмуляция нескольких прогонов: k плавает, цена сходится к цели.

    Модель витрины: storefront = seller * k_true, где k_true фиксирован.
    Движок не знает k_true и доводит цену по наблюдениям.
    """
    k_true = Decimal("0.585")
    target = Decimal("3409")
    cfg = _config()
    seller = Decimal("6200")  # старт заведомо выше коридора (витрина ~3627)
    eng = _engine()
    last = seller
    for _ in range(8):
        storefront = (seller * k_true).quantize(Decimal("0.01"))
        price, code, _ = eng._ozon_rrp_closed_loop(
            SimpleNamespace(), cfg, _data(seller, storefront), target)
        if code == "NO_CHANGE":
            break
        seller = Decimal(price)
        last = seller
    # Сошлось: витрина в пределах мёртвой зоны (±1.5% от 3409 ≈ ±51)
    final_storefront = last * k_true
    assert abs(final_storefront - target) <= Decimal("51")


def test_storefront_factor_corrects_index():
    """Поправка индекс->реальная витрина меняет оцениваемую витрину и цель.

    Индекс даёт витрину 3600 (выше коридора). Реальная замерена как 0.95 от
    индекса -> фактор уводит оценку к 3420, то есть в коридор -> NO_CHANGE.
    """
    without = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), _config(), _data(5804, Decimal("3600")), Decimal("3409"))
    assert without[1] == "CALCULATED"  # без поправки — снизить

    cfg = _config(ozon_storefront_factor=Decimal("0.95"))
    _, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3600")), Decimal("3409"))
    # k_obs = 3600*0.95/5804 = 0.5893 -> est = 5804*0.5893 = 3420 ∈ коридор
    assert code == "NO_CHANGE"
