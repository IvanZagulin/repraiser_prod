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
                ozon_storefront_factor=None, rrp_tolerance=Decimal("5"),
                ozon_site_price=None, ozon_site_price_at=None,
                ozon_site_seller_price=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _data(seller, buyer):
    """Витрина восстановлена из ценового индекса — запасной путь."""
    return {"seller_price": Decimal(str(seller)), "final_price": Decimal(str(buyer))}


def _measured(seller, buyer, stale=False):
    """Витрина прочитана из отчёта Ozon — прямой замер."""
    data = _data(seller, buyer)
    data.update({
        "ozon_buyer_source": "site_report",
        "ozon_site_price": float(buyer),
        "ozon_site_price_stale": stale,
    })
    return data


# ---------------------------------------------------------------------------
# EMA realized k (только для пути «по индексу»)
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
# Путь «по индексу»: широкая зона, потому что оценка неточная
# ---------------------------------------------------------------------------

def test_within_deadband_no_change():
    # РРЦ 3409, зона +1.5% -> коридор [3409; 3460.1].
    # seller*k = 5804*0.588 ≈ 3413 внутри -> не трогаем.
    cfg = _config(ozon_realized_k=Decimal("0.588"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3413")), Decimal("3409"))
    assert code == "NO_CHANGE"
    assert price is None


def test_jitter_above_rrp_does_not_churn():
    """Дрейф витрины над РРЦ в пределах зоны не должен дёргать цену."""
    eng = _engine()
    for sf in (Decimal("3409"), Decimal("3430"), Decimal("3460")):
        cfg = _config(ozon_realized_k=sf / Decimal("5804"))
        _, code, _ = eng._ozon_rrp_closed_loop(
            SimpleNamespace(), cfg, _data(5804, sf), Decimal("3409"))
        assert code == "NO_CHANGE", f"витрина {sf} внутри зоны не должна менять цену"


def test_below_deadband_raises():
    # витрина ниже РРЦ -> поднять цену продавца (РРЦ это пол)
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
    """Эмуляция нескольких прогонов: цена сходится к цели сверху."""
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
    final_storefront = last * k_true
    assert target <= final_storefront <= target * Decimal("1.015")


def test_storefront_factor_corrects_index():
    """Поправка индекс->реальная витрина меняет оцениваемую витрину и цель."""
    without = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), _config(), _data(5804, Decimal("3600")), Decimal("3409"))
    assert without[1] == "CALCULATED"  # без поправки — снизить

    cfg = _config(ozon_storefront_factor=Decimal("0.95"))
    _, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _data(5804, Decimal("3600")), Decimal("3409"))
    # k_obs = 3600*0.95/5804 = 0.5893 -> est = 5804*0.5893 = 3420 ∈ коридор
    assert code == "NO_CHANGE"


# ---------------------------------------------------------------------------
# Путь «прямой замер»: узкая зона, цель — сама РРЦ
# ---------------------------------------------------------------------------

def test_measured_pushes_price_down_to_rrp():
    """Боевой случай «Альтернативы» на 2026-10-20.

    Цена продавца 3691 ₽, витрина по отчёту 2122 ₽, РРЦ 2100 ₽. По индексной
    зоне в 1.5% это «в коридоре» и товар навсегда стоял на 22 ₽ дороже РРЦ;
    по прямому замеру движок обязан снять цену продавца до 3653 ₽.
    """
    cfg = _config()
    price, code, msg = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(3691, Decimal("2122")), Decimal("2100"))
    assert code == "CALCULATED"
    assert price == 3653
    assert "витрина с сайта" in msg
    # k записан как есть, без EMA: замер точный
    assert cfg.ozon_realized_k == Decimal("0.5749")
    # и новая цена продавца не уводит витрину ниже РРЦ
    assert Decimal(price) * Decimal("2122") / Decimal("3691") >= Decimal("2100")


def test_measured_inside_narrow_band_no_change():
    cfg = _config()
    _, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(3691, Decimal("2104")), Decimal("2100"))
    assert code == "NO_CHANGE"


def test_measured_below_rrp_raises():
    cfg = _config()
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(3691, Decimal("2090")), Decimal("2100"))
    assert code == "CALCULATED"
    assert price > 3691


def test_stale_measurement_waits_instead_of_chasing():
    """Отчёт ещё от прежней цены — трогать цену нельзя, иначе раскачка."""
    cfg = _config()
    price, code, msg = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg,
        _measured(3691, Decimal("2300"), stale=True), Decimal("2100"))
    assert code == "NO_CHANGE"
    assert price is None
    assert "не пересчитал" in msg
    # k по отставшему замеру не записываем
    assert cfg.ozon_realized_k is None


def test_measured_ignores_storefront_factor():
    """Поправка нужна только индексу; у прямого замера поправлять нечего."""
    cfg = _config(ozon_storefront_factor=Decimal("0.90"))
    price, code, _ = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(3691, Decimal("2122")), Decimal("2100"))
    assert code == "CALCULATED"
    assert price == 3653


def test_measured_dry_run_does_not_persist_k():
    cfg = _config()
    _engine(dry_run=True)._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(3691, Decimal("2122")), Decimal("2100"))
    assert cfg.ozon_realized_k is None


def test_tiny_correction_is_not_worth_a_price_change():
    """Дешёвый товар: перебор рубль, правка меньше шага — не трогаем."""
    cfg = _config()
    price, code, msg = _engine()._ozon_rrp_closed_loop(
        SimpleNamespace(), cfg, _measured(500, Decimal("301")), Decimal("300"))
    assert code == "NO_CHANGE"
    assert price is None
    assert "меньше шага" in msg


def test_measured_converges_in_one_step():
    """При точной витрине цикл приходит к РРЦ за один шаг и там остаётся."""
    k_true = Decimal("0.5749")
    target = Decimal("2100")
    cfg = _config()
    eng = _engine()
    seller = Decimal("3691")
    for _ in range(5):
        storefront = (seller * k_true).quantize(Decimal("0.01"))
        price, code, _ = eng._ozon_rrp_closed_loop(
            SimpleNamespace(), cfg, _measured(seller, storefront), target)
        if code == "NO_CHANGE":
            break
        seller = Decimal(price)
    final_storefront = seller * k_true
    assert target <= final_storefront <= target * Decimal("1.003")
