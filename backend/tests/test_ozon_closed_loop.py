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

def test_in_corridor_no_change():
    # РРЦ — пол: витрина должна быть в [РРЦ; РРЦ+допуск].
    # seller*k = 5804*0.588 ≈ 3413 ∈ [3409; 3414] -> не трогаем.
    cfg = _config(ozon_realized_k=Decimal("0.588"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3413")), Decimal("3409"))
    assert code == "NO_CHANGE"
    assert price is None


def test_storefront_below_rrp_raises():
    # витрина ниже РРЦ (пол пробит) -> поднять цену продавца
    cfg = _config(ozon_realized_k=Decimal("0.587"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3407")), Decimal("3409"))
    assert code == "CALCULATED"
    assert price > 5804


def test_computes_seller_to_hit_storefront():
    # k=0.5827, цель 3409 -> seller = 3409/0.5827 ≈ 5850
    cfg = _config(ozon_realized_k=Decimal("0.5827"))
    price, code, msg = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3382")), Decimal("3409"))
    assert code == "CALCULATED"
    assert 5848 <= price <= 5852


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
    seller = Decimal("5820")
    eng = _engine()
    last = None
    for _ in range(6):
        storefront = (seller * k_true).quantize(Decimal("0.01"))
        price, code, _ = eng._ozon_rrp_closed_loop(
            SimpleNamespace(), cfg, _data(seller, storefront), target)
        if code == "NO_CHANGE":
            last = seller
            break
        seller = Decimal(price)
        last = seller
    # После сходимости витрина у цели в пределах допуска
    final_storefront = last * k_true
    assert abs(final_storefront - target) <= Decimal("6")


def test_storefront_factor_corrects_index():
    """Если замерили, что реальная витрина на 1% выше индекса, фактор это учитывает."""
    cfg = _config(ozon_storefront_factor=Decimal("1.01"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3382")), Decimal("3409"))
    # k_obs = 3382*1.01/5804 = 0.5886 -> seller = 3409/0.5886 ≈ 5791
    assert code == "CALCULATED"
    assert 5788 <= price <= 5794
