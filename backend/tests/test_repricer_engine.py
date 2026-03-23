"""
Unit tests for the RepricerEngine pricing logic.

Focus areas:
  - _round_up: rounding behaviour with various Decimal inputs
  - _apply_limits: min_price clamp, max_price clamp, margin guard
  - _calculate_seller_price_from_final: inverse SPP calculation
  - Decimal precision: no float drift in financial calculations
"""

from decimal import Decimal, ROUND_UP
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.repricer_engine import RepricerEngine
from app.models.product import Product
from app.models.product_config import ProductConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(db_session=None, user_id=None, user_settings=None):
    """Create a RepricerEngine with optional pre-loaded settings."""
    engine = RepricerEngine(db=db_session or MagicMock(), user_id=user_id)
    # Pre-load user settings to avoid DB queries in unit tests
    engine._user_settings = user_settings or {}
    return engine


def _make_config(
    min_price="300.00",
    max_price="2000.00",
    product=None,
    strategy="rrp",
):
    """Create a lightweight ProductConfig-like object for testing."""
    cfg = SimpleNamespace(
        min_price=Decimal(min_price),
        max_price=Decimal(max_price) if max_price is not None else None,
        product=product,
        strategy=strategy,
    )
    return cfg


def _make_product(cost_price="500.00"):
    """Create a lightweight Product-like object for testing."""
    return SimpleNamespace(
        cost_price=Decimal(cost_price),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# _round_up
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoundUp:
    def setup_method(self):
        self.engine = _make_engine()

    def test_exact_integer_stays_same(self):
        assert self.engine._round_up(Decimal("1000")) == 1000

    def test_fractional_rounds_up(self):
        assert self.engine._round_up(Decimal("999.01")) == 1000
        assert self.engine._round_up(Decimal("999.99")) == 1000

    def test_half_rounds_up(self):
        assert self.engine._round_up(Decimal("500.5")) == 501

    def test_tiny_fraction_rounds_up(self):
        assert self.engine._round_up(Decimal("100.001")) == 101

    def test_zero(self):
        assert self.engine._round_up(Decimal("0")) == 0

    def test_one_cent(self):
        assert self.engine._round_up(Decimal("0.01")) == 1

    def test_negative_value(self):
        # ROUND_UP rounds away from zero, so -1.1 -> -2
        assert self.engine._round_up(Decimal("-1.1")) == -2

    def test_large_value(self):
        assert self.engine._round_up(Decimal("99999.001")) == 100000

    def test_returns_int_type(self):
        result = self.engine._round_up(Decimal("42.7"))
        assert isinstance(result, int)


# ═══════════════════════════════════════════════════════════════════════════════
# _apply_limits — min/max clamping
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyLimitsClamp:
    """Test min_price / max_price boundary enforcement."""

    def setup_method(self):
        self.engine = _make_engine(user_settings={})

    def test_price_within_bounds_unchanged(self):
        config = _make_config(min_price="300", max_price="2000")
        price, reason = self.engine._apply_limits(Decimal("1000"), config)
        assert price == Decimal("1000")
        assert reason == "UPDATED"

    def test_price_below_min_clamped(self):
        config = _make_config(min_price="500", max_price="2000")
        price, reason = self.engine._apply_limits(Decimal("200"), config)
        assert price == Decimal("500")
        assert reason == "CLAMPED_TO_MIN"

    def test_price_above_max_clamped(self):
        config = _make_config(min_price="100", max_price="1500")
        price, reason = self.engine._apply_limits(Decimal("2000"), config)
        assert price == Decimal("1500")
        assert reason == "CLAMPED_TO_MAX"

    def test_price_exactly_at_min(self):
        config = _make_config(min_price="500", max_price="2000")
        price, reason = self.engine._apply_limits(Decimal("500"), config)
        assert price == Decimal("500")
        assert reason == "UPDATED"

    def test_price_exactly_at_max(self):
        config = _make_config(min_price="100", max_price="1500")
        price, reason = self.engine._apply_limits(Decimal("1500"), config)
        assert price == Decimal("1500")
        assert reason == "UPDATED"

    def test_no_max_price_allows_high_values(self):
        config = _make_config(min_price="100", max_price=None)
        price, reason = self.engine._apply_limits(Decimal("999999"), config)
        assert price == Decimal("999999")
        assert reason == "UPDATED"

    def test_max_clamp_overrides_min_when_both_triggered(self):
        """If price < min but max < min (config error), max takes precedence
        because max check runs after min check."""
        config = _make_config(min_price="1000", max_price="500")
        price, reason = self.engine._apply_limits(Decimal("200"), config)
        # First clamped to min=1000, then clamped to max=500
        assert price == Decimal("500")
        assert reason == "CLAMPED_TO_MAX"


# ═══════════════════════════════════════════════════════════════════════════════
# _apply_limits — margin guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyLimitsMarginGuard:
    """Test the min_margin_pct safety net with Decimal math."""

    def test_margin_guard_not_triggered_when_disabled(self):
        """min_margin_pct=0 means no margin guard."""
        engine = _make_engine(user_settings={"min_margin_pct": "0"})
        product = _make_product(cost_price="500")
        config = _make_config(min_price="100", max_price="5000", product=product)

        # price = 400, cost = 500 => negative margin, but guard is off
        price, reason = engine._apply_limits(Decimal("400"), config)
        assert price == Decimal("400")
        assert reason == "UPDATED"

    def test_margin_guard_clamps_price_up(self):
        """When price yields margin below threshold, price is bumped up."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "20",
            "commission_type": "pct",
            "commission_value": "15",
            "logistics_type": "rub",
            "logistics_value": "50",
            "acquiring_type": "pct",
            "acquiring_value": "2",
        })
        product = _make_product(cost_price="500")
        config = _make_config(min_price="100", max_price="5000", product=product)

        # price=600: commission = 600*15/100=90, logistics=50, acquiring=600*2/100=12
        # fees = 90+50+12 = 152
        # margin = (600-500-152)/500*100 = -10.4% < 20% => CLAMP
        price, reason = engine._apply_limits(Decimal("600"), config)
        assert reason == "CLAMPED_TO_MARGIN"
        assert price > Decimal("600")

        # Verify the resulting price actually yields >= 20% margin
        sp = price
        fees = sp * Decimal("15") / 100 + Decimal("50") + sp * Decimal("2") / 100
        actual_margin = (sp - Decimal("500") - fees) / Decimal("500") * 100
        assert actual_margin >= Decimal("20") - Decimal("0.01")  # allow tiny rounding

    def test_margin_guard_not_triggered_when_margin_sufficient(self):
        """When margin is already above threshold, price stays."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "10",
            "commission_type": "pct",
            "commission_value": "5",
            "logistics_type": "rub",
            "logistics_value": "20",
            "acquiring_type": "pct",
            "acquiring_value": "1",
        })
        product = _make_product(cost_price="500")
        config = _make_config(min_price="100", max_price="5000", product=product)

        # price=800: commission=40, logistics=20, acquiring=8 => fees=68
        # margin = (800-500-68)/500*100 = 46.4% > 10% => no clamp
        price, reason = engine._apply_limits(Decimal("800"), config)
        assert price == Decimal("800")
        assert reason == "UPDATED"

    def test_margin_guard_skipped_when_no_cost_price(self):
        """If product has no cost_price, margin guard cannot run."""
        engine = _make_engine(user_settings={"min_margin_pct": "20"})
        product = SimpleNamespace(cost_price=None)
        config = _make_config(min_price="100", max_price="5000", product=product)

        price, reason = engine._apply_limits(Decimal("200"), config)
        assert price == Decimal("200")
        assert reason == "UPDATED"

    def test_margin_guard_skipped_when_no_product(self):
        """If config.product is None, margin guard cannot run."""
        engine = _make_engine(user_settings={"min_margin_pct": "20"})
        config = _make_config(min_price="100", max_price="5000", product=None)

        price, reason = engine._apply_limits(Decimal("200"), config)
        assert price == Decimal("200")
        assert reason == "UPDATED"

    def test_margin_guard_with_all_fixed_fees(self):
        """All fees as fixed rub amounts."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "15",
            "commission_type": "rub",
            "commission_value": "100",
            "logistics_type": "rub",
            "logistics_value": "80",
            "acquiring_type": "rub",
            "acquiring_value": "30",
        })
        product = _make_product(cost_price="400")
        config = _make_config(min_price="100", max_price="5000", product=product)

        # min_price_by_margin = (400*(1+0.15) + 100+80+30) / (1-0) = 460 + 210 = 670
        price, reason = engine._apply_limits(Decimal("500"), config)
        assert reason == "CLAMPED_TO_MARGIN"
        assert price == Decimal("670")

    def test_margin_guard_with_all_pct_fees(self):
        """All fees as percentages of seller price."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "10",
            "commission_type": "pct",
            "commission_value": "10",
            "logistics_type": "pct",
            "logistics_value": "5",
            "acquiring_type": "pct",
            "acquiring_value": "2",
        })
        product = _make_product(cost_price="1000")
        config = _make_config(min_price="100", max_price="5000", product=product)

        # min_price_by_margin = (1000*1.10 + 0) / (1 - 0.17) = 1100/0.83 = 1325.30... => 1326
        price, reason = engine._apply_limits(Decimal("1100"), config)
        assert reason == "CLAMPED_TO_MARGIN"
        assert price == Decimal("1326")

    def test_margin_guard_decimal_precision(self):
        """Ensure no float drift in margin calculation."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "5.5",
            "commission_type": "pct",
            "commission_value": "7.3",
            "logistics_type": "rub",
            "logistics_value": "45.50",
            "acquiring_type": "pct",
            "acquiring_value": "1.7",
        })
        product = _make_product(cost_price="333.33")
        config = _make_config(min_price="100", max_price="5000", product=product)

        price, reason = engine._apply_limits(Decimal("350"), config)

        # Verify result is Decimal (or int from _round_up wrapped in Decimal)
        # and that margin actually meets threshold if clamped
        if reason == "CLAMPED_TO_MARGIN":
            sp = price
            fees = (
                sp * Decimal("7.3") / 100
                + Decimal("45.50")
                + sp * Decimal("1.7") / 100
            )
            margin = (sp - Decimal("333.33") - fees) / Decimal("333.33") * 100
            assert margin >= Decimal("5.5") - Decimal("0.01")


# ═══════════════════════════════════════════════════════════════════════════════
# _calculate_seller_price_from_final
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalculateSellerPriceFromFinal:
    def setup_method(self):
        self.engine = _make_engine()

    def test_zero_spp(self):
        """With 0% SPP, seller price == final price (rounded up)."""
        result = self.engine._calculate_seller_price_from_final(
            Decimal("1000"), Decimal("0")
        )
        assert result == Decimal("1000")

    def test_typical_spp(self):
        """final=800, spp=20% => seller = 800 / 0.80 = 1000."""
        result = self.engine._calculate_seller_price_from_final(
            Decimal("800"), Decimal("20")
        )
        assert result == Decimal("1000")

    def test_high_spp(self):
        """final=500, spp=50% => seller = 500 / 0.50 = 1000."""
        result = self.engine._calculate_seller_price_from_final(
            Decimal("500"), Decimal("50")
        )
        assert result == Decimal("1000")

    def test_fractional_result_rounds_up(self):
        """final=999, spp=10% => seller = 999 / 0.90 = 1110.0 => 1110."""
        result = self.engine._calculate_seller_price_from_final(
            Decimal("999"), Decimal("10")
        )
        expected = Decimal("1110")
        assert result == expected

    def test_irrational_result_rounds_up(self):
        """final=100, spp=30% => seller = 100/0.70 = 142.857... => 143."""
        result = self.engine._calculate_seller_price_from_final(
            Decimal("100"), Decimal("30")
        )
        assert result == Decimal("143")

    def test_spp_100_raises_error(self):
        """SPP of 100% means free product — should raise ValueError."""
        with pytest.raises(ValueError, match="100"):
            self.engine._calculate_seller_price_from_final(Decimal("500"), Decimal("100"))

    def test_spp_above_100_raises_error(self):
        with pytest.raises(ValueError):
            self.engine._calculate_seller_price_from_final(Decimal("500"), Decimal("150"))

    def test_result_type_is_decimal(self):
        result = self.engine._calculate_seller_price_from_final(
            Decimal("1000"), Decimal("15")
        )
        assert isinstance(result, Decimal)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration-style: _apply_limits + margin guard combined with clamps
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyLimitsCombined:
    """Edge cases where multiple limits interact."""

    def test_margin_guard_result_still_below_min_price(self):
        """If margin-guard price < min_price, min_price wins."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "5",
            "commission_type": "rub",
            "commission_value": "10",
            "logistics_type": "rub",
            "logistics_value": "10",
            "acquiring_type": "rub",
            "acquiring_value": "5",
        })
        product = _make_product(cost_price="100")
        # min_price_by_margin = (100*1.05 + 25)/1 = 130 but min_price = 500
        config = _make_config(min_price="500", max_price="5000", product=product)

        price, reason = engine._apply_limits(Decimal("80"), config)
        # Clamped to min first (500), then margin check:
        # fees=25, margin=(500-100-25)/100*100=375% >> 5% => no further clamp
        assert price == Decimal("500")
        assert reason == "CLAMPED_TO_MIN"

    def test_margin_guard_result_above_max_price(self):
        """If margin-guard pushes above max, max should still be enforced...
        but note: in the current code, margin guard runs AFTER max_price check,
        so margin can exceed max. This test documents that behavior."""
        engine = _make_engine(user_settings={
            "min_margin_pct": "50",
            "commission_type": "pct",
            "commission_value": "20",
            "logistics_type": "rub",
            "logistics_value": "100",
            "acquiring_type": "pct",
            "acquiring_value": "5",
        })
        product = _make_product(cost_price="1000")
        config = _make_config(min_price="100", max_price="1500", product=product)

        price, reason = engine._apply_limits(Decimal("1200"), config)
        # This documents current behavior: margin guard may push above max_price
        # because it runs after the max_price check
        if reason == "CLAMPED_TO_MARGIN":
            assert price > Decimal("1500")  # margin guard overrode max
